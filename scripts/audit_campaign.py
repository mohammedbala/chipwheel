#!/usr/bin/env python3
"""Audit committed campaign ranges against the frozen deterministic scenarios."""
import argparse
import ast
import collections
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import time
from urllib.parse import quote

from campaign import atomic, read, safe_dir, sha, verify_frozen, rank, allocation


class AuditError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise AuditError(message)


def frozen_scenarios(folder):
    """Load only the pure generator and its constants; never start a simulator."""
    source = folder / 'frozen/test/campaign_cases.py'
    parsed = ast.parse(source.read_text(), filename=str(source))
    namespace = {'hashlib': hashlib, 'random': random}
    generator = None
    for node in parsed.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ('CATEGORIES', 'CONTROL', 'BOUNDARY'):
                    namespace[target.id] = ast.literal_eval(node.value)
        elif isinstance(node, ast.FunctionDef) and node.name == 'scenario_spec':
            generator = node
    require(generator is not None, 'Frozen scenario generator is missing')
    exec(compile(ast.Module(body=[generator], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['scenario_spec']


def audit_rows(manifest, rows, scenario_spec):
    offsets = collections.defaultdict(int)
    stage_counts = collections.Counter()
    candidate_counts = collections.Counter()
    eliminated = set()
    passed_total = failed_total = 0
    for stage, cid, start, count, passed, failed, raw in rows:
        label = f'{stage}/{cid}/{start}'
        require(stage in manifest['stage_budgets'] and cid in manifest['candidates'], label + ': unknown stage or candidate')
        require(all(type(x) is int for x in (start, count, passed, failed)) and start >= 0 and passed >= 0, label + ': invalid integer counts')
        require(cid not in eliminated, label + ': checks credited after disqualification')
        require(start == offsets[stage, cid], label + ': non-contiguous or duplicate case range')
        require(0 < count <= manifest['batch_limit'], label + ': invalid batch size')
        require(passed + failed == count and failed in (0, 1), label + ': inconsistent evaluation totals')
        report = json.loads(raw)
        require(report.get('final') is True, label + ': incomplete report credited')
        for field, expected in [('checked', count), ('passed', passed), ('failed', failed),
                                ('stage', stage), ('candidate', cid), ('start', start),
                                ('campaign_revision', manifest['revision']),
                                ('rtl_sha256', manifest['candidates'][cid]['rtl_sha256'])]:
            require(report.get(field) == expected, label + ': mismatched ' + field)
        failure = report.get('failure')
        require(bool(failure) == bool(failed), label + ': missing or spurious failure evidence')
        if failed:
            require(failure.get('case_id') == start + count - 1, label + ': worker continued beyond failing case')
            require(failure.get('seed') == manifest['seed'] and failure.get('stage') == stage, label + ': failure replay provenance differs')
            eliminated.add(cid)
        digest = hashlib.sha256()
        coverage = collections.Counter()
        for case_id in range(start, start + count):
            scenario = scenario_spec(manifest['seed'], stage, case_id)
            keys = [scenario['category']]
            if scenario['category'] == 'uart':
                keys += ['byte:' + str(scenario['byte']), 'duration:' + str(scenario['duration'])]
            elif scenario['category'] in ('control', 'boundary'):
                category = scenario['category']
                keys += [category + ':' + scenario[category]]
            coverage.update(keys)
            case_passed = not (failed and case_id == start + count - 1)
            digest.update(json.dumps([case_id, scenario, case_passed], sort_keys=True).encode())
            if not case_passed:
                require(failure.get('scenario') == scenario, label + ': failing scenario differs from frozen generator')
        require(report.get('digest') == digest.hexdigest(), label + ': scenario digest differs')
        require(report.get('coverage') == dict(coverage), label + ': coverage differs from checked case range')
        offsets[stage, cid] += count
        stage_counts[stage] += count
        candidate_counts[cid] += count
        passed_total += passed
        failed_total += failed
    for stage, count in stage_counts.items():
        require(count <= manifest['stage_budgets'][stage], stage + ': budget exceeded')
    return dict(checked=passed_total + failed_total, passed=passed_total, failed=failed_total,
                batches=len(rows), by_stage=dict(stage_counts), by_candidate=dict(candidate_counts),
                eliminated=sorted(eliminated), case_ranges={f'{s}/{c}': n for (s, c), n in offsets.items()})


def audit(folder, require_complete=False):
    manifest = read(folder / 'manifest.json')
    verify_frozen(folder, manifest)
    # A read transaction gives one consistent snapshot while the worker commits.
    uri = 'file:' + quote(str(folder / 'ledger.sqlite'), safe='/') + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        db.execute('BEGIN')
        require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'SQLite integrity check failed')
        rows = db.execute('SELECT stage,candidate,start,count,passed,failed,report FROM batches ORDER BY rowid').fetchall()
    result = audit_rows(manifest, rows, frozen_scenarios(folder))
    for cid in result['by_candidate']:
        qualification = read(folder / 'candidates' / cid / 'qualification.json')
        require(qualification.get('status') == 'passed', cid + ': bulk checks lack successful qualification')
        require(qualification.get('rtl_sha256') == manifest['candidates'][cid]['rtl_sha256'], cid + ': stale qualification')
        directed = qualification.get('report', {})
        require(directed.get('final') and directed.get('checked', 0) > 0 and not directed.get('failed') and not directed.get('paused'), cid + ': directed checks did not finish')
        require(directed.get('rtl_sha256') == manifest['candidates'][cid]['rtl_sha256'], cid + ': directed report uses another design')
        mutations = qualification.get('mutations', [])
        require({x.get('fault') for x in mutations} == {'short-wait', 'msb-first'} and len(mutations) == 2, cid + ': required mutations missing')
        require(all(x.get('detected') and x.get('report', {}).get('final') is True
                    and x['report'].get('checked') == 1 and x['report'].get('failed') == 1
                    and x['report'].get('passed') == 0 for x in mutations), cid + ': fault escaped checking or mutation report is incomplete')
    target = sum(manifest['stage_budgets'].values())
    complete = all(result['by_stage'].get(stage, 0) == budget for stage, budget in manifest['stage_budgets'].items())
    if require_complete:
        require(complete, f"Campaign budget is incomplete: {result['checked']:,} / {target:,}")
    if complete:
        survivors = [c for c in manifest['candidates'] if c not in result['eliminated']
                     and read(folder / 'candidates' / c / 'qualification.json').get('status') == 'passed']
        ranking, basis = rank(folder, survivors)
        require(bool(ranking), 'Completed campaign has no valid hardware measurements')
        finalists = ranking[:2]
        for stage in ('stress', 'holdout'):
            counts = {key.split('/')[1]: n for key, n in result['case_ranges'].items() if key.startswith(stage + '/')}
            quotas = allocation(finalists, manifest['stage_budgets'][stage], counts)
            require(all(counts.get(c, 0) == q for c, q in quotas.items()), stage + ': current finalists did not receive required checks')
        result.update(finalists=finalists, ranking_basis=basis, physical_validation_complete=basis == 'physical')
    result.update(status='passed', campaign=manifest['id'], revision=manifest['revision'],
                  target=target, budget_complete=complete, audited_at=time.time(),
                  auditor_sha256=sha(__file__), frozen_inputs_verified=True,
                  note='Audit validates recorded scenarios and provenance; RTL replays remain separate.')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('id')
    parser.add_argument('--require-complete', action='store_true')
    args = parser.parse_args()
    folder = safe_dir(args.id)
    try:
        result = audit(folder, args.require_complete)
    except (AuditError, OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'failed', 'campaign': args.id, 'reason': str(exc)}, indent=2))
        raise SystemExit(2)
    atomic(folder / 'ledger-audit.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
