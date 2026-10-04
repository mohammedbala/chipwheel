"""Assembler for Chipwheel v3: pioasm syntax (RP2040 PIO encoding) + v3 extensions.

Supported directives: .program, .side_set N [opt] [pindirs], .wrap_target,
.wrap, .origin N, .define NAME VALUE. Instructions use pioasm syntax with
`side V` and `[delay]` suffixes. v3 extensions:

  jmp flag, L              (condition 111 when JFLAG != 0: CRC==0 / TIMEOUT / CODE_ERR)
  wait P gpio|pin N [edge] [timeout]
  in crc, N   out crc, N   mov crc, SRC   mov DEST, crc
  in time, N               (low N bits of the free-running clock counter)

Returns a Program with words, wrap target/top and side-set settings.
"""
import re
from dataclasses import dataclass, field


class AsmError(ValueError):
    pass


@dataclass
class Program:
    name: str
    words: list
    origin: int = 0
    wrap_target: int = 0
    wrap: int = 0
    side_count: int = 0       # bits of the delay/side-set field used for side-set (incl. enable)
    side_en: int = 0
    side_pindir: int = 0
    labels: dict = field(default_factory=dict)

    def addr(self, label):
        return self.origin + self.labels[label]


JMP_COND = {'': 0, '!x': 1, 'x--': 2, '!y': 3, 'y--': 4, 'x!=y': 5, 'pin': 6, '!osre': 7, 'flag': 7}
IN_SRC = {'pins': 0, 'x': 1, 'y': 2, 'null': 3, 'crc': 4, 'time': 5, 'isr': 6, 'osr': 7}
OUT_DST = {'pins': 0, 'x': 1, 'y': 2, 'null': 3, 'pindirs': 4, 'pc': 5, 'isr': 6, 'crc': 7}
MOV_DST = {'pins': 0, 'x': 1, 'y': 2, 'crc': 3, 'exec': 4, 'pc': 5, 'isr': 6, 'osr': 7}
MOV_SRC = {'pins': 0, 'x': 1, 'y': 2, 'null': 3, 'crc': 4, 'status': 5, 'isr': 6, 'osr': 7}
SET_DST = {'pins': 0, 'x': 1, 'y': 2, 'pindirs': 4}


def _eval(expr, defines):
    """Small integer expressions in .define and [delay] (e.g. T3 - 1)."""
    toks = re.findall(r'[A-Za-z_]\w*|0x[0-9a-fA-F]+|\d+|[-+*()]', expr)
    py = ''.join(str(defines[t]) if t in defines else t for t in toks)
    if not re.fullmatch(r'[0-9xa-fA-F+\-*() ]+', py):
        raise AsmError(f'bad expression {expr!r}')
    return int(eval(py, {'__builtins__': {}}))


def _int(tok, defines):
    tok = tok.strip()
    if tok in defines:
        return defines[tok]
    try:
        return int(tok, 0)
    except ValueError:
        raise AsmError(f'bad number {tok!r}')


def assemble(text, origin=None):
    """Assemble pioasm-style text into a Program."""
    defines = {}
    name = 'program'
    instrs, labels = [], {}
    side_count, side_opt, side_pindir = 0, False, False
    wrap_target = wrap = None
    org = origin or 0
    for raw in text.splitlines():
        line = re.split(r'//|;', raw, 1)[0].strip()
        if not line:
            continue
        if line.startswith('.'):
            parts = line.split()
            d = parts[0]
            if d == '.program':
                name = parts[1]
            elif d == '.side_set':
                side_count = int(parts[1])
                side_opt = 'opt' in parts[2:]
                side_pindir = 'pindirs' in parts[2:]
            elif d == '.wrap_target':
                wrap_target = len(instrs)
            elif d == '.wrap':
                wrap = len(instrs) - 1
            elif d == '.origin':
                if origin is None:
                    org = int(parts[1], 0)
            elif d == '.define':
                rest = [t for t in parts[1:] if t != 'public']
                defines[rest[0]] = _int(' '.join(rest[1:]).replace(' ', ''), defines) if len(rest) == 2 \
                    else _eval(' '.join(rest[1:]), defines)
            else:
                raise AsmError(f'unknown directive {d}')
            continue
        while True:
            m = re.match(r'^([A-Za-z_]\w*):\s*(.*)$', line)
            if not m:
                break
            if m.group(1) in labels:
                raise AsmError(f'duplicate label {m.group(1)}')
            labels[m.group(1)] = len(instrs)
            line = m.group(2).strip()
        if line:
            instrs.append(line)
    if len(instrs) + org > 32:
        raise AsmError(f'{len(instrs)} instructions at origin {org} exceed 32 words')
    field_side = side_count + (1 if side_opt else 0)
    if field_side > 5:
        raise AsmError('side-set too wide')
    delay_bits = 5 - field_side
    for k, v in labels.items():
        defines[k] = org + v
    words = [_encode(t, defines, side_count, side_opt, delay_bits) for t in instrs]
    return Program(name, words, org, org + (wrap_target or 0),
                   org + (wrap if wrap is not None else len(words) - 1),
                   field_side, int(side_opt), int(side_pindir), labels)


def _encode(text, defines, side_count, side_opt, delay_bits):
    delay = 0
    m = re.search(r'\[([^\]]+)\]\s*$', text)
    if m:
        delay = _eval(m.group(1), defines)
        text = text[:m.start()].strip()
    side = None
    m = re.search(r'\bside\s+(\S+)\s*$', text)
    if m:
        side = _int(m.group(1), defines)
        text = text[:m.start()].strip()
    if delay >= (1 << delay_bits):
        raise AsmError(f'delay {delay} too large ({delay_bits} bits): {text}')
    if side is None and side_count and not side_opt:
        raise AsmError(f'side-set required: {text}')
    if side is not None and not side_count:
        raise AsmError(f'no .side_set declared: {text}')
    f = delay
    if side is not None:
        if side >= (1 << side_count):
            raise AsmError(f'side value {side} too large')
        f |= side << delay_bits
        if side_opt:
            f |= 1 << 4
    parts = text.replace(',', ' , ').split()
    op = parts[0].lower()
    args = [a for a in ' '.join(parts[1:]).split(',')]
    args = [a.strip() for a in args if a.strip()]

    def word(opc, low):
        return (opc << 13) | (f << 8) | (low & 0xFF)
    if op == 'nop':
        return word(5, (2 << 5) | 2)
    if op == 'jmp':
        toks = ' '.join(args).split()          # pioasm: jmp [cond] target
        if len(toks) == 1:
            cond, tgt = '', toks[0]
        else:
            cond, tgt = toks[0].lower(), toks[1]
        if cond not in JMP_COND:
            raise AsmError(f'bad jmp condition {cond}')
        return word(0, (JMP_COND[cond] << 5) | (_int(tgt, defines) & 31))
    if op == 'wait':
        toks = ' '.join(args).split()
        pol = _int(toks[0], defines)
        src = toks[1].lower()
        if src == 'line':                       # v3: TX line unit idle (1) / busy (0)
            return word(1, (pol << 7) | (3 << 5))
        idx = _int(toks[2], defines)
        rest = [t.lower() for t in toks[3:]]
        if src in ('gpio', 'pin'):
            if idx > 7:
                raise AsmError('pin index > 7')
            idx |= (8 if 'edge' in rest else 0) | (16 if 'timeout' in rest else 0)
            s = 0 if src == 'gpio' else 1
        elif src == 'irq':
            if idx > 3:
                raise AsmError('irq index > 3')
            idx |= 16 if 'rel' in rest else 0
            s = 2
        else:
            raise AsmError(f'bad wait source {src}')
        return word(1, (pol << 7) | (s << 5) | idx)
    if op == 'in':
        return word(2, (IN_SRC[args[0].lower()] << 5) | (_int(args[1], defines) & 31))
    if op == 'out':
        return word(3, (OUT_DST[args[0].lower()] << 5) | (_int(args[1], defines) & 31))
    if op in ('push', 'pull'):
        flags = [a.lower() for a in ' '.join(args).split()]
        cond = 'iffull' in flags or 'ifempty' in flags
        block = 'noblock' not in flags
        return word(4, ((op == 'pull') << 7) | (cond << 6) | (block << 5))
    if op == 'mov':
        dst = args[0].lower()
        src = args[1].replace(' ', '').lower()
        mop = 0
        if src.startswith('!') or src.startswith('~'):
            mop, src = 1, src[1:]
        elif src.startswith('::'):
            mop, src = 2, src[2:]
        return word(5, (MOV_DST[dst] << 5) | (mop << 3) | MOV_SRC[src])
    if op == 'irq':
        toks = [t.lower() for t in ' '.join(args).split()]
        clear = 'clear' in toks
        wait = 'wait' in toks
        rel = 'rel' in toks
        nums = [t for t in toks if t not in ('set', 'nowait', 'wait', 'clear', 'rel')]
        idx = _int(nums[0], defines)
        return word(6, (clear << 6) | (wait << 5) | (rel << 4) | (idx & 3))
    if op == 'set':
        return word(7, (SET_DST[args[0].lower()] << 5) | (_int(args[1], defines) & 31))
    raise AsmError(f'unknown instruction {op}')
