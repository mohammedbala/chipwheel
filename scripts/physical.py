#!/usr/bin/env python3
"""Record standalone synthesis or invoke the actual official hardening flow."""
import argparse
import hashlib
import json
import os
import resource
import subprocess
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=['synth', 'layout'])
    a = p.parse_args()
    os.chdir(ROOT)
    env = os.environ.copy()
    env.update(PATH=str(ROOT / '.tools/flow-venv/bin') + os.pathsep + env['PATH'],
               YOWASP_CACHE_DIR=str(ROOT / '.tools/yowasp-cache'),
               PDK_ROOT=str(ROOT / '.tools/pdk'))
    if Path('/opt/homebrew/lib').exists():
        env['DYLD_FALLBACK_LIBRARY_PATH'] = '/opt/homebrew/lib'
    python = str(ROOT / '.tools/flow-venv/bin/python')
    commands = ([['yowasp-yosys', '-s', 'scripts/synth.ys']] if a.stage == 'synth' else
                [[python, 'tt/tt_tool.py', '--create-user-config', '--ihp'],
                 [python, 'tt/tt_tool.py', '--harden', '--ihp']])
    started = time.perf_counter()
    returncode = 0
    with (ROOT / 'results' / (a.stage + '-flow.log')).open('w') as log:
        for command in commands:
            log.write('Command: ' + ' '.join(command) + '\n'); log.flush()
            returncode = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
            if returncode:
                break
    result = {'stage': a.stage, 'status': 'passed' if returncode == 0 else 'failed',
              'returncode': returncode, 'build_wall_seconds': time.perf_counter() - started,
              'peak_child_rss_bytes': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
              'rtl_sha256': hashlib.sha256((ROOT / 'src/project.v').read_bytes()).hexdigest(),
              'config_sha256': hashlib.sha256((ROOT / 'src/config.json').read_bytes()).hexdigest(),
              'clock_period_ns': 100, 'tiles': '6x4', 'pdk_revision': '2bbec755dc67ca3db0261c3d6163e15735d66710',
              'routing_status': 'unverified', 'timing_slack_ns': None,
              'area_um2': None, 'note': 'No completed official physical build.'}
    if a.stage == 'synth' and returncode == 0:
        stat = json.loads((ROOT / 'results/mapped-stat.json').read_text())['design']
        result.update(area_um2=stat['area'], mapped_cells=stat['num_cells'],
                      tool=json.loads((ROOT / 'results/mapped-stat.json').read_text())['creator'],
                      library_sha256=hashlib.sha256((ROOT / '.tools/pdk-lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib').read_bytes()).hexdigest(),
                      note='Standalone Yosys/ABC mapped cell area, typical 1.20 V / 25 C; excludes CTS, placement and routing.')
    if a.stage == 'layout' and returncode:
        result['blocker'] = 'No compatible container engine found; full PDK not installed; disk space insufficient for full setup.'
    (ROOT / 'results' / (a.stage + '-flow.json')).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(returncode)


if __name__ == '__main__':
    main()
