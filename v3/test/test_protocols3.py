"""Protocol tests for Chipwheel v3.

Each test runs a real program on the reference model with independent pin-level
devices or line-code reference functions, checks the protocol result there, and
then replays the identical stimulus on the RTL, which must reproduce every output
bit on every clock. Programs marked "pico-examples" are the unmodified RP2040 ones.
"""
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'model'))
sys.path.insert(0, str(HERE))
from asm import assemble  # noqa: E402
from bench3 import Bench3, config3, LineRecorder, UartSource, SpiSlave, I2cSlave, uart_decode  # noqa: E402
import devices3 as dv  # noqa: E402
import replay  # noqa: E402

PROG = HERE.parent / 'programs'
GL = os.environ.get('CW3_GL')


def vvp():
    if GL:
        return replay.build([GL] + replay.GL_MODELS, defines=('GL_TEST', 'FUNCTIONAL'), tag='gl')
    return replay.build()


def check_rtl(b):
    cycles, errors, log = replay.replay(b, vvp())
    assert errors == 0, log[-1500:]
    return cycles


def prog(name, origin=0):
    return assemble((PROG / name).read_text(), origin=origin)


def ins(text, p=None):
    """One instruction assembled with program p's side-set layout (for EXEC)."""
    head = ''
    if p is not None and p.side_count:
        n = p.side_count - p.side_en
        head = f'.side_set {n}{" opt" if p.side_en else ""}{" pindirs" if p.side_pindir else ""}\n'
    return assemble(head + text).words[0]


def start(b, p, sm, regs, execs=(), od=0, owner=0, enable=None):
    b.load(p)
    b.configure(sm, regs)
    # SM0 reaches bank B (words 16..31) only with SPAN (spec section 2)
    span = int(sm == 0 and p.origin + len(p.words) > 16)
    b.globals(od=od, owner=owner, span=span)
    for t in execs:
        b.exec(sm, ins(t, p))
    b.restart(sm, p.origin)
    if enable is not None:
        b.enable(enable)


# ---------------------------------------------------------------- UART
@pytest.mark.parametrize('name,bit', [('uart_tx.pio', 8), ('uart_tx_fast.pio', 1)])
def test_uart_tx(name, bit):
    """pico-examples uart_tx (8 cycles/bit) and a 1-cycle/bit variant (10 clocks/byte)."""
    rec = LineRecorder(0)
    b = Bench3([rec])
    b.reset()
    p = prog(name)
    start(b, p, 0, config3(prog=p, out_count=1, set_count=1),
          ['set pins, 1', 'set pindirs, 1'])
    t0 = b.cycle
    b.enable(1)
    data = [0x41, 0x55, 0x00, 0xFF, 0xA5, 0x3C, 0x81, 0x7E]
    for d in data:
        b.send(0, d)
    b.step(24 * bit + 40)
    got, starts = uart_decode(rec.trace, bit, start=t0)
    assert got == data
    if bit == 1:
        assert {y - x for x, y in zip(starts, starts[1:])} == {10}
    check_rtl(b)


def test_uart_rx_pico():
    """pico-examples uart_rx at 8 cycles/bit, back-to-back frames."""
    data = [0x41, 0x00, 0xFF, 0x5A, 0xC3, 0x81]
    b = Bench3([UartSource(2, data, 8, gap=1, start=700)])
    b.reset()
    p = prog('uart_rx.pio')
    start(b, p, 0, config3(prog=p, in_base=2, jmp_pin=2), enable=1)
    assert [b.read(0) for _ in data] == data
    assert b.chip.irq == 0
    check_rtl(b)


def test_full_duplex_uart_two_state_machines():
    """SM0 transmits (uart_tx) while SM1 receives (uart_rx at origin 16) concurrently."""
    rx_data = [0x12, 0x34, 0x56, 0x78]
    tx_data = [0xDE, 0xAD, 0xBE, 0xEF]
    rec = LineRecorder(0)
    b = Bench3([rec, UartSource(2, rx_data, 8, gap=3, start=1400)])
    b.reset()
    tx = prog('uart_tx.pio')
    rx = prog('uart_rx.pio', origin=16)
    start(b, tx, 0, config3(prog=tx, out_count=1, set_count=1), ['set pins, 1', 'set pindirs, 1'])
    start(b, rx, 1, config3(prog=rx, in_base=2, jmp_pin=2))
    t0 = b.cycle
    b.enable(3)
    got = []
    for d in tx_data:
        b.send(0, d)
        if (b.status >> 3) & 1:
            got.append(b.read(1))
    while len(got) < len(rx_data):
        got.append(b.read(1))
    b.step(200)
    assert got == rx_data
    assert uart_decode(rec.trace, 8, start=t0)[0] == tx_data
    check_rtl(b)


# ---------------------------------------------------------------- SPI
def test_spi_cpha0_full_duplex():
    """pico-examples spi_cpha0 (mode 0), autopull/autopush 8-bit, MSB first."""
    tx, replies = [0x9F, 0x00, 0xA5, 0x3C], [0x12, 0x34, 0x56, 0x78]
    slave = SpiSlave(mode=0, replies=list(replies))
    b = Bench3([slave])
    b.reset()
    p = prog('spi_cpha0.pio')
    start(b, p, 0, config3(prog=p, div=1, out_base=0, out_count=1, side_base=1, in_base=2,
                           set_base=0, set_count=4, out_left=1, in_left=1, autopull=1, autopush=1,
                           pull_thresh=8, push_thresh=8),
          ['set pins, 0 side 0', 'set pindirs, 3 side 0', 'set pindirs, 11 side 0'], enable=1)
    # SCK and MOSI are driven low before CS is asserted, as real firmware does
    rx = []
    for t in tx:
        b.send(0, t)
        rx.append(b.read(0))
    b.step(20)
    assert slave.received == tx
    assert rx == replies
    check_rtl(b)


def test_spi_slave_edge_clocked():
    """CLKSRC=1: the state machine steps on SCK rising edges, sampling MOSI once per bit."""
    data = [0xA5, 0x3C, 0xF0, 0x0F]
    master = dv.SpiMaster(data, sck=1, mosi=0, cs=3, miso=2, half=5, start=600)
    b = Bench3([master])
    b.reset()
    p = prog('spi_slave_rx.pio')
    start(b, p, 0, config3(prog=p, clksrc=1, clk_pin=1, in_fast=1, in_base=0, in_left=1,
                           autopush=1, push_thresh=8), enable=1)
    got = [b.read(0) for _ in data]
    assert got == data
    check_rtl(b)


def test_qspi_four_lanes():
    """OUT PINS, 4 with a side-set clock: one nibble per SCK, MSB nibble first."""
    rec = dv.Recorder()
    b = Bench3([rec])
    b.reset()
    p = prog('qspi_out.pio')
    start(b, p, 0, config3(prog=p, out_base=0, out_count=4, side_base=4, set_base=0, set_count=5,
                           out_left=1, autopull=1, pull_thresh=8),
          ['set pindirs, 31 side 0'])
    t0 = b.cycle
    b.enable(1)
    data = [0x12, 0xAB, 0x5C, 0xF0]
    for d in data:
        b.send(0, d)
    b.step(20)
    nibs, prev = [], 0
    for v in rec.trace[t0:]:
        clk = (v >> 4) & 1
        if clk and not prev:
            nibs.append(v & 15)
        prev = clk
    got = [(nibs[2 * k] << 4) | nibs[2 * k + 1] for k in range(len(nibs) // 2)]
    assert got[:len(data)] == data
    check_rtl(b)


# ---------------------------------------------------------------- I2C
def test_i2c_write_then_read_with_stretching():
    slave = I2cSlave(addr=0x50, stretch=15, regs={0x10: 0xDE, 0x11: 0xAD})
    b = Bench3([slave])
    b.reset()
    p = prog('i2c_master.pio')
    init = config3(prog=p, out_base=0, out_count=1, set_base=0, set_count=2, side_base=1, in_base=0,
                   out_left=1, in_left=1, autopull=1, pull_thresh=9, fifo16=1)
    regs = config3(prog=p, out_base=0, out_count=1, set_base=0, set_count=1, side_base=1, in_base=0,
                   out_left=1, in_left=1, autopull=1, pull_thresh=9, fifo16=1)
    # both open-drain pins released (value 1) with direction set, then the program's SET covers SDA only
    start(b, p, 0, init, ['set pins, 3', 'set pindirs, 3'], od=0b11)
    b.configure(0, regs)

    def xfer(byte, ack):
        b.send(0, (byte << 8) | (ack << 7), nibbles=4)
        v = b.read(0, nibbles=4)
        return (v >> 1) & 0xFF, v & 1

    def run_from(label):
        b.enable(0)
        b.restart(0, p.addr(label))
        b.enable(1)

    run_from('start')
    acks = [xfer(v, 1)[1] for v in (0xA0, 0x30, 0x42)]          # write reg 0x30 = 0x42
    run_from('stop')
    b.step(400)
    assert acks == [0, 0, 0] and slave.regs[0x30] == 0x42
    run_from('start')
    assert [xfer(v, 1)[1] for v in (0xA0, 0x10)] == [0, 0]       # pointer = 0x10
    run_from('start')                                            # repeated START
    assert xfer(0xA1, 1)[1] == 0
    got = [xfer(0xFF, 0)[0], xfer(0xFF, 1)[0]]                   # ACK then NACK
    run_from('stop')
    b.step(400)
    assert got == [0xDE, 0xAD]
    kinds = [e[0] for e in slave.log]
    assert kinds.count('start') == 3 and kinds.count('stop') == 2
    check_rtl(b)


# ---------------------------------------------------------------- WS2812
def test_ws2812_pico():
    rec = LineRecorder(0)
    b = Bench3([rec])
    b.reset()
    p = prog('ws2812.pio')
    start(b, p, 0, config3(prog=p, out_count=0, side_base=0, set_base=0, set_count=1, out_left=1,
                           autopull=1, pull_thresh=8),
          ['set pindirs, 1 side 0'])
    t0 = b.cycle
    b.enable(1)
    data = [0xFF, 0x00, 0xA5, 0x3C]
    for d in data:
        b.send(0, d)
    b.step(150)
    highs, n = [], 0
    for lv in rec.trace[t0:]:
        if lv:
            n += 1
        elif n:
            highs.append(n)
            n = 0
    bits = [1 if h >= 6 else 0 for h in highs]
    assert set(highs) <= {2, 7}, highs
    got = [sum(bits[8 * k + i] << (7 - i) for i in range(8)) for k in range(len(bits) // 8)]
    assert got[:len(data)] == data
    check_rtl(b)


# ---------------------------------------------------------------- line unit: USB style
USB_BIT = 4      # clocks per bit (12.5 Mbit/s at 50 MHz)


def usb_line_cfg(p, tx=0, rx=0, in_base=2):
    return config3(prog=p, div=USB_BIT - 1, out_base=0, out_count=1, set_base=0, set_count=2, pull_thresh=8,
                   in_base=in_base, resync=rx, line_tx=tx, line_rx=rx, enc=1, dec=1,
                   stuff_n=6, stuff_ones=1, crc_out=tx, crc_in=rx, crc_reflect=1, poly=0xA001,
                   diff=tx, fifo16=rx)


def test_usb_style_tx_nrzi_stuffing_crc16():
    rec = dv.Recorder()
    b = Bench3([rec])
    b.reset()
    p = prog('usb_tx.pio')
    data = [0x00, 0xFF, 0xFF, 0x3F, 0x80, 0x55]                 # long runs force stuffing
    start(b, p, 0, usb_line_cfg(p, tx=1),
          ['set pins, 1', 'set pindirs, 3', f'set x, {len(data) - 1}'], enable=1)
    for v in [0x80, 0xC3] + data:                               # SYNC, PID (DATA0), data
        b.send(0, v)
    b.step(1200)
    dp, dm = rec.pin(0), rec.pin(1)
    t0 = next(i for i, v in enumerate(dp) if v == 0)            # first K
    se0 = next(i for i in range(t0, len(dp)) if dp[i] == 0 and dm[i] == 0)
    nbits = (se0 - t0) // USB_BIT                               # SE0 starts just after the last bit
    line = dv.sample_bits(dp, t0, USB_BIT, nbits)
    bits, errs = dv.destuff(dv.nrzi_decode(line, 1), 6, True)
    assert errs == 0
    assert dv.bits_to_bytes_lsb(bits[:16]) == [0x80, 0xC3]
    payload = bits[16:]
    assert dv.bits_to_bytes_lsb(payload[:-16]) == data
    assert payload[-16:] == dv.crc16_usb(payload[:-16])
    assert dv.usb_crc16_ok(payload)
    assert all(dp[i] == dm[i] ^ 1 for i in range(t0, se0))      # differential
    check_rtl(b)


def test_usb_style_rx_nrzi_destuffing_crc16():
    data = [0xC3, 0x00, 0xFF, 0xFF, 0x7E, 0x01]                 # PID + data
    crc = dv.crc16_usb(dv.bytes_lsb(data[1:]))
    bits = dv.bytes_lsb([0x80]) + dv.bytes_lsb(data) + crc
    line = dv.nrzi_encode(dv.stuff(bits, 6, True), 1)
    wave = [lv for lv in line for _ in range(USB_BIT)] + [0] * (2 * USB_BIT) + [1] * USB_BIT
    b = Bench3([dv.Waveform({2: wave}, start=900, idle={2: 1})])
    b.reset()
    p = prog('usb_rx.pio')
    nbytes = len(data) + 2
    start(b, p, 0, usb_line_cfg(p, rx=1), [f'set x, {nbytes - 1}'], enable=1)
    got = [b.read(0, nibbles=4) >> 8 for _ in range(nbytes)]
    crc_reg = b.read(0, nibbles=4)
    assert got[:len(data)] == data
    assert dv.bits_to_bytes_lsb(crc) == got[len(data):]
    # CRC unit ran over PID + data + CRC (init 0xFFFF, reflected 0xA001)
    ref = 0xFFFF
    for bt in dv.bytes_lsb(got):
        fb = (ref & 1) ^ bt
        ref = (ref >> 1) ^ (0xA001 if fb else 0)
    assert crc_reg == ref
    check_rtl(b)


# ---------------------------------------------------------------- line unit: CAN style
def test_can_style_stuffing_and_crc15():
    """NRZ with 5-bit stuffing of both polarities, CRC-15 appended by the program."""
    rec = dv.Recorder()
    b = Bench3([rec])
    b.reset()
    p = assemble('''
.program can_tx
    mov crc, null
    set x, 3                ; 4 data bytes
dat:
    pull
dbit:
    out pins, 1
    jmp !osre dbit
    jmp x-- dat
    mov osr, crc            ; CRC-15 in the top 15 bits (top-aligned polynomial)
    set x, 14
cbit:
    out pins, 1
    jmp x-- cbit
    wait 1 line
    set pins, 1
end:
    jmp end
''')
    start(b, p, 0, config3(prog=p, div=7, out_base=0, out_count=1, set_base=0, set_count=1, out_left=1, pull_thresh=8,
                           line_tx=1, enc=0, stuff_n=5, crc_out=1, poly=0x4599 << 1),
          ['set pins, 1', 'set pindirs, 1'], enable=1)
    data = [0x00, 0xFF, 0x0F, 0x83]
    for d in data:
        b.send(0, d)
    b.step(800)
    tx = rec.pin(0)
    t0 = next(i for i, v in enumerate(tx) if v == 0)
    data_bits = [(d >> (7 - i)) & 1 for d in data for i in range(8)]
    crc = dv.crc15_can(data_bits)
    frame = data_bits + [(crc >> (14 - i)) & 1 for i in range(15)]
    n_line = len(dv.stuff(frame, 5, False))
    raw = dv.sample_bits(tx, t0, 8, n_line)
    bits, errs = dv.destuff(raw, 5, False)
    assert errs == 0 and bits == frame
    assert n_line > len(frame)                                  # stuffing actually happened
    check_rtl(b)


# ---------------------------------------------------------------- line unit: Manchester
def test_manchester_loop_between_state_machines():
    """SM0 Manchester-encodes onto G4; SM1 decodes G4 by edge timing; both through pins."""
    rec = dv.Recorder()
    b = Bench3([rec])
    b.reset()
    tx = prog('bitstream.pio')
    rx = prog('bitsink.pio', origin=16)
    half = 6                                                    # clocks per half bit
    start(b, tx, 0, config3(prog=tx, div=half - 1, out_base=4, out_count=1, set_base=4, set_count=1,
                            line_tx=1, enc=3, autopull=1, pull_thresh=8),
          ['set pins, 0', 'set pindirs, 1'])
    t_drv = b.cycle                                             # G4 driven low from here on
    start(b, rx, 1, config3(prog=rx, div=(3 * half) // 2 - 1, in_base=4, line_rx=1, dec=3,
                            autopush=1, push_thresh=8))
    b.enable(3)
    data = [0x55, 0x55, 0xD5, 0x12, 0xF0, 0x3C]                 # preamble, SFD, payload
    got = []
    for d in data:
        b.send(0, d)
        if (b.status >> 3) & 1:
            got.append(b.read(1))
    while len(got) < len(data):
        got.append(b.read(1, limit=5000))
    assert got == data
    # independent decode of the recorded line
    line = rec.pin(4)
    t0 = next(i for i in range(t_drv, len(line)) if line[i] == 1) - half   # first bit 1: low then high
    halves = dv.sample_bits(line, t0, half, 2 * 8 * len(data))
    bits = [halves[2 * k + 1] for k in range(8 * len(data))]
    assert all(halves[2 * k] != halves[2 * k + 1] for k in range(8 * len(data)))
    assert [sum(bits[8 * k + i] << i for i in range(8)) for k in range(len(data))] == data
    check_rtl(b)


# ---------------------------------------------------------------- 1-Wire with timeout
@pytest.mark.parametrize('present', [True, False])
def test_onewire_reset_presence_timeout(present):
    us = 2                                                      # clocks per microsecond
    slave = dv.OneWireSlave(pin=0, cpu=us, present=present)
    b = Bench3([slave])
    b.reset()
    p = prog('onewire_reset.pio')
    start(b, p, 0, config3(prog=p, div=us - 1, set_base=0, set_count=1, in_base=0, jflag=2, in_left=1),
          ['set pins, 1', 'set pindirs, 1'], od=0b1, enable=1)
    assert b.read(0, limit=20000) == (1 if present else 0)
    assert slave.resets == (1 if present else 0)
    check_rtl(b)
