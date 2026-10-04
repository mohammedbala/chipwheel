#!/usr/bin/env python3
"""Reproducible runner with separate compile and simulation measurements."""
import argparse
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path
from cocotb_tools.runner import get_runner

ROOT = Path(__file__).resolve().parents[1]


def content_hash():
    digest = hashlib.sha256()
    files = [ROOT / 'info.yaml', ROOT / 'src/config.json']
    for folder in ('src', 'programs', 'test', 'scripts', 'docs'):
        files += sorted(p for p in (ROOT / folder).glob('*') if p.is_file() and p.suffix in ('.py', '.v', '.md', '.txt', '.yaml'))
    for p in files:
        digest.update(str(p.relative_to(ROOT)).encode() + b'\0' + p.read_bytes())
    return digest.hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['short', 'campaign', 'mutation'], nargs='?', default='short')
    p.add_argument('--seed', type=int, default=20261003)
    p.add_argument('--transactions', type=int, default=2000)
    p.add_argument('--waves', action='store_true')
    p.add_argument('--name')
    p.add_argument('--source', type=Path, default=ROOT / 'src/project.v')
    p.add_argument('--cell-model', type=Path)
    a = p.parse_args()
    if a.transactions <= 0:
        p.error('transactions must be positive')
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / 'test'))
    sys.path.insert(0, str(ROOT))
    local_bin = ROOT / '.tools/icarus-verilog/13.0/bin'
    if local_bin.exists():
        os.environ['PATH'] = str(local_bin) + os.pathsep + os.environ['PATH']
    name = a.name or a.mode
    if not name.replace('-', '').replace('_', '').isalnum():
        p.error('name must contain only letters, digits, hyphens or underscores')
    output = ROOT / 'results' / (name + '-work')
    output.mkdir(parents=True, exist_ok=True)
    metrics_file = output / 'metrics.json'
    metrics_file.unlink(missing_ok=True)
    runner = get_runner('icarus')
    build_digest = hashlib.sha256(a.source.read_bytes() + (ROOT / 'test/tb.v').read_bytes())
    if a.cell_model:
        build_digest.update(a.cell_model.read_bytes())
    build = ROOT / 'test/sim_build/bench' / build_digest.hexdigest()[:12]
    started = time.perf_counter()
    sources = [a.source.resolve(), ROOT / 'test/tb.v']
    if a.cell_model:
        sources.insert(0, a.cell_model.resolve())
    runner.build(sources=sources, hdl_toplevel='tb',
                 build_dir=build, always=False, timescale=('1ns', '1ps'), build_args=['-g2012', '-DFUNCTIONAL', '-DSIM'])
    build_time = time.perf_counter() - started
    started = time.perf_counter()
    error = None
    try:
        runner.test(hdl_toplevel='tb', test_module='test', test_dir=output,
                    extra_env={'PYTHONPATH': str(ROOT / 'test') + os.pathsep + str(ROOT),
                               'CW_MODE': a.mode, 'CW_SEED': str(a.seed),
                               'CW_TRANSACTIONS': str(a.transactions),
                               'CW_METRICS': str(metrics_file),
                               'CW_DEMO': str(ROOT / 'results/demo-A.json')},
                    seed=a.seed, waves=a.waves, plusargs=['+waves'] if a.waves else [],
                    results_xml=str(output / 'results.xml'))
    except BaseException as exc:
        error = str(exc)
    process_time = time.perf_counter() - started
    # cocotb runner can complete with failed tests; XML is the final authority.
    import xml.etree.ElementTree as ET
    xml = output / 'results.xml'
    xml_failed = not xml.exists() or bool(ET.parse(xml).findall('.//failure')) or bool(ET.parse(xml).findall('.//error'))
    metrics = json.loads(metrics_file.read_text()) if metrics_file.exists() else {
        'completed_transactions': 0, 'checked_transactions': 0, 'failures': 1,
        'timeouts': 0, 'simulated_clock_cycles': None, 'simulation_wall_seconds': None}
    metrics.update({'status': 'failed' if error or xml_failed else 'passed',
                    'source_content_sha256': content_hash(),
                    'rtl_sha256': hashlib.sha256(a.source.read_bytes()).hexdigest(),
                    'source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                    'revision_note': 'worktree content hash identifies local changes',
                    'mode': a.mode, 'seed': a.seed,
                    'requested_random_transactions': a.transactions if a.mode == 'campaign' else 0,
                    'build_wall_seconds': build_time, 'simulation_process_wall_seconds': process_time,
                    'peak_child_rss_bytes': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                    'peak_rss_note': 'macOS bytes; process-family maximum including compile, not simulator-only',
                    'cell_model_sha256': hashlib.sha256(a.cell_model.read_bytes()).hexdigest() if a.cell_model else None,
                    'configuration': {'clock_hz': 10000000, 'words': 32, 'word_bits': 16, 'waves': a.waves},
                    'tools': {'python': platform.python_version(), 'cocotb': __import__('cocotb').__version__,
                              'iverilog': subprocess.check_output(['iverilog', '-V'], text=True, stderr=subprocess.DEVNULL).splitlines()[0]},
                    'host': platform.platform(), 'runner_error': error})
    destination = ROOT / 'results' / (name + '.json')
    destination.write_text(json.dumps(metrics, indent=2) + '\n')
    print(json.dumps(metrics, indent=2))
    return 1 if error or xml_failed else 0


if __name__ == '__main__':
    sys.exit(main())
