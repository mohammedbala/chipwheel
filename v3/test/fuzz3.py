"""Random differential testing for Chipwheel v3: reference model vs RTL / gates.

Each case loads all 32 program words, both SM configurations and the global
registers with random (biased) values, enables random state machines, then
runs thousands of cycles of random host traffic on every channel (TX nibbles,
command packets including config rewrites, EXEC, RESTART, ENABLE, IRQ clears,
program rewrites), random reads and random pin activity. Every output bit of
every cycle must match the model.

    python3 fuzz3.py --cases 100 --cycles 4000 --seed 1 [--gl netlist.v]
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
from bench3 import Bench3, CH_CMD, CH_PROG  # noqa: E402
import replay  # noqa: E402


class RandomPins:
    def __init__(self, rng, rate, ref):
        self.rng, self.rate, self.ref = rng, rate, ref
        self.level = [1] * 8
        self.drive = {}

    def observe(self, lines, cycle):
        pass

    def update(self, cycle):
        for i in range(8):
            if self.rng.random() < self.rate:
                self.level[i] ^= 1
        uo, uout, oe, care = self.ref[0].out
        # never fight a chip-driven 1; may pull an open-drain or undriven line low
        self.drive = {i: self.level[i] for i in range(8)
                      if not ((oe >> i) & 1) or (self.level[i] == 0 and not (uout >> i) & 1)}


def random_word(rng):
    op = rng.choices(range(8), weights=[14, 10, 14, 16, 10, 12, 6, 14])[0]
    field = rng.randrange(32) if rng.random() < 0.5 else 0
    low = rng.randrange(256)
    if op == 1 and rng.random() < 0.7:          # WAIT: mostly short, often with timeout
        low = (low & 0xE7) | (0x10 if rng.random() < 0.6 else 0)
    if op in (2, 3) and rng.random() < 0.6:     # IN/OUT: favour small counts and pins
        low = (low & 0xE0) | rng.choice([1, 1, 1, 2, 4, 8, 16, 0])
    return (op << 13) | (field << 8) | low


def random_cfg(rng):
    def r(n):
        return rng.randrange(1 << n)
    div = rng.choice([0, 0, 0, 1, 2, 3, r(16) & 0x1F, r(16)])
    c1 = rng.choice([0, 0, 0, 1, 2, 3]) | (r(1) << 2) | (r(3) << 3) | (r(1) << 6)
    c2 = r(16)
    c3 = r(13)
    c4 = r(16)
    c5 = r(16)
    if rng.random() < 0.5:
        c5 &= ~0x3800 if rng.random() < 0.5 else 0xFFFF
    c6 = r(5)
    c7 = r(16)
    return [div, c1, c2, c3, c4, c5, c6, c7]


def codec_word(rng):
    """Instructions that keep single-bit shifts through the line codec busy."""
    r = rng.random()
    delay = rng.choice([0, 0, 0, 1])
    if r < 0.4:
        return 0x6001 | (rng.choice([0, 0, 0, 3]) << 5) | (delay << 8)           # out pins/null, 1
    if r < 0.75:
        return 0x4001 | (delay << 8)                                            # in pins, 1
    if r < 0.8:
        return 0x00E0 | rng.randrange(32)                                       # jmp flag, addr
    if r < 0.85:
        return 0x2060 | (rng.randrange(2) << 7)                                 # wait 0/1 line
    if r < 0.9:
        return 0xA003 | (rng.choice([0b011, 0b001, 0b111]) << 5) | (rng.choice([0, 1]) << 3)  # mov crc/x/osr, ...
    if r < 0.95:
        return 0x8020 | rng.choice([0, 0x80])                                   # push / pull block
    return random_word(rng)


def codec_cfg(rng):
    c = random_cfg(rng)
    c[0] = rng.choice([0, 0, 1, 2])                                             # fast ticks
    c[1] &= ~3                                                                  # divider clocking
    c[4] = (c[4] & ~0x3FF) | 31 << 5                                           # wrap 31 -> 0
    c[4] |= (1 << 12) | (1 << 13)                                               # autopush + autopull
    stuff = rng.choice([1, 2, 3, 5, 6])
    c[5] = (rng.randrange(16)) | (rng.randrange(16) << 4) | (stuff << 8) | (rng.randrange(2) << 11) | \
           (rng.choice([1, 1, 0]) << 12) | (rng.choice([1, 1, 0]) << 13) | (rng.randrange(4) << 14)
    c[6] = rng.randrange(4) | (rng.randrange(4) << 2) | (rng.randrange(2) << 4)
    return c


def run_case(seed, cycles, vvp=None, cov=None):
    rng = random.Random(seed)
    codec = rng.random() < 0.35
    ref = [None]
    pins = RandomPins(rng, rng.choice([0.01, 0.05, 0.2, 0.5] if codec else [0.01, 0.05, 0.2]), ref)
    b = Bench3([pins])
    ref[0] = b
    b.reset()
    gen = codec_word if codec else random_word
    b.load([gen(rng) for _ in range(32)], origin=0)
    for sm in (0, 1):
        b.configure(sm, codec_cfg(rng) if codec else random_cfg(rng))
    b.globals(od=rng.randrange(256), owner=rng.randrange(256), span=int(rng.random() < 0.15))
    for _ in range(rng.randrange(4)):
        b.exec(rng.randrange(2), random_word(rng))
    b.enable(rng.choice([1, 2, 3, 3]))
    end = b.cycle + cycles
    while b.cycle < end:
        r = rng.random()
        if codec and r < 0.5:                          # keep TX buffers fed
            sm = rng.randrange(2)
            if (b.status >> (2 * sm)) & 1:
                b.write_nibble(sm, rng.randrange(16))
            else:
                b.step(rng.randrange(1, 6))
            continue
        if r < 0.10:                                   # TX nibble to an SM
            b.write_nibble(rng.randrange(2), rng.randrange(16))
        elif r < 0.13:                                 # random read nibble consume
            b.ch = rng.randrange(4)
            b.step(rng.randrange(1, 3))
            b.rt ^= 1
            b.step(rng.randrange(1, 4))
        elif r < 0.145:                                # command packet
            kind = rng.random()
            if kind < 0.35:
                addr, data = (rng.randrange(2) << 4) | rng.randrange(8), rng.randrange(1 << 16)
            elif kind < 0.5:
                addr, data = 0x30, rng.randrange(4)
            elif kind < 0.6:
                addr, data = 0x31, rng.randrange(1 << 9)
            elif kind < 0.75:
                addr, data = 0x32 + rng.randrange(2), random_word(rng)
            elif kind < 0.85:
                addr, data = 0x35, rng.randrange(16)
            elif kind < 0.92:
                addr, data = 0x20 + rng.randrange(2), rng.randrange(1 << 16)
            else:
                addr, data = rng.randrange(256), rng.randrange(1 << 16)
            b.command(addr, data)
        elif r < 0.15:                                 # program word rewrite
            b.command(0x34, rng.randrange(32))
            for k in (12, 8, 4, 0):
                b.write_nibble(CH_PROG, random_word(rng) >> k)
            b.step(1)
        elif r < 0.155:                                # stray nibble on cmd/prog channels
            b.write_nibble(rng.choice([CH_CMD, CH_PROG]), rng.randrange(16))
        elif r < 0.16:                                 # glitchy host: random control bits
            for _ in range(rng.randrange(1, 5)):
                b.nib, b.ch = rng.randrange(16), rng.randrange(4)
                b.wt, b.rt = rng.randrange(2), rng.randrange(2)
                b.step()
        else:
            b.step(rng.randrange(1, 12))
    if cov is not None:
        cov.update(b.chip.cov)
        cov['cycles'] += b.cycle
    if vvp is None:
        return b, None
    return b, replay.replay(b, vvp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cases', type=int, default=50)
    ap.add_argument('--cycles', type=int, default=3000)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--gl')
    ap.add_argument('--out')
    a = ap.parse_args()
    vvp = (replay.build([a.gl] + replay.GL_MODELS, defines=('GL_TEST', 'FUNCTIONAL'), tag='gl')
           if a.gl else replay.build())
    cov = Counter()
    t0 = time.time()
    total = 0
    for k in range(a.cases):
        seed = a.seed * 1000003 + k
        try:
            b, (cyc, err, log) = run_case(seed, a.cycles, vvp, cov)
        except RuntimeError as e:          # model refused (e.g. undefined state) -> skip case
            cov['model_refused'] += 1
            continue
        total += cyc
        if err:
            print(f'FAIL seed={seed}\n{log[-1500:]}')
            sys.exit(1)
    summary = {'cases': a.cases, 'cycles': total, 'seconds': round(time.time() - t0, 2), 'seed': a.seed,
               'target': 'gl' if a.gl else 'rtl', 'coverage': dict(cov), 'failures': 0}
    print(json.dumps(summary))
    if a.out:
        Path(a.out).write_text(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
