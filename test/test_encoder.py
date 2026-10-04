import pytest
from programs.encode import word, uart, validate_payload


def test_uart_overhead_regression():
    # Literal encoding for B=3: start/stop WAIT 1, data WAIT 0.
    # Catches the original one-cycle start/stop wait error permanently.
    assert uart(3) == [0x4008, 0x1000, 0x2001, 0x3000, 0x2000, 0x5003, 0x1001, 0x2001, 0]


@pytest.mark.parametrize('duration', [-1, 0, 1, 2, 4097, 3.0, True])
def test_invalid_bit_durations(duration):
    with pytest.raises(ValueError):
        uart(duration)


@pytest.mark.parametrize('byte', [-1, 256, 1.0, True])
def test_invalid_payload(byte):
    with pytest.raises(ValueError):
        validate_payload(byte)


@pytest.mark.parametrize('opcode,arg', [('BOGUS', 0), ('WAIT', -1), ('WAIT', 4096),
                                        ('COUNT', 256), ('JMP', 32), ('OUT', 2),
                                        ('SHIFT', 1), ('HALT', 1)])
def test_invalid_instruction_arguments(opcode, arg):
    with pytest.raises(ValueError):
        word(opcode, arg)
