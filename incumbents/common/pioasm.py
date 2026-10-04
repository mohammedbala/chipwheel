"""Tiny RP2040-PIO instruction encoder (only what the incumbent programs need).

Encodings follow the RP2040 datasheet section 3.4. Side-set/delay packing
follows lawrie/fpga_pio's decoder: with `.side_set N [opt]` the upstream core is
configured with pins_side_count = N + opt (the opt enable bit counts as a
side-set bit, as in upstream sim/uart_tx.v), so the 5-bit field instr[12:8] is
[opt-enable][side value(s)][delay].
"""


class Pio:
    def __init__(self, side_bits=0, side_opt=False):
        self.side_bits = side_bits
        self.side_opt = side_opt
        self.field_side = side_bits + (1 if side_opt else 0)  # bits taken from the 5-bit field
        self.delay_bits = 5 - self.field_side

    def _pack(self, base, side=None, delay=0):
        assert 0 <= delay < (1 << self.delay_bits), 'delay too large'
        f = delay
        if self.side_bits:
            if side is None:
                assert self.side_opt, 'side-set is mandatory without opt'
            else:
                f |= side << self.delay_bits
                if self.side_opt:
                    f |= 1 << 4
        else:
            assert side is None
        return base | (f << 8)

    # --- instructions -------------------------------------------------
    def jmp(self, addr, cond='always', **k):
        c = {'always': 0, '!x': 1, 'x--': 2, '!y': 3, 'y--': 4, 'x!=y': 5, 'pin': 6, '!osre': 7}[cond]
        return self._pack((0 << 13) | (c << 5) | addr, **k)

    def out(self, dest, n, **k):
        d = {'pins': 0, 'x': 1, 'y': 2, 'null': 3, 'pindirs': 4, 'pc': 5, 'isr': 6, 'exec': 7}[dest]
        return self._pack((3 << 13) | (d << 5) | (n & 31), **k)

    def in_(self, src, n, **k):
        s = {'pins': 0, 'x': 1, 'y': 2, 'null': 3, 'isr': 6, 'osr': 7}[src]
        return self._pack((2 << 13) | (s << 5) | (n & 31), **k)

    def push(self, block=True, iffull=False, **k):
        return self._pack((4 << 13) | (iffull << 6) | (block << 5), **k)

    def pull(self, block=True, ifempty=False, **k):
        return self._pack((4 << 13) | (1 << 7) | (ifempty << 6) | (block << 5), **k)

    def set(self, dest, val, **k):
        d = {'pins': 0, 'x': 1, 'y': 2, 'pindirs': 4}[dest]
        return self._pack((7 << 13) | (d << 5) | (val & 31), **k)

    def nop(self, **k):  # mov y, y
        return self._pack((5 << 13) | (2 << 5) | 2, **k)


# --- upstream fpga_pio host-side configuration words -------------------
def grps(out_base=0, set_base=0, side_base=0, in_base=0, out_count=0, set_count=0, side_count=0):
    return (out_base | (set_base << 5) | (side_base << 10) | (in_base << 15) |
            (out_count << 20) | (set_count << 26) | (side_count << 29))


def pend(wrap_top, wrap_target=0, sideset_enable_bit=0):
    return (wrap_target << 7) | (wrap_top << 12) | (sideset_enable_bit << 30)


def shift(auto_push=0, auto_pull=0, in_right=0, out_right=0, push_thresh=0, pull_thresh=0):
    return ((auto_push << 16) | (auto_pull << 17) | (in_right << 18) | (out_right << 19) |
            ((push_thresh & 31) << 20) | ((pull_thresh & 31) << 25))
