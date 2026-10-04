#!/usr/bin/env python3
"""Isolated faulty copies: never modifies the correct RTL or checker."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
source = ROOT / 'src/project.v'
original = source.read_text()
faults = {
    'short-wait': ('wait_left <= argument;', "wait_left <= (argument == 0 ? 0 : argument - 1'b1);"),
    'msb-first': ('pin_value <= data_shift[0];\n                                   data_shift <= {1\'b0, data_shift[7:1]};',
                  'pin_value <= data_shift[7];\n                                   data_shift <= {data_shift[6:0], 1\'b0};'),
}
results = []
for name, (before, after) in faults.items():
    assert original.count(before) == 1
    folder = ROOT / 'results' / (name + '-work')
    folder.mkdir(parents=True, exist_ok=True)
    faulty = folder / 'project.v'
    faulty.write_text(original.replace(before, after))
    with (ROOT / 'results' / (name + '.log')).open('w') as log:
        run = subprocess.run([sys.executable, str(ROOT / 'scripts/run.py'), 'mutation',
                              '--name', name, '--source', str(faulty)], stdout=log, stderr=subprocess.STDOUT)
    evidence = json.loads((ROOT / 'results' / (name + '.json')).read_text())
    caught = run.returncode != 0 and evidence['failures'] == 1 and evidence['simulated_clock_cycles'] > 30
    caught = caught and any(message in (ROOT / 'results' / (name + '.log')).read_text() for message in ('busy/completion at edge', 'UART byte='))
    results.append({'fault': name, 'detected': caught, 'faulty_rtl_sha256': evidence['rtl_sha256'],
                    'evidence': name + '.json'})
assert source.read_text() == original
(ROOT / 'results/mutations.json').write_text(json.dumps({
    'correct_rtl_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'correct_source_unchanged': True, 'faults': results}, indent=2) + '\n')
if not all(r['detected'] for r in results):
    raise SystemExit('Mutation not detected by public waveform/completion checker')
print('Both isolated RTL faults detected; correct RTL unchanged.')
