"""Small assembler: encodes instructions, never predicts waveforms."""
OPCODES = {'HALT': 0, 'OUT': 1, 'WAIT': 2, 'SHIFT': 3,
           'COUNT': 4, 'DJNZ': 5, 'JMP': 6}
LIMITS = {'HALT': 0, 'OUT': 1, 'WAIT': 4095, 'SHIFT': 0,
          'COUNT': 255, 'DJNZ': 31, 'JMP': 31}


def word(name, arg=0):
    if name not in OPCODES or type(arg) is not int or not 0 <= arg <= LIMITS[name]:
        raise ValueError(f'invalid instruction {name} {arg}')
    return (OPCODES[name] << 12) | arg


def uart(bit_cycles):
    if type(bit_cycles) is not int or not 3 <= bit_cycles <= 4096:
        raise ValueError('UART bit duration must be an integer in 3..4096')
    return [word('COUNT', 8), word('OUT', 0), word('WAIT', bit_cycles - 2),
            word('SHIFT'), word('WAIT', bit_cycles - 3), word('DJNZ', 3),
            word('OUT', 1), word('WAIT', bit_cycles - 2), word('HALT')]


def pulse():
    return [word('OUT', 0), word('WAIT', 2), word('OUT', 1),
            word('WAIT', 4), word('OUT', 0), word('WAIT', 1), word('HALT')]


def validate_payload(byte):
    if type(byte) is not int or not 0 <= byte <= 255:
        raise ValueError('payload must be a byte')
    return byte


if __name__ == '__main__':
    import argparse
    from pathlib import Path
    p = argparse.ArgumentParser()
    p.add_argument('program', choices=['uart', 'pulse'])
    p.add_argument('--bit-cycles', type=int, default=8)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    words = uart(a.bit_cycles) if a.program == 'uart' else pulse()
    content = ''.join(f'{w:04x}\n' for w in words)
    if a.output:
        a.output.write_text(content)
    else:
        print(content, end='')
