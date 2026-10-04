"""Replay Chipwheel v3 bench traces on RTL (or a gate-level netlist) with Icarus."""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
V3 = ROOT / 'v3'
IVERILOG = ROOT / '.tools/icarus-verilog/13.0/bin/iverilog'
VVP = ROOT / '.tools/icarus-verilog/13.0/bin/vvp'
BUILD = V3 / 'build'
CELLS = ROOT / 'test/cells_sim.v'     # behavioral models of the instantiated cells
RTL = [Path(os.environ['CW3_RTL']) if os.environ.get('CW3_RTL') else V3 / 'src/chipwheel3.v', CELLS]
GL_MODELS = [ROOT / '.tools/pdk-lib/sg13cmos5l_functional.v', ROOT / '.tools/pdk-lib/sg13cmos5l_udp.v']


def build(sources=None, defines=(), tag='rtl'):
    sources = [Path(s) for s in (sources or RTL)]
    tb = V3 / 'test/tb_replay.v'
    h = hashlib.sha256()
    for f in sources + [tb]:
        h.update(f.read_bytes())
    h.update(repr(defines).encode())
    BUILD.mkdir(exist_ok=True)
    out = BUILD / f'{tag}-{h.hexdigest()[:16]}.vvp'
    if not out.exists():
        cmd = [str(IVERILOG), '-g2012', '-o', str(out), '-s', 'tb_replay']
        cmd += [f'-D{d}' for d in defines] + [str(tb)] + [str(s) for s in sources]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f'iverilog failed:\n{r.stdout}\n{r.stderr}')
    return out


def replay(bench, vvp=None, waves=False, keep=None):
    """Replay a finished Bench on the compiled design; returns (cycles, errors, log)."""
    vvp = vvp or build()
    with tempfile.TemporaryDirectory(dir=BUILD) as td:
        td = Path(keep) if keep else Path(td)
        td.mkdir(parents=True, exist_ok=True)
        (td / 'stim.hex').write_text(''.join(f'{x:05x}\n' for x in bench.stim))
        (td / 'exp.hex').write_text(''.join(f'{x:08x}\n' for x in bench.expect))
        cmd = [str(VVP), '-n', str(vvp), f'+stim={td}/stim.hex', f'+exp={td}/exp.hex']
        if waves:
            cmd.append('+waves')
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=td)
    log = r.stdout + r.stderr
    for line in log.splitlines():
        if line.startswith('REPLAY'):
            parts = dict(kv.split('=') for kv in line.split()[1:])
            return int(parts['cycles']), int(parts['errors']), log
    raise RuntimeError(f'replay produced no summary:\n{log}')
