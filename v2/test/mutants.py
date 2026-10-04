"""Mutation check: every injected RTL fault must be caught by the directed
suite or a short random differential run. Faults live in copies under
v2/build/; the real RTL is never edited. Writes v2/results/mutants.json.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RTL = HERE.parents[1] / 'src/project.v'
BUILD = HERE.parent / 'build'
PY = sys.executable

MUTANTS = {
    'msb_shift_drops_input': ("shl = m ? {v[7:0], b} : {b, v[8:1]};", "shl = m ? {v[7:0], 1'b0} : {b, v[8:1]};"),
    'lsb_shift_wraps': ("shl = m ? {v[7:0], b} : {b, v[8:1]};", "shl = m ? {v[7:0], b} : {v[0], v[8:1]};"),
    'no_clock_stretch': ("wire stretched = rel & od[1] & ~pb[1];", "wire stretched = 1'b0;"),
    'wrap_ignored': ("wire [3:0] next_pc = (pc == wrap_top) ? 4'd0 : pc + 4'd1;", "wire [3:0] next_pc = pc + 4'd1;"),
    'single_sync_wait': ("pa <= uio_in[3:0]; pb <= pa;", "pa <= uio_in[3:0]; pb <= uio_in[3:0];"),
    'overrun_never': ("                if (rxv & ~strobe) ovr <= 1;\n", ""),
    'delay_one_short': ("if (busy ? sub == 4'd1 : ins[2:0] == 3'd0) begin", "if (busy ? sub == 4'd2 : ins[2:0] == 3'd0) begin"),
    'pull_without_ack': ("            if (!s2) txack <= 0;", "            txack <= 0;"),
    'od_drives_high': ("    assign uio_out = {4'b0, out & ~od};", "    assign uio_out = {4'b0, out};"),
    'push_wrong_half': ("rxd <= pushhi ? sr[7:0] : sr[8:1];", "rxd <= sr[7:0];"),
    'brx_no_decrement': ("if (ins[5:4] == 2'd1 && x != 0) x <= x - 4'd1;", ""),
    'cpol_ignored_ph3': ("wire cpol = ~ph3 & idle[1];", "wire cpol = idle[1];"),
}


def run(env, args):
    r = subprocess.run([PY] + args, cwd=HERE, env=env, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)[-300:]


def main():
    src = RTL.read_text()
    BUILD.mkdir(exist_ok=True)
    results = {}
    for name, (a, b) in MUTANTS.items():
        assert a in src, f'{name}: pattern not found'
        path = BUILD / f'mutant_{name}.v'
        path.write_text(src.replace(a, b, 1))
        env = dict(os.environ, CW2_RTL=str(path))
        rc, _ = run(env, ['-m', 'pytest', '-q', '-x', 'test_protocols.py', '-p', 'no:cacheprovider'])
        caught_by = 'directed' if rc else None
        if not caught_by:
            rc, _ = run(env, ['fuzz.py', '--cases', '150', '--cycles', '3000', '--seed', '7'])
            caught_by = 'fuzz' if rc else None
        results[name] = caught_by
        print(f'{name:24s} {caught_by or "NOT CAUGHT"}')
    out = HERE.parent / 'results/mutants.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({'rtl_sha_note': 'mutants of v2/src/chipwheel2.v', 'results': results}, indent=1))
    sys.exit(0 if all(results.values()) else 1)


if __name__ == '__main__':
    main()
