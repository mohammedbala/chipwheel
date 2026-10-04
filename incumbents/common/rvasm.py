"""Minimal two-pass RV32I assembler for the bit-bang incumbent programs.

Only the handful of base-ISA instructions the programs use (plus a few
pseudo-instructions). No toolchain is installed; every program assembled here
is verified by simulation on the actual core.

    words, listing = assemble(src_text)
"""
import re

ABI = {'zero': 0, 'ra': 1, 'sp': 2, 'gp': 3, 'tp': 4, 't0': 5, 't1': 6, 't2': 7,
       's0': 8, 'fp': 8, 's1': 9, 'a0': 10, 'a1': 11, 'a2': 12, 'a3': 13, 'a4': 14,
       'a5': 15, 'a6': 16, 'a7': 17, 's2': 18, 's3': 19, 's4': 20, 's5': 21, 's6': 22,
       's7': 23, 's8': 24, 's9': 25, 's10': 26, 's11': 27, 't3': 28, 't4': 29,
       't5': 30, 't6': 31}


def reg(r):
    r = r.strip()
    if r in ABI:
        return ABI[r]
    m = re.fullmatch(r'x(\d+)', r)
    assert m and int(m.group(1)) < 32, f'bad register {r}'
    return int(m.group(1))


def imm(v, labels=None):
    v = v.strip()
    if labels is not None and v in labels:
        return labels[v]
    return int(v, 0)


def r_type(f7, rs2, rs1, f3, rd, op):
    return (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op


def i_type(im, rs1, f3, rd, op):
    assert -2048 <= im < 2048, f'imm {im} out of range'
    return ((im & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op


def s_type(im, rs2, rs1, f3, op):
    assert -2048 <= im < 2048
    im &= 0xFFF
    return ((im >> 5) << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | ((im & 31) << 7) | op


def b_type(off, rs2, rs1, f3):
    assert -4096 <= off < 4096 and off % 2 == 0
    o = off & 0x1FFF
    return (((o >> 12) & 1) << 31) | (((o >> 5) & 63) << 25) | (rs2 << 20) | (rs1 << 15) | \
        (f3 << 12) | (((o >> 1) & 15) << 8) | (((o >> 11) & 1) << 7) | 0x63


def j_type(off, rd):
    assert -(1 << 20) <= off < (1 << 20) and off % 2 == 0
    o = off & 0x1FFFFF
    return (((o >> 20) & 1) << 31) | (((o >> 1) & 0x3FF) << 21) | (((o >> 11) & 1) << 20) | \
        (((o >> 12) & 0xFF) << 12) | (rd << 7) | 0x6F


ALU_I = {'addi': 0, 'slti': 2, 'sltiu': 3, 'xori': 4, 'ori': 6, 'andi': 7}
SHIFT_I = {'slli': (0, 1), 'srli': (0, 5), 'srai': (0x20, 5)}
ALU_R = {'add': (0, 0), 'sub': (0x20, 0), 'sll': (0, 1), 'slt': (0, 2), 'sltu': (0, 3),
         'xor': (0, 4), 'srl': (0, 5), 'sra': (0x20, 5), 'or': (0, 6), 'and': (0, 7)}
LOADS = {'lb': 0, 'lh': 1, 'lw': 2, 'lbu': 4, 'lhu': 5}
STORES = {'sb': 0, 'sh': 1, 'sw': 2}
BRANCH = {'beq': 0, 'bne': 1, 'blt': 4, 'bge': 5, 'bltu': 6, 'bgeu': 7}


def _mem(arg):
    m = re.fullmatch(r'\s*(-?\w+)\s*\(\s*(\w+)\s*\)\s*', arg)
    assert m, f'bad memory operand {arg}'
    return int(m.group(1), 0), reg(m.group(2))


def _expand(op, a):
    """Pseudo-instructions -> list of (op, args)."""
    if op == 'nop':
        return [('addi', ['x0', 'x0', '0'])]
    if op == 'li':
        v = int(a[1], 0)
        assert -2048 <= v < 2048, 'li only supports 12-bit immediates here'
        return [('addi', [a[0], 'x0', a[1]])]
    if op == 'mv':
        return [('addi', [a[0], a[1], '0'])]
    if op == 'j':
        return [('jal', ['x0', a[0]])]
    if op == 'beqz':
        return [('beq', [a[0], 'x0', a[1]])]
    if op == 'bnez':
        return [('bne', [a[0], 'x0', a[1]])]
    if op == 'bgez':
        return [('bge', [a[0], 'x0', a[1]])]
    if op == 'bltz':
        return [('blt', [a[0], 'x0', a[1]])]
    if op == 'sltz':
        return [('slt', [a[0], a[1], 'x0'])]
    return [(op, a)]


def _parse(src):
    items = []  # (label_list, op, args, text)
    pending = []
    for raw in src.splitlines():
        line = raw.split('#')[0].split(';')[0].strip()
        if not line:
            continue
        while ':' in line:
            lab, line = line.split(':', 1)
            pending.append(lab.strip())
            line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        op = parts[0].lower()
        args = [x.strip() for x in re.split(r',(?![^()]*\))', parts[1])] if len(parts) > 1 else []
        for i, (o, a) in enumerate(_expand(op, args)):
            items.append((pending if i == 0 else [], o, a, line))
            pending = []
    if pending:
        items.append((pending, None, None, ''))
    return items


def assemble(src, base=0):
    items = _parse(src)
    labels = {}
    pc = base
    for labs, op, _, _ in items:
        for l in labs:
            labels[l] = pc
        if op is not None:
            pc += 4
    words, listing = [], []
    pc = base
    for _, op, a, text in items:
        if op is None:
            continue
        if op in ALU_I:
            w = i_type(imm(a[2]), reg(a[1]), ALU_I[op], reg(a[0]), 0x13)
        elif op in SHIFT_I:
            f7, f3 = SHIFT_I[op]
            sh = imm(a[2])
            assert 0 <= sh < 32
            w = r_type(f7, sh, reg(a[1]), f3, reg(a[0]), 0x13)
        elif op in ALU_R:
            f7, f3 = ALU_R[op]
            w = r_type(f7, reg(a[2]), reg(a[1]), f3, reg(a[0]), 0x33)
        elif op in LOADS:
            o, rs1 = _mem(a[1])
            w = i_type(o, rs1, LOADS[op], reg(a[0]), 0x03)
        elif op in STORES:
            o, rs1 = _mem(a[1])
            w = s_type(o, reg(a[0]), rs1, STORES[op], 0x23)
        elif op in BRANCH:
            w = b_type(imm(a[2], labels) - pc, reg(a[1]), reg(a[0]), BRANCH[op])
        elif op == 'jal':
            w = j_type(imm(a[1], labels) - pc, reg(a[0]))
        elif op == 'jalr':
            o, rs1 = _mem(a[1])
            w = i_type(o, rs1, 0, reg(a[0]), 0x67)
        elif op == 'lui':
            w = ((imm(a[1]) & 0xFFFFF) << 12) | (reg(a[0]) << 7) | 0x37
        else:
            raise ValueError(f'unsupported op {op}')
        words.append(w)
        listing.append((pc, w, text))
        pc += 4
    return words, listing, labels


if __name__ == '__main__':
    # Self-check against known encodings (from the RISC-V spec / GNU as output).
    t = [('addi a0, x0, 1', 0x00100513), ('sw t0, -16(x0)', 0xFE502823),
         ('lw a0, -8(x0)', 0xFF802503), ('srli t0, t0, 1', 0x0012D293),
         ('slt t1, a0, x0', 0x00052333), ('ori t0, t0, 0x200', 0x2002E293),
         ('lui a1, 0x800', 0x008005B7), ('slli a0, a0, 24', 0x01851513),
         ('or a0, a0, t2', 0x00756533), ('andi t2, t2, 1', 0x0013F393),
         ('sub a0, a0, t2', 0x40750533)]
    for s, exp in t:
        w = assemble(s)[0][0]
        assert w == exp, f'{s}: got {w:08x} expected {exp:08x}'
    w, _, _ = assemble('L: nop\n bge a0, x0, L\n j L\n')
    assert w[1] == 0xFE055EE3, hex(w[1])
    assert w[2] == 0xFF9FF06F, hex(w[2])
    print('rvasm self-test ok')
