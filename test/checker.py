"""Protocol oracle based on 8N1 wire framing, with no engine state access."""
def uart_expected(byte, bit_cycles):
    bits = [0] + [(byte >> i) & 1 for i in range(8)] + [1]
    # Samples E1 through completion: setup edge, frame, final halt edge.
    return [1] + [bit for bit in bits for _ in range(bit_cycles)] + [1]


def check_uart(byte, bit_cycles, observed):
    expected = uart_expected(byte, bit_cycles)
    assert len(observed) == len(expected), 'completion timing/timeout mismatch'
    for edge, (actual, want) in enumerate(zip(observed, expected), 1):
        assert actual == want, f'UART byte={byte} B={bit_cycles} edge=E{edge}: got {actual}, expected {want}'
    # Independent decode samples bit centers from the observed wire.
    bits = [observed[1 + (i + 1) * bit_cycles + bit_cycles // 2] for i in range(8)]
    decoded = sum(bit << i for i, bit in enumerate(bits))
    assert decoded == byte, f'decoded {decoded}, expected {byte}'
    return decoded
