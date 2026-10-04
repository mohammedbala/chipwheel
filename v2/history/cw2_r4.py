"""Chipwheel v2 assembler and cycle-accurate reference model.

Written from v2/docs/spec.md. The model advances one rising clock edge per
`edge()` call. Input convention: the inputs passed to `edge()` are the values
present at that rising edge and throughout the following high phase (the test
bench changes inputs just after the falling edge). Outputs returned are those
after the edge, with the same inputs still applied.
"""
import re

MEM_WORDS = 15
NINS = 12
DIV, PADS, CFG = 12, 13, 14

# ------------------------------------------------------------------ assembler
PINS = {'P0': 0, 'P1': 1, 'P2': 2, 'P3': 3}


class AsmError(ValueError):
    pass


def _num(tok, lo, hi, what):
    try:
        v = int(tok, 0)
    except ValueError:
        raise AsmError(f'bad {what}: {tok!r}')
    if not lo <= v <= hi:
        raise AsmError(f'{what} {v} outside {lo}..{hi}')
    return v


def _pin(tok):
    t = tok.upper()
    if t not in PINS:
        raise AsmError(f'bad pin {tok!r}')
    return PINS[t]


def assemble(text):
    """Assemble program text. Returns (words, labels).

    Syntax, one instruction per line, ';' comments, 'label:' prefixes:
      BR label | BRX label | BRF0 label | BRF1 label
      SET Pn, v [, delay]
      SHIFT n [, D] [, A]       (n = 1..16; D = pull+drive, A = push)
      LDX k | WAIT Pn, v | PULL | PUSH | HALT | TST | NOP | DELAY d
    """
    lines = []
    labels = {}
    for raw in text.splitlines():
        line = raw.split(';', 1)[0].strip()
        while True:
            m = re.match(r'^([A-Za-z_]\w*):\s*(.*)$', line)
            if not m:
                break
            if m.group(1) in labels:
                raise AsmError(f'duplicate label {m.group(1)}')
            labels[m.group(1)] = len(lines)
            line = m.group(2).strip()
        if line:
            lines.append(line)
    if len(lines) > NINS:
        raise AsmError(f'{len(lines)} instructions > {NINS}')
    words = []
    for line in lines:
        parts = line.split(None, 1)
        mn = parts[0].upper()
        args = [a.strip() for a in parts[1].split(',')] if len(parts) > 1 else []

        def target(tok):
            if tok in labels:
                return labels[tok]
            return _num(tok, 0, 15, 'target')

        def need(n):
            if len(args) != n:
                raise AsmError(f'{mn} takes {n} operands: {line!r}')
        if mn in ('BR', 'BRX', 'BRF0', 'BRF1'):
            need(1)
            cc = {'BR': 0, 'BRX': 1, 'BRF0': 2, 'BRF1': 3}[mn]
            words.append((cc << 4) | target(args[0]))
        elif mn == 'SET':
            if len(args) not in (2, 3):
                raise AsmError(f'SET takes 2 or 3 operands: {line!r}')
            d = _num(args[2], 0, 7, 'delay') if len(args) == 3 else 0
            words.append(0x40 | (_pin(args[0]) << 4) | (_num(args[1], 0, 1, 'value') << 3) | d)
        elif mn == 'SHIFT':
            if not args:
                raise AsmError('SHIFT needs a bit count')
            n = _num(args[0], 1, 16, 'bit count')
            flags = {a.upper() for a in args[1:]}
            if flags - {'D', 'A'}:
                raise AsmError(f'bad SHIFT flags {flags}')
            words.append(0x80 | ((n - 1) << 2) | (('D' in flags) << 1) | ('A' in flags))
        elif mn == 'LDX':
            need(1)
            words.append(0xC0 | _num(args[0], 0, 15, 'X value'))
        elif mn == 'WAIT':
            need(2)
            words.append(0xD0 | (_pin(args[0]) << 2) | (_num(args[1], 0, 1, 'value') << 1))
        elif mn in ('PULL', 'PUSH', 'HALT', 'TST', 'NOP'):
            need(0)
            words.append(0xE0 | {'PULL': 0, 'PUSH': 1, 'HALT': 2, 'TST': 3, 'NOP': 4}[mn])
        elif mn == 'DELAY':
            need(1)
            words.append(0xF0 | _num(args[0], 0, 15, 'delay'))
        else:
            raise AsmError(f'unknown mnemonic {mn!r}')
    return words, labels


def config(div=0, od=0, idle=0, insel=0, msb=0, clkd=0, ph3=0, wrap_top=15):
    """Return the three configuration bytes (words 12..14).

    od: bit i set = Pi open-drain (an open-drain pin left at 1 is an input).
    """
    return [div & 255, (idle & 15) | ((od & 15) << 4),
            insel | (msb << 1) | (clkd << 2) | (ph3 << 3) | ((wrap_top & 15) << 4)]




# ------------------------------------------------------------------ model
class Model:
    def __init__(self):
        self.mem = [None] * MEM_WORDS     # None = undefined (power-up)
        self.reset()

    def reset(self):
        self.s1 = self.s2 = self.s3 = 0
        self.pa = self.pb = 0
        self.wptr = 0
        self.running = self.pen = self.txack = self.rxv = self.ovr = 0
        self.rxd = 0
        self.pc = self.x = self.sub = self.out = self.ph = 0
        self.busy = self.rel = self.f = 0
        self.sr = 0
        self.divc = 0

    def m(self, a):
        v = self.mem[a]
        if v is None:
            raise RuntimeError(f'read of undefined memory word {a}')
        return v

    @staticmethod
    def shl(v, b, msb):
        return (((v << 1) | b) & 0x1FF) if msb else ((b << 8) | (v >> 1))

    @staticmethod
    def outb(v, msb):
        return (v >> 8) & 1 if msb else v & 1

    def outputs(self, uio_in):
        """Return (uo_out, uio_out, uio_oe, uio_out_care_mask)."""
        hsel = (uio_in >> 7) & 1
        status = (self.pb << 4) | (self.ovr << 3) | (self.rxv << 2) | (self.txack << 1) | self.running
        uo = self.rxd if hsel else status
        pads = self.mem[PADS]
        if pads is None:
            if self.pen:
                raise RuntimeError('pads enabled with undefined PADS word')
            return uo, 0, 0, 0
        od = pads >> 4
        uout = self.out & ~od & 15
        oe = ((~od | ~self.out) & 15) if self.pen else 0
        return uo, uout, oe, 0xFF

    def edge(self, ui_in, uio_in, rst_n=1):
        if not rst_n:
            self.reset()
            return self.outputs(uio_in)
        D = ui_in & 0xFF
        htag = (uio_in >> 5) & 1
        hcmd = (uio_in >> 6) & 1
        strobe = self.s2 and not self.s3
        mem_we = strobe and hcmd and htag and not self.running
        avail = self.s2 and not hcmd and not self.txack
        pulled = (D << 1) | htag
        n = {}
        setn = n.__setitem__

        setn('s1', (uio_in >> 4) & 1)
        setn('s2', self.s1)
        setn('s3', self.s2)
        setn('pa', uio_in & 15)
        setn('pb', self.pa)
        if not self.s2:
            setn('txack', 0)
        if strobe:
            setn('rxv', 0)
        if mem_we:
            setn('wptr', (self.wptr + 1) & 15)
        mem_write = (self.wptr, D) if mem_we and self.wptr < MEM_WORDS else None

        out = self.out

        def set_out(bit, v):
            nonlocal out
            out = (out & ~(1 << bit)) | ((v & 1) << bit)
            setn('out', out)

        def push(val):
            setn('rxd', val)
            setn('rxv', 1)
            if self.rxv and not strobe:
                setn('ovr', 1)

        if self.running:
            if self.divc != 0:
                setn('divc', self.divc - 1)
            else:
                cfg = self.m(CFG)
                insel, msb, clkd, ph3 = cfg & 1, (cfg >> 1) & 1, (cfg >> 2) & 1, (cfg >> 3) & 1
                wrap_top = cfg >> 4
                pads = self.m(PADS)
                idle, od = pads & 15, pads >> 4
                cpol = 0 if ph3 else (idle >> 1) & 1
                next_pc = 0 if self.pc == wrap_top else (self.pc + 1) & 15
                ins = self.m(self.pc) if self.pc < NINS else 0x00
                op = ins >> 6
                in_bit = (self.pa >> (0 if insel else 2)) & 1
                nbits = (ins >> 2) & 15
                sh_d, sh_a = (ins >> 1) & 1, ins & 1
                busy, sub = self.busy, self.sub
                last = (sub == 0) if busy else (nbits == 0)
                shift_pull = op == 2 and sh_d and not busy and ((not clkd) or self.ph == 0)
                src = pulled if shift_pull else self.sr
                shifted = self.shl(src, in_bit, msb)
                rx_word = (shifted & 0xFF) if (msb ^ (nbits == 8)) else (shifted >> 1) & 0xFF
                stall = bool(self.rel and (od >> 1) & 1 and not (self.pb >> 1) & 1)
                if not busy:
                    if op == 2 and shift_pull and not avail:
                        stall = True
                    elif op == 3:
                        sub_op = (ins >> 4) & 3
                        if sub_op == 1 and ((self.pb >> ((ins >> 2) & 3)) & 1) != ((ins >> 1) & 1):
                            stall = True
                        elif sub_op == 2 and (ins & 7) == 0 and not avail:
                            stall = True
                if not stall:
                    setn('divc', self.m(DIV))
                    setn('rel', 0)

                    def countdown(first_count):
                        if (sub == 0) if busy else (first_count == 0):
                            setn('busy', 0)
                            setn('pc', next_pc)
                        else:
                            setn('busy', 1)
                            setn('sub', (sub - 1) & 15 if busy else (first_count - 1) & 15)

                    if op == 0:
                        cc = (ins >> 4) & 3
                        if cc == 1 and self.x != 0:
                            setn('x', self.x - 1)
                        taken = cc == 0 or (cc == 1 and self.x != 0) or (cc == 2 and not self.f) or (cc == 3 and self.f)
                        setn('pc', ins & 15 if taken else next_pc)
                    elif op == 1:
                        if not busy:
                            p, v = (ins >> 4) & 3, (ins >> 3) & 1
                            set_out(p, v)
                            if p == 1 and v:
                                setn('rel', 1)
                        countdown(ins & 7)
                    elif op == 2:
                        if shift_pull:
                            setn('txack', 1)
                        if not clkd:
                            if sh_d:
                                set_out(0, self.outb(src, msb))
                            setn('sr', shifted)
                            setn('f', in_bit)
                            if last:
                                setn('busy', 0)
                                setn('pc', next_pc)
                                if sh_a:
                                    push(rx_word)
                            else:
                                setn('busy', 1)
                                setn('sub', (sub - 1) & 15 if busy else (nbits - 1) & 15)
                        elif self.ph == 0:
                            set_out(1, cpol)
                            if not busy:
                                setn('sr', src)
                                setn('sub', nbits)
                                setn('busy', 1)
                            else:
                                setn('sr', shifted)
                                setn('f', in_bit)
                            if not ph3 and sh_d:
                                set_out(0, self.outb(shifted if busy else src, msb))
                            setn('ph', 1 if ph3 else 2)
                        elif self.ph == 1:
                            if sh_d:
                                set_out(0, self.outb(self.sr, msb))
                            setn('ph', 2)
                        elif self.ph == 2:
                            set_out(1, 1 - cpol)
                            setn('rel', 1 - cpol)
                            if sub == 0:
                                setn('ph', 3)
                            else:
                                setn('sub', sub - 1)
                                setn('ph', 0)
                        else:
                            if ph3:
                                set_out(1, 0)
                            setn('sr', shifted)
                            setn('f', in_bit)
                            if sh_a:
                                push(rx_word)
                            setn('busy', 0)
                            setn('ph', 0)
                            setn('pc', next_pc)
                    else:
                        sub_op = (ins >> 4) & 3
                        if sub_op == 0:
                            setn('x', ins & 15)
                            setn('pc', next_pc)
                        elif sub_op == 1:
                            setn('pc', next_pc)
                        elif sub_op == 2:
                            mo = ins & 7
                            if mo == 0:
                                setn('sr', pulled)
                                setn('txack', 1)
                                setn('pc', next_pc)
                            elif mo == 1:
                                push((self.sr & 0xFF) if msb else (self.sr >> 1) & 0xFF)
                                setn('pc', next_pc)
                            elif mo == 2:
                                setn('running', 0)
                            elif mo == 3:
                                setn('f', self.outb(self.sr, msb))
                                setn('sr', self.shl(self.sr, 0, msb))
                                setn('pc', next_pc)
                            else:
                                setn('pc', next_pc)
                        else:
                            countdown(ins & 15)

        if strobe and hcmd and not htag:
            cmd = (D >> 4) & 7
            if cmd == 0:
                if not self.pen:
                    setn('out', self.m(PADS) & 15)
                setn('pc', D & 15)
                setn('running', 1)
                setn('pen', 1)
                setn('busy', 0)
                setn('ph', 0)
                setn('rel', 0)
                setn('divc', 0)
            elif cmd == 1:
                setn('running', 0)
            elif cmd == 2:
                setn('out', self.m(PADS) & 15)
                setn('pen', 1)
            elif cmd == 3:
                setn('wptr', 0)
            elif cmd == 4:
                setn('ovr', 0)

        for k, v in n.items():
            setattr(self, k, v)
        if mem_write:
            self.mem[mem_write[0]] = mem_write[1]
        return self.outputs(uio_in)
