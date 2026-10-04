"""Random differential testing: reference model vs RTL (or gate-level) netlist.

Each case: reset, load random program + config, then thousands of cycles of
random host strobes (any command/data/tag mix, random widths), random HSEL,
random input-pin activity and occasional resets. Every output bit of every
cycle must match. Inputs follow the replay convention (applied just after the
falling edge), so arbitrary, protocol-violating stimulus is still well defined.

    python3 fuzz.py --cases 200 --cycles 4000 --seed 1
"""
import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'model'))
sys.path.insert(0, str(HERE))
from bench import Bench, HSTB, HTAG, HCMD, HSEL  # noqa: E402
import replay  # noqa: E402


class RandomPins:
    """Device driving undriven pins with random slowly-toggling levels."""

    def __init__(self, rng, rate):
        self.rng, self.rate = rng, rate
        self.level = [1, 1, 1, 1]
        self.drive = {}

    def observe(self, lines, cycle):
        pass

    def update(self, cycle):
        for i in range(4):
            if self.rng.random() < self.rate:
                self.level[i] ^= 1
        self.drive = {i: self.level[i] for i in range(4)}


class ChipAwarePins(RandomPins):
    """Only drives pins the chip is not driving (avoids contention)."""

    def __init__(self, rng, rate, bench_ref):
        super().__init__(rng, rate)
        self.bench_ref = bench_ref

    def update(self, cycle):
        super().update(cycle)
        b = self.bench_ref[0]
        uo, uout, oe, care = b.out
        # open-drain chip pins: device may still pull low (wired-AND)
        self.drive = {i: self.level[i] for i in range(4) if not (oe >> i) & 1 or self.level[i] == 0
                      and not ((uout >> i) & 1)}


def random_program(rng):
    # bias towards useful instructions but allow every encoding
    words = []
    for _ in range(10):
        r = rng.random()
        if r < 0.15:
            words.append(rng.randrange(256))
        elif r < 0.3:
            words.append(0x40 | rng.randrange(64))                         # SET
        elif r < 0.55:
            words.append(0x80 | (rng.randrange(16) << 2) | rng.randrange(4))  # SHIFT
        elif r < 0.7:
            words.append(rng.randrange(64))                                # BR
        elif r < 0.8:
            words.append(0xD0 | rng.randrange(16))                         # WAIT
        elif r < 0.9:
            words.append(0xE0 | rng.randrange(8))                          # MISC
        else:
            words.append(0xC0 | rng.randrange(16) if rng.random() < 0.5 else 0xF0 | rng.randrange(16))
    return words


def random_config(rng):
    div = rng.choice([0, 0, 0, 1, 1, 2, 3, rng.randrange(256)])
    return [div, rng.randrange(256), rng.randrange(256)]


def run_case(seed, cycles, vvp=None, coverage=None):
    rng = random.Random(seed)
    ref = [None]
    pins = ChipAwarePins(rng, rng.choice([0.01, 0.05, 0.2]), ref)
    b = Bench([pins])
    ref[0] = b
    b.reset()
    b.load(random_program(rng), random_config(rng))
    b.command(0x20 if rng.random() < 0.5 else 0x70)
    b.command(rng.randrange(16))                      # RUN at a random entry
    end = b.cycle + cycles
    while b.cycle < end:
        r = rng.random()
        if r < 0.02:                                  # command strobe
            cmd = rng.choice([rng.randrange(16), 0x10, 0x20, 0x30, 0x40, 0x50, 0x70, rng.randrange(256)])
            b.ui = cmd
            b.ctl = (b.ctl & HSEL) | HCMD | (HTAG if rng.random() < 0.1 else 0)
            b.step(rng.randrange(1, 3))
            b.ctl |= HSTB
            b.step(rng.randrange(1, 5))
            b.ctl &= ~HSTB
            b.step(rng.randrange(0, 4))
        elif r < 0.06:                                # data strobe (handshake or abandoned)
            b.ui = rng.randrange(256)
            b.ctl = (b.ctl & HSEL) | (HTAG if rng.random() < 0.5 else 0)
            b.step()
            b.ctl |= HSTB
            for _ in range(rng.randrange(1, 60)):
                b.step()
                if b.out[0] & 2 and not b.ctl & HSEL:
                    break
            b.ctl &= ~HSTB
            b.step(rng.randrange(0, 5))
        elif r < 0.08:
            b.ctl ^= HSEL
            b.step()
        elif r < 0.081:                               # mid-run reset, then reprogram
            b.reset()
            b.load(random_program(rng), random_config(rng))
            b.command(rng.randrange(16))
        elif r < 0.085:                               # fully random control nibble for a few cycles
            for _ in range(rng.randrange(1, 6)):
                b.ui = rng.randrange(256)
                b.ctl = rng.randrange(16) << 4
                b.step()
            b.ctl &= HSEL
        else:
            b.step(rng.randrange(1, 20))
        if coverage is not None:
            m = b.model
            if m.running and m.pc < 10 and m.mem[m.pc] is not None:
                coverage[f'op{m.mem[m.pc] >> 6}'] += 1
    if coverage is not None:
        coverage['cycles'] += b.cycle
    if vvp is None:
        return b, None
    cyc, err, log = replay.replay(b, vvp)
    return b, (cyc, err, log)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cases', type=int, default=50)
    ap.add_argument('--cycles', type=int, default=3000)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--gl', help='gate-level netlist to replay instead of RTL')
    ap.add_argument('--out', help='write a JSON summary here')
    a = ap.parse_args()
    if a.gl:
        vvp = replay.build([a.gl] + replay.GL_MODELS, defines=('GL_TEST', 'FUNCTIONAL'), tag='gl')
    else:
        vvp = replay.build()
    cov = Counter()
    t0 = time.time()
    total = 0
    for k in range(a.cases):
        seed = a.seed * 1000003 + k
        b, (cyc, err, log) = run_case(seed, a.cycles, vvp, cov)
        total += cyc
        if err:
            print(f'FAIL seed={seed}\n{log}')
            sys.exit(1)
    dt = time.time() - t0
    summary = {'cases': a.cases, 'cycles': total, 'seconds': round(dt, 2), 'seed': a.seed,
               'target': 'gl' if a.gl else 'rtl', 'coverage': dict(cov), 'failures': 0}
    print(json.dumps(summary))
    if a.out:
        Path(a.out).write_text(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
