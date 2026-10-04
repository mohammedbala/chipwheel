"""Directed protocol tests for Chipwheel v2.

Each test co-simulates the reference model with a scripted host and an
independent pin-level device, checks the protocol result with that device or a
strict line decoder, then replays the identical stimulus on the RTL, which
must reproduce every output bit on every cycle.
"""
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'model'))
sys.path.insert(0, str(HERE))
from bench import Bench, LineRecorder, UartSource, SpiSlave, I2cSlave, uart_decode, HSTB, HSEL  # noqa: E402
from cw2 import assemble, config  # noqa: E402
import replay  # noqa: E402

PROGRAMS = HERE.parent / 'programs'
GL = os.environ.get('CW2_GL')            # path to a gate-level netlist to replay instead of RTL


def vvp():
    if GL:
        return replay.build([GL] + replay.GL_MODELS, defines=('GL_TEST', 'FUNCTIONAL'), tag='gl')
    return replay.build()


def check_rtl(b):
    cycles, errors, log = replay.replay(b, vvp())
    assert errors == 0, log
    assert cycles == len(b.stim)
    return cycles


def prog(name):
    return assemble((PROGRAMS / name).read_text())[0]


# ---------------------------------------------------------------- UART TX
UART_TX_CFG = dict(od=0b1110, idle=0b1111, msb=0, clkd=0, wrap_top=1)


@pytest.mark.parametrize('bit', [1, 2, 3, 8, 17])
def test_uart_tx_stream(bit):
    data = [0x41, 0x00, 0xFF, 0x55, 0xAA, 0x80, 0x01, 0x7E]
    rec = LineRecorder(0)
    b = Bench([rec])
    b.reset()
    b.load(prog('uart_tx.s'), config(div=bit - 1, **UART_TX_CFG))
    b.idle()
    b.run(0)
    for d in data:
        b.send(d, tag=0)                     # tag 0 = start bit
    b.step(12 * bit + 20)
    got, starts = uart_decode(rec.trace, bit)
    assert got == data
    gaps = {s1 - s0 for s0, s1 in zip(starts, starts[1:])}
    if bit >= 2:
        assert gaps == {10 * bit}, gaps     # back-to-back frames, no idle gaps
    check_rtl(b)


# ---------------------------------------------------------------- UART RX
@pytest.mark.parametrize('bit', [4, 8, 16, 26])
def test_uart_rx(bit):
    data = [0x41, 0x00, 0xFF, 0x5A, 0xC3]
    src = UartSource(2, data, bit, gap=bit // 2 + 3, start=200)
    b = Bench([src])
    b.reset()
    b.load(prog('uart_rx.s'), config(div=bit // 2 - 1, od=0b1111, idle=0b1111, msb=0,
                                      clkd=1, ph3=0, insel=0, wrap_top=2))
    b.run(0)
    got = [b.read_rx(limit=40 * bit + 400) for _ in data]
    assert got == data
    assert not b.out[0] & 8, 'overrun'
    check_rtl(b)


def test_uart_rx_overrun_and_clear():
    bit = 8
    data = [0x11, 0x22, 0x33]
    src = UartSource(2, data, bit, gap=4, start=200)
    b = Bench([src])
    b.reset()
    b.load(prog('uart_rx.s'), config(div=bit // 2 - 1, od=0b1111, idle=0b1111, clkd=1, wrap_top=2))
    b.run(0)
    b.wait_until(lambda: b.cycle > 200 + 3 * 10 * bit + 3 * 4 + 20, what='all frames')
    st = b.out[0]
    assert st & 4 and st & 8, f'expected RXV and OVR, status {st:02x}'
    assert b.read_rx(ack=False) == 0x33      # newest byte overwrote the others
    b.command(0x40)                          # clear OVR (strobe also clears RXV)
    assert not b.out[0] & 12
    check_rtl(b)


# ---------------------------------------------------------------- SPI
def spi_stream(b, tx):
    """Full-duplex host: keeps the next TX byte presented, reads each RX byte."""
    rx, i, state, hold = [], 0, 'present', 0
    expect_rx = len(tx)
    guard = 0
    while len(rx) < expect_rx:
        guard += 1
        assert guard < 200000, 'spi_stream stuck'
        st = b.out[0]
        if b.ctl & HSEL:
            rx.append(st)                     # uo_out shows RXD this cycle
            b.ctl &= ~HSEL
            b.step()
            continue
        if state == 'present' and i < len(tx):
            b.ui, b.ctl = tx[i], 0
            b.step()
            b.ctl = HSTB
            state = 'wait_ack'
        elif state == 'wait_ack' and st & 2:
            b.ctl = 0
            state, i = 'wait_clear', i + 1
        elif state == 'wait_clear' and not st & 2:
            state, hold = 'hold', 2
        elif state == 'hold':
            hold -= 1
            if hold == 0:
                state = 'present'
        if st & 4 and state in ('present', 'hold', 'wait_clear') and not b.ctl & HSTB:
            if state == 'present' and i >= len(tx):
                b.ctl |= HSEL                  # last byte(s): read then explicit ack
                b.step()
                rx.append(b.out[0])
                b.ctl &= ~HSEL
                b.command(0x70)
                continue
            if state == 'present':
                b.ctl |= HSEL                  # read now; the next TX strobe clears RXV
                b.step()
                rx.append(b.out[0])
                b.ctl &= ~HSEL
                continue
        b.step()
    return rx


def spi_program(cpol, cpha):
    """Returns (words, run entry, end entry). The streaming SHIFT sits at 0
    (WRAP_TOP 0). CPHA=0 restores SCK idle at the end; CPHA=1 sets it first."""
    if cpha:
        text = f"""
      loop: SHIFT 8, D, A
            SET P1, {cpol}
            SET P3, 0
            BR loop
      end:  SET P3, 1
            HALT"""
        return assemble(text)[0], 1, 4
    text = (PROGRAMS / 'spi.s').read_text().replace('end:   SET P1, 0', f'end:   SET P1, {cpol}')
    return assemble(text)[0], 1, 3


@pytest.mark.parametrize('mode', [0, 1, 2, 3])
@pytest.mark.parametrize('div,fast', [(0, True), (1, True), (1, False), (3, False)])
def test_spi_modes(mode, div, fast):
    # fast: slave MISO settles within one clock of the SCK edge (needed at DIV=0)
    tx = [0x9F, 0x00, 0xA5, 0x3C, 0xFF, 0x01]
    replies = [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC]
    slave = SpiSlave(mode=mode, replies=list(replies), same_cycle=fast)
    b = Bench([slave])
    b.reset()
    cpol, cpha = mode >> 1, mode & 1
    words, entry, end = spi_program(cpol, cpha)
    shift_pol = cpol ^ cpha                   # clocked-shift polarity (IDLE[1])
    b.load(words, config(div=div, od=0b0100, idle=0b1100 | (shift_pol << 1),
                         msb=1, clkd=1, ph3=0, insel=0, wrap_top=0))
    b.idle()
    b.run(entry)
    rx = spi_stream(b, tx)
    b.run(end)                                # end of transfer: deselect
    b.wait_halt()
    b.step(4)
    assert slave.received == tx
    assert rx == replies
    check_rtl(b)


# ---------------------------------------------------------------- I2C
I2C_CFG = dict(od=0b1111, idle=0b1111, msb=1, clkd=1, ph3=1, insel=1, wrap_top=1)


def i2c_bench(stretch=0, div=3):
    slave = I2cSlave(addr=0x50, stretch=stretch, regs={0x10: 0xDE, 0x11: 0xAD, 0x12: 0xBE})
    b = Bench([slave], pullups=0b1111)
    b.reset()
    b.load(prog('i2c.s'), config(div=div, **I2C_CFG))
    b.idle()
    return b, slave


@pytest.mark.parametrize('stretch', [0, 7, 40])
def test_i2c_write_then_read(stretch):
    b, slave = i2c_bench(stretch)
    # write 0x20, 0x21 to registers 0x30, 0x31
    b.run(4)                                 # START
    for byte in (0xA0, 0x30, 0x20, 0x21):
        b.send(byte, tag=1)                  # tag 1 = release SDA for the slave ACK
        b.read_rx()
    b.run(6)                                 # STOP
    b.wait_halt()
    assert slave.regs[0x30] == 0x20 and slave.regs[0x31] == 0x21
    # set pointer to 0x10, repeated start, read 3 bytes (ACK, ACK, NACK)
    b.run(4)
    for byte in (0xA0, 0x10):
        b.send(byte, tag=1)
        b.read_rx()
    b.run(2)                                 # repeated START
    b.send(0xA1, tag=1)
    assert b.read_rx() == 0xA1
    got = []
    for k in range(3):
        b.send(0xFF, tag=1 if k == 2 else 0)  # tag 0 = master ACK, 1 = NACK (last)
        got.append(b.read_rx())
    b.wait_halt()                            # NACK branch issues STOP and halts
    assert got == [0xDE, 0xAD, 0xBE]
    kinds = [e[0] for e in slave.log]
    assert kinds.count('start') == 3 and kinds.count('stop') == 2
    check_rtl(b)
    return b


def test_i2c_stretch_slows_bus():
    plain = test_i2c_write_then_read(0)
    slow = test_i2c_write_then_read(40)
    assert slow.cycle > plain.cycle + 25 * 9 * 10  # each of ~90 SCL low phases was held


def test_i2c_address_nack():
    b, slave = i2c_bench()
    b.run(4)
    b.send(0xB4, tag=1)                      # nobody at 0x5A
    b.read_rx()
    b.wait_halt()                            # NACK -> STOP -> HALT
    assert slave.log[-1][0] == 'stop'
    assert ('byte', 0xB4, False) in slave.log
    check_rtl(b)


# ---------------------------------------------------------------- pulses
def test_pulse_train():
    rec = LineRecorder(0)
    b = Bench([rec])
    b.reset()
    b.load(prog('pulse.s'), config(div=0, od=0b1110, idle=0b1111))
    b.idle()
    t0 = b.cycle
    b.run(0)
    b.wait_halt()
    b.step(3)
    tr = ''.join(map(str, rec.trace[t0:]))
    assert '1' + '0' * 4 + '1' * 6 + '0' * 3 + '1' in tr, tr
    check_rtl(b)
