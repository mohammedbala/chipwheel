"""Cycle bench for Chipwheel v3: nibble host bus driver, 8-line resolution,
independent devices (reused from v2/model/bench.py), and recording of every
cycle's inputs and model outputs for replay on RTL / gate-level netlists.

A device may set `drive_late` (a dict like `drive`) to give the line a
different value for the falling-edge half of the clock (DDR sampling)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'v2' / 'model'))
from bench import (LineRecorder, UartSource, SpiSlave, I2cSlave, Device,  # noqa: E402,F401
                   uart_decode, Timeout)
from cw3 import Chip  # noqa: E402

CH_TX0, CH_TX1, CH_CMD, CH_PROG = 0, 1, 2, 3


def config3(div=0, clksrc=0, resync=0, clk_pin=0, in_fast=0, in_ddr=0,
            out_base=0, out_count=0, set_base=0, set_count=0, side_base=0,
            in_base=0, jmp_pin=0, side_count=0, side_en=0, side_pindir=0, jflag=0, status_sel=0,
            wrap_bottom=0, wrap_top=31, in_left=0, out_left=0, autopush=0, autopull=0,
            fifo16=0, diff=0, push_thresh=0, pull_thresh=0, stuff_n=0, stuff_ones=0,
            line_tx=0, line_rx=0, enc=0, dec=0, crc_out=0, crc_in=0, crc_reflect=0, poly=0, prog=None):
    """Eight configuration register values; `prog` (asm.Program) supplies wrap and side-set."""
    if prog is not None:
        wrap_bottom, wrap_top = prog.wrap_target, prog.wrap
        side_count, side_en, side_pindir = prog.side_count, prog.side_en, prog.side_pindir
    return [div & 0xFFFF,
            (clksrc & 3) | (resync << 2) | ((clk_pin & 7) << 3) | (in_fast << 6) | (in_ddr << 7),
            (out_base & 7) | ((out_count & 15) << 3) | ((set_base & 7) << 7) | ((set_count & 7) << 10) |
            ((side_base & 7) << 13),
            (in_base & 7) | ((jmp_pin & 7) << 3) | ((side_count & 3) << 6) | (side_en << 8) |
            (side_pindir << 9) | ((jflag & 3) << 10) | (status_sel << 12),
            (wrap_bottom & 31) | ((wrap_top & 31) << 5) | (in_left << 10) | (out_left << 11) |
            (autopush << 12) | (autopull << 13) | (fifo16 << 14) | (diff << 15),
            (push_thresh & 15) | ((pull_thresh & 15) << 4) | ((stuff_n & 7) << 8) | (stuff_ones << 11) |
            (line_tx << 12) | (line_rx << 13) | ((enc & 3) << 14),
            (dec & 3) | (crc_out << 2) | (crc_in << 3) | (crc_reflect << 4),
            poly & 0xFFFF]


class Bench3:
    def __init__(self, devices=(), pullups=0xFF):
        self.chip = Chip()
        self.devices = list(devices)
        self.pullups = pullups
        self.stim, self.expect = [], []
        self.nib = 0
        self.ch = 0
        self.wt = 0
        self.rt = 0
        self.rst_n = 0
        self.out = (0, 0, 0, 0)
        self.lines = pullups
        self.cycle = 0

    @property
    def ui(self):
        return (self.rt << 7) | ((self.ch & 3) << 5) | (self.wt << 4) | (self.nib & 15)

    def _resolve(self, late=False):
        uo, uout, oe, care = self.out
        lines = 0
        for i in range(8):
            chip = (uout >> i) & 1 if (oe >> i) & 1 else None
            devs = [d for d in ((dv.drive_late if late and getattr(dv, 'drive_late', None) is not None
                                 else dv.drive).get(i) for dv in self.devices) if d is not None]
            if chip == 1 and 0 in devs:
                raise AssertionError(f'cycle {self.cycle}: G{i} contention (chip 1, device 0)')
            vals = devs + ([chip] if chip is not None else [])
            lines |= (min(vals) if vals else (self.pullups >> i) & 1) << i
        return lines

    def step(self, n=1):
        for _ in range(n):
            self.lines = self._resolve()
            for d in self.devices:
                d.observe(self.lines, self.cycle)
            if any(getattr(d, 'same_cycle', False) for d in self.devices):
                self.lines = self._resolve()
            ui = self.ui
            late = (self._resolve(late=True)
                    if any(getattr(d, 'drive_late', None) is not None for d in self.devices) else self.lines)
            self.stim.append((late << 17) | (self.rst_n << 16) | (ui << 8) | self.lines)
            self.out = self.chip.edge(ui, self.lines, self.rst_n, late)
            uo, uout, oe, care = self.out
            self.expect.append((uo << 24) | (uout << 16) | (oe << 8) | care)
            self.cycle += 1
            for d in self.devices:
                d.update(self.cycle)

    @property
    def status(self):
        return self.out[0] >> 4

    def wait_until(self, cond, limit=200000, what='condition'):
        for _ in range(limit):
            if cond():
                return
            self.step()
        raise Timeout(f'cycle {self.cycle}: timeout waiting for {what}')

    # ------------------------------------------------------------ host bus
    def reset(self):
        self.rst_n = 0
        self.step(2)
        self.rst_n = 1
        self.step(1)

    def write_nibble(self, ch, nib):
        self.ch, self.nib = ch, nib & 15
        self.wt ^= 1
        self.step(3)                       # event lands 2 clocks later; hold through it

    def write_entry(self, sm, value, nibbles=2):
        for k in range(nibbles):
            self.write_nibble(sm, value >> (4 * k))

    def command(self, addr, data):
        for k in (4, 0):
            self.write_nibble(CH_CMD, addr >> k)
        for k in (12, 8, 4, 0):
            self.write_nibble(CH_CMD, data >> k)
        self.step(1)                       # command executes one clock after its last nibble

    def load(self, program, origin=None):
        words = program.words if hasattr(program, 'words') else program
        org = program.origin if origin is None and hasattr(program, 'origin') else (origin or 0)
        self.command(0x34, org)
        for w in words:
            for k in (12, 8, 4, 0):
                self.write_nibble(CH_PROG, w >> k)
            self.step(1)

    def configure(self, sm, regs):
        for i, v in enumerate(regs):
            self.command((sm << 4) | i, v)

    def globals(self, od=0, owner=0, span=0, link01=0, link10=0):
        self.command(0x20, (od & 0xFF) | ((owner & 0xFF) << 8))
        self.command(0x21, (span & 1) | ((link01 & 1) << 1) | ((link10 & 1) << 2))

    def enable(self, mask):
        self.command(0x30, mask & 3)

    def restart(self, sm, pc):
        self.command(0x31, (sm << 8) | (pc & 31))

    def exec(self, sm, instr):
        self.command(0x32 + sm, instr)

    def clear_irq(self, mask):
        self.command(0x35, mask)

    def send(self, sm, value, nibbles=2, limit=200000):
        self.wait_until(lambda: (self.status >> (2 * sm)) & 1, limit, f'SM{sm} TX ready')
        self.write_entry(sm, value, nibbles)

    def read(self, sm, nibbles=2, limit=200000):
        self.wait_until(lambda: (self.status >> (2 * sm + 1)) & 1, limit, f'SM{sm} RX valid')
        self.ch = sm
        v = 0
        for k in range(nibbles):
            self.step(1)
            v |= (self.out[0] & 15) << (4 * k)
            self.rt ^= 1
            self.step(3)
        return v
