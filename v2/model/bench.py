"""Cycle bench for Chipwheel v2: host driver, line resolution and devices.

The bench co-simulates the reference model with a scripted host and
independent pin-level devices (UART line monitor, SPI slave, I2C slave). It
records every cycle's inputs and the model's outputs so the identical stimulus
can be replayed on RTL or gate-level netlists (v2/test/replay.py), which must
reproduce every output bit. Devices only see pad values, never model state.
"""
from cw2 import Model, MEM_WORDS, NINS

HSTB, HTAG, HCMD, HSEL = 1 << 4, 1 << 5, 1 << 6, 1 << 7


class Timeout(AssertionError):
    pass


class Bench:
    def __init__(self, devices=(), pullups=0b1111):
        self.model = Model()
        self.devices = list(devices)
        self.pullups = pullups
        self.stim, self.expect = [], []
        self.ui = 0
        self.ctl = 0          # host bits uio[7:4]
        self.rst_n = 0
        self.out = (0, 0, 0, 0)
        self.lines = pullups & 15
        self.cycle = 0
        self.drive = {}       # device drives effective this cycle: pin -> 0/1

    # ------------------------------------------------------------ core step
    def _resolve(self):
        uo, uout, oe, care = self.out
        lines = 0
        for i in range(4):
            chip = (uout >> i) & 1 if (oe >> i) & 1 else None
            devs = [d for d in (dv.drive.get(i) for dv in self.devices) if d is not None]
            if chip is not None and chip == 1 and 0 in devs:
                raise AssertionError(f'cycle {self.cycle}: P{i} contention (chip drives 1, device 0)')
            vals = devs + ([chip] if chip is not None else [])
            v = min(vals) if vals else (self.pullups >> i) & 1
            lines |= v << i
        return lines

    def step(self, n=1):
        for _ in range(n):
            self.lines = self._resolve()
            for d in self.devices:
                d.observe(self.lines, self.cycle)
            if any(getattr(d, 'same_cycle', False) for d in self.devices):
                self.lines = self._resolve()     # fast devices react within the cycle
            uio = (self.ctl & 0xF0) | self.lines
            self.stim.append((self.rst_n << 16) | (self.ui << 8) | uio)
            self.out = self.model.edge(self.ui, uio, self.rst_n)
            uo, uout, oe, care = self.out
            self.expect.append((uo << 24) | (uout << 16) | (oe << 8) | care)
            self.cycle += 1
            for d in self.devices:
                d.update(self.cycle)

    @property
    def status(self):
        return self.out[0] if not self.ctl & HSEL else None

    def wait_until(self, cond, limit=100000, what='condition'):
        for _ in range(limit):
            if cond():
                return
            self.step()
        raise Timeout(f'cycle {self.cycle}: timeout waiting for {what}')

    # ------------------------------------------------------------ host ops
    def reset(self):
        self.rst_n = 0
        self.ui, self.ctl = 0, 0
        self.step(2)
        self.rst_n = 1
        self.step(1)

    def _strobe(self, d, ctl):
        self.ui = d
        self.ctl = ctl
        self.step()
        self.ctl = ctl | HSTB
        self.step(2)
        self.ctl = ctl
        self.step(3)          # hold D/HCMD/HTAG 3 clocks after HSTB falls

    def command(self, d):
        self._strobe(d, HCMD)

    def write_mem(self, words, start_ptr_reset=True):
        if start_ptr_reset:
            self.command(0x30)
        for w in words:
            assert 0 <= w <= 255
            self._strobe(w, HCMD | HTAG)

    def load(self, program, cfg):
        words = list(program) + [0xE4] * (NINS - len(program))   # pad with NOP
        assert len(words) == NINS and len(cfg) == 3
        self.write_mem(words + list(cfg))

    def run(self, entry=0):
        self.command(entry & 15)

    def stop(self):
        self.command(0x10)

    def idle(self):
        self.command(0x20)

    def send(self, byte, tag=0, limit=100000):
        """4-phase data handshake; returns when the engine consumed the byte."""
        self.ui = byte & 255
        self.ctl = (self.ctl & HSEL) | (HTAG if tag else 0)
        self.step()
        self.ctl |= HSTB
        self.wait_until(lambda: self.out[0] & 2, limit, 'TXACK')
        self.ctl &= ~HSTB
        self.wait_until(lambda: not self.out[0] & 2, limit, 'TXACK clear')
        self.step(2)          # hold time (TXACK clears 2 clocks after HSTB fall)

    def rx_ready(self):
        return bool(self.out[0] & 4)

    def read_rx(self, limit=100000, ack=True):
        self.wait_until(lambda: self.out[0] & 4, limit, 'RXV')
        self.ctl |= HSEL
        self.step()
        byte = self.out[0]
        self.ctl &= ~HSEL
        if ack:
            self.command(0x70)    # no-op command: strobe event clears RXV
        return byte

    def wait_halt(self, limit=100000):
        self.wait_until(lambda: not self.out[0] & 1, limit, 'halt')


# ---------------------------------------------------------------- devices
class Device:
    def __init__(self):
        self.drive = {}

    def observe(self, lines, cycle):
        pass

    def update(self, cycle):
        pass


class LineRecorder(Device):
    """Records one pin's line value every cycle."""

    def __init__(self, pin):
        super().__init__()
        self.pin = pin
        self.trace = []

    def observe(self, lines, cycle):
        self.trace.append((lines >> self.pin) & 1)


def uart_decode(trace, bit, nbytes=None, start=0):
    """Strict 8N1 decode of a per-cycle line trace with exactly `bit` cycles/bit.

    Every bit cell must be constant for its full duration and the stop bit must
    be high for at least one full cell. Returns (bytes, frame_start_cycles).
    """
    out, starts = [], []
    i = start
    n = len(trace)
    while i < n and (nbytes is None or len(out) < nbytes):
        if trace[i] == 1:
            i += 1
            continue
        t0 = i
        if t0 + 10 * bit > n:
            raise AssertionError(f'truncated frame at cycle {t0}')
        cells = [trace[t0 + k * bit: t0 + (k + 1) * bit] for k in range(10)]
        for k, c in enumerate(cells):
            if len(set(c)) != 1:
                raise AssertionError(f'frame at {t0}: bit cell {k} not constant: {c}')
        if cells[0][0] != 0 or cells[9][0] != 1:
            raise AssertionError(f'frame at {t0}: bad start/stop')
        out.append(sum(cells[1 + k][0] << k for k in range(8)))
        starts.append(t0)
        i = t0 + 9 * bit + bit       # end of stop cell
    return out, starts


class UartSource(Device):
    """Drives a UART line (8N1, LSB first) with `bit` cycles per bit."""

    def __init__(self, pin, data, bit, gap=0, start=0):
        super().__init__()
        self.pin = pin
        self.wave = []
        t = 0
        self.wave += [1] * start
        for b in data:
            self.wave += [0] * bit
            for k in range(8):
                self.wave += [(b >> k) & 1] * bit
            self.wave += [1] * (bit + gap)
        self.drive = {pin: 1}

    def update(self, cycle):
        self.drive = {self.pin: self.wave[cycle] if cycle < len(self.wave) else 1}


class SpiSlave(Device):
    """SPI slave, any mode. Samples MOSI on the sampling edge, shifts MISO on
    the other edge (MISO changes one cycle after the shifting edge is seen)."""

    def __init__(self, sck=1, mosi=0, miso=2, cs=3, mode=0, msb=True, replies=(), same_cycle=False):
        super().__init__()
        self.same_cycle = same_cycle      # MISO valid before the next rising edge
        self.sck, self.mosi, self.miso, self.cs = sck, mosi, miso, cs
        self.cpol, self.cpha = mode >> 1, mode & 1
        self.msb = msb
        self.replies = list(replies)
        self.received = []
        self.prev_sck = self.cpol
        self.prev_cs = 1
        self.bits = 0
        self.nbit = 0
        self.tx = 0
        self.tx_left = 0
        self.miso_val = 1
        self.pending = None
        self.drive = {}

    def _load(self):
        self.tx = self.replies.pop(0) if self.replies else 0xFF
        self.tx_left = 8

    def _next_out(self):
        if self.tx_left == 0:
            self._load()
        b = (self.tx >> 7) & 1 if self.msb else self.tx & 1
        self.tx = ((self.tx << 1) & 0xFF) if self.msb else (self.tx >> 1)
        self.tx_left -= 1
        return b

    def observe(self, lines, cycle):
        sck = (lines >> self.sck) & 1
        cs = (lines >> self.cs) & 1
        mosi = (lines >> self.mosi) & 1
        if self.prev_cs and not cs:              # select
            self.nbit, self.bits = 0, 0
            self.tx_left = 0
            if not self.cpha:
                self.pending = self._next_out()
        if not cs and sck != self.prev_sck:
            leading = sck != self.cpol
            sample_edge = leading if not self.cpha else not leading
            if sample_edge:
                self.bits = ((self.bits << 1) | mosi) if self.msb else (self.bits | (mosi << self.nbit))
                self.nbit += 1
                if self.nbit == 8:
                    self.received.append(self.bits & 0xFF)
                    self.nbit, self.bits = 0, 0
            else:
                self.pending = self._next_out()
        self.prev_sck, self.prev_cs = sck, cs
        if self.same_cycle:
            self.update(cycle)

    def update(self, cycle):
        if self.pending is not None:
            self.miso_val = self.pending
            self.pending = None
        self.drive = {self.miso: self.miso_val}


class I2cSlave(Device):
    """Open-drain I2C slave with a register file, optional clock stretching.

    Write: [addr<<1|0] [reg] [data...]; read: [addr<<1|1] -> data from reg.
    Stretches SCL low for `stretch` cycles after each SCL falling edge.
    """

    def __init__(self, addr=0x50, sda=0, scl=1, stretch=0, regs=None):
        super().__init__()
        self.addr, self.sda, self.scl, self.stretch = addr, sda, scl, stretch
        self.regs = dict(regs or {})
        self.log = []          # ('start'|'stop'|'byte', value, acked)
        self.prev = (1, 1)
        self.state = 'idle'
        self.bits = self.nbit = 0
        self.ptr = None
        self.reading = False
        self.sda_low = False
        self.scl_hold = 0
        self.tx = 0
        self.drive = {}
        self.first_data = True

    def observe(self, lines, cycle):
        sda = (lines >> self.sda) & 1
        scl = (lines >> self.scl) & 1
        psda, pscl = self.prev
        if pscl and scl and psda and not sda:
            self.log.append(('start', None, None))
            self.state, self.nbit, self.bits = 'addr', 0, 0
            self.sda_low = False
        elif pscl and scl and not psda and sda:
            self.log.append(('stop', None, None))
            self.state = 'idle'
            self.sda_low = False
        elif not pscl and scl and self.state in ('addr', 'wdata', 'ack', 'rdata', 'mack'):
            if self.state in ('addr', 'wdata'):
                self.bits = (self.bits << 1) | sda
                self.nbit += 1
            elif self.state == 'mack':
                self.master_ack = sda == 0
        elif pscl and not scl:
            if self.stretch:
                self.scl_hold = self.stretch
            self._falling()
        self.prev = (sda, scl)

    def _falling(self):
        if self.state in ('addr', 'wdata') and self.nbit == 8:
            b = self.bits
            if self.state == 'addr':
                ok = (b >> 1) == self.addr
                self.log.append(('byte', b, ok))
                if ok:
                    self.reading = bool(b & 1)
                    self.first_data = True
                    self.sda_low = True
                    self.state = 'ack'
                else:
                    self.state = 'idle'
            else:
                if self.first_data:
                    self.ptr = b
                    self.first_data = False
                else:
                    self.regs[self.ptr] = b
                    self.ptr = (self.ptr + 1) & 0xFF
                self.log.append(('byte', b, True))
                self.sda_low = True
                self.state = 'ack'
            self.nbit, self.bits = 0, 0
        elif self.state == 'ack':               # end of our ACK clock
            self.sda_low = False
            if self.reading:
                self.state = 'rdata'
                self.tx = self.regs.get(self.ptr, 0xFF)
                self.log.append(('byte', self.tx, None))
                self.nbit = 0
                self._drive_tx()
            else:
                self.state = 'wdata'
        elif self.state == 'rdata':
            if self.nbit == 8:
                self.sda_low = False
                self.state = 'mack'
            else:
                self._drive_tx()
        elif self.state == 'mack':
            self.ptr = (self.ptr + 1) & 0xFF if self.ptr is not None else None
            if getattr(self, 'master_ack', False):
                self.state = 'rdata'
                self.tx = self.regs.get(self.ptr, 0xFF)
                self.log.append(('byte', self.tx, None))
                self.nbit = 0
                self._drive_tx()
            else:
                self.state = 'idle'
                self.sda_low = False

    def _drive_tx(self):
        bit = (self.tx >> (7 - self.nbit)) & 1
        self.sda_low = bit == 0
        self.nbit += 1

    def update(self, cycle):
        d = {}
        if self.sda_low:
            d[self.sda] = 0
        if self.scl_hold > 0:
            d[self.scl] = 0
            self.scl_hold -= 1
        self.drive = d
