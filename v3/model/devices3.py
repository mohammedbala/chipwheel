"""Independent pin-level devices and line-code reference functions for v3 tests.

Written from the protocol definitions, not from the v3 model or RTL: they only
see pad values and produce/consume plain per-cycle waveforms.
"""
from bench3 import Device


class Recorder(Device):
    """Records all 8 line values every cycle."""

    def __init__(self):
        super().__init__()
        self.trace = []

    def observe(self, lines, cycle):
        self.trace.append(lines)

    def pin(self, i, start=0):
        return [(v >> i) & 1 for v in self.trace[start:]]


class Waveform(Device):
    """Drives pins from a precomputed per-cycle waveform {pin: [levels]} starting at `start`."""

    def __init__(self, waves, start=0, idle=None):
        super().__init__()
        self.waves, self.start = waves, start
        self.idle = idle or {p: w[0] for p, w in waves.items()}
        self.drive = dict(self.idle)

    def update(self, cycle):
        k = cycle - self.start
        self.drive = {p: (w[k] if 0 <= k < len(w) else (w[-1] if k >= len(w) else self.idle[p]))
                      for p, w in self.waves.items()}


# ---------------------------------------------------------------- line codes
def crc16_usb(bits):
    """USB CRC16 over data bits in wire order; returns the 16 bits to append (wire order)."""
    crc = 0xFFFF
    for b in bits:
        fb = (crc & 1) ^ b
        crc >>= 1
        if fb:
            crc ^= 0xA001
    crc ^= 0xFFFF
    return [(crc >> i) & 1 for i in range(16)]


def usb_crc16_ok(bits):
    """Residue check: data+CRC bits in wire order leave the USB residue 0xB001 (reflected)."""
    crc = 0xFFFF
    for b in bits:
        fb = (crc & 1) ^ b
        crc >>= 1
        if fb:
            crc ^= 0xA001
    return crc == 0xB001


def bytes_lsb(bs):
    return [(b >> i) & 1 for b in bs for i in range(8)]


def bits_to_bytes_lsb(bits):
    return [sum(bits[8 * k + i] << i for i in range(8)) for k in range(len(bits) // 8)]


def stuff(bits, n, ones_only):
    """Insert the complement after n identical bits (or a 0 after n ones)."""
    out, run, last = [], 0, None
    for b in bits:
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        if run == n and (not ones_only or b == 1):
            sb = 0 if ones_only else 1 - b
            out.append(sb)
            run, last = 1, sb
    return out


def destuff(bits, n, ones_only):
    out, run, last, i = [], 0, None, 0
    errors = 0
    while i < len(bits):
        b = bits[i]
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        i += 1
        if run == n and (not ones_only or b == 1) and i < len(bits):
            sb = bits[i]
            if sb != (0 if ones_only else 1 - b):
                errors += 1
            run, last = 1, sb
            i += 1
    return out, errors


def nrzi_encode(bits, start_level):
    """USB NRZI: a 0 toggles the line, a 1 keeps it."""
    lvl, out = start_level, []
    for b in bits:
        if b == 0:
            lvl ^= 1
        out.append(lvl)
    return out


def nrzi_decode(levels, start_level):
    prev, out = start_level, []
    for lv in levels:
        out.append(1 if lv == prev else 0)
        prev = lv
    return out


def manchester_encode(bits):
    """IEEE 802.3: 0 = high then low, 1 = low then high; returns half-bit levels."""
    out = []
    for b in bits:
        out += [1 - b, b]
    return out


def sample_bits(trace, start, period, n, phase=None):
    """Sample a per-cycle trace at bit centres."""
    phase = period // 2 if phase is None else phase
    return [trace[start + k * period + phase] for k in range(n)]


def crc15_can(bits):
    crc = 0
    for b in bits:
        fb = ((crc >> 14) & 1) ^ b
        crc = (crc << 1) & 0x7FFF
        if fb:
            crc ^= 0x4599
    return crc


class SpiMaster(Device):
    """Drives SCK/MOSI/CS like a mode-0 SPI master with `half` cycles per SCK half."""

    def __init__(self, data, sck=1, mosi=0, cs=3, miso=2, half=6, start=100):
        super().__init__()
        self.sck, self.mosi, self.cs, self.miso = sck, mosi, cs, miso
        self.wave = []          # (cs, sck, mosi) per cycle
        self.wave += [(1, 0, 0)] * start
        self.wave += [(0, 0, 0)] * half
        for byte in data:
            for i in range(7, -1, -1):
                b = (byte >> i) & 1
                self.wave += [(0, 0, b)] * half + [(0, 1, b)] * half
        self.wave += [(0, 0, 0)] * half + [(1, 0, 0)] * 20
        self.sampled = []       # MISO at each rising edge
        self._prev_sck = 0
        self.drive = {cs: 1, sck: 0, mosi: 0}

    def observe(self, lines, cycle):
        sck = (lines >> self.sck) & 1
        if sck and not self._prev_sck:
            self.sampled.append((lines >> self.miso) & 1)
        self._prev_sck = sck

    def update(self, cycle):
        cs, sck, mosi = self.wave[cycle] if cycle < len(self.wave) else (1, 0, 0)
        self.drive = {self.cs: cs, self.sck: sck, self.mosi: mosi}


class OneWireSlave(Device):
    """1-Wire slave on an open-drain line: answers a reset pulse with a presence pulse."""

    def __init__(self, pin=0, cpu=50, present=True):
        super().__init__()
        self.pin, self.present = pin, present
        self.low_since = None
        self.reset_min = 400 * cpu // 1000 * 1000 // 1000   # ~400 us scaled: cycles per us = cpu
        self.reset_min = 400 * cpu
        self.wait = 30 * cpu
        self.width = 120 * cpu
        self.sched = None       # (start, end) of our presence pulse
        self.resets = 0
        self.drive = {}

    def observe(self, lines, cycle):
        lv = (lines >> self.pin) & 1
        mine = self.sched and self.sched[0] <= cycle < self.sched[1]
        if lv == 0 and not mine:
            if self.low_since is None:
                self.low_since = cycle
        elif lv == 1 and self.low_since is not None:
            if cycle - self.low_since >= self.reset_min and self.present:
                self.resets += 1
                self.sched = (cycle + self.wait, cycle + self.wait + self.width)
            self.low_since = None

    def update(self, cycle):
        self.drive = {self.pin: 0} if self.sched and self.sched[0] <= cycle < self.sched[1] else {}
