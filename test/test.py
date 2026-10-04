"""Tiny Tapeout cocotb test for Chipwheel v3 (also run on the gate-level netlist).

Each scenario is co-simulated on the v3 reference model (v3/model) with an
independent pin-level device or line-code checker; the protocol result is
asserted there. The recorded per-clock inputs are then driven into the DUT and
every output bit must match the model on every clock.
"""
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

ROOT = Path(__file__).resolve().parents[1]
for sub in ('v3/model', 'v2/model'):
    sys.path.insert(0, str(ROOT / sub))
from asm import assemble  # noqa: E402
from bench3 import Bench3, config3, LineRecorder, SpiSlave, I2cSlave, uart_decode  # noqa: E402
import devices3 as dv  # noqa: E402

PROG = ROOT / 'v3' / 'programs'


def prog(name, origin=0):
    return assemble((PROG / name).read_text(), origin=origin)


def ins(text, p=None):
    head = ''
    if p is not None and p.side_count:
        n = p.side_count - p.side_en
        head = f'.side_set {n}{" opt" if p.side_en else ""}{" pindirs" if p.side_pindir else ""}\n'
    return assemble(head + text).words[0]


def start(b, p, sm, regs, execs=(), od=0, owner=0, enable=None):
    b.load(p)
    b.configure(sm, regs)
    b.globals(od=od, owner=owner, span=int(sm == 0 and p.origin + len(p.words) > 16))
    for t in execs:
        b.exec(sm, ins(t, p))
    b.restart(sm, p.origin)
    if enable is not None:
        b.enable(enable)


async def replay(dut, bench, name):
    for i, (s, e) in enumerate(zip(bench.stim, bench.expect)):
        await FallingEdge(dut.clk)
        await Timer(1, unit='ns')
        dut.rst_n.value = (s >> 16) & 1
        dut.ui_in.value = (s >> 8) & 255
        dut.uio_in.value = s & 255
        await RisingEdge(dut.clk)
        await Timer(5, unit='ns')
        uo, uout, oe, care = e >> 24, (e >> 16) & 255, (e >> 8) & 255, e & 255
        got_uo = int(dut.uo_out.value)
        got_oe = int(dut.uio_oe.value)
        got_out = int(dut.uio_out.value) if care else 0
        assert got_uo == uo, f'{name} cycle {i}: uo_out {got_uo:02x} expected {uo:02x}'
        assert got_oe == oe, f'{name} cycle {i}: uio_oe {got_oe:02x} expected {oe:02x}'
        assert (got_out ^ uout) & care == 0, f'{name} cycle {i}: uio_out {got_out:02x} expected {uout:02x}'
        await Timer(1, unit='ns')
        dut.uio_in.value = (s >> 17) & 255          # value for the falling edge (DDR sampling)
    dut._log.info(f'{name}: {len(bench.stim)} clocks match the reference model')


async def begin(dut):
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    cocotb.start_soon(Clock(dut.clk, 20, unit='ns').start())


@cocotb.test()
async def uart_tx_one_bit_per_clock(dut):
    """UART 8N1 at 1 clock per bit: 10 clocks per byte, back to back."""
    await begin(dut)
    rec = LineRecorder(0)
    b = Bench3([rec])
    b.reset()
    p = prog('uart_tx_fast.pio')
    start(b, p, 0, config3(prog=p, out_count=1, set_count=1), ['set pins, 1', 'set pindirs, 1'])
    t0 = b.cycle
    b.enable(1)
    data = [0x41, 0x55, 0x00, 0xFF, 0xA5]
    for d in data:
        b.send(0, d)
    b.step(60)
    got, starts = uart_decode(rec.trace, 1, start=t0)
    assert got == data and {y - x for x, y in zip(starts, starts[1:])} == {10}
    await replay(dut, b, 'uart_tx')


@cocotb.test()
async def spi_mode0_full_duplex(dut):
    """pico-examples spi_cpha0 with a mode-0 slave."""
    await begin(dut)
    tx, replies = [0x9F, 0xA5, 0x3C], [0x12, 0x34, 0x56]
    slave = SpiSlave(mode=0, replies=list(replies))
    b = Bench3([slave])
    b.reset()
    p = prog('spi_cpha0.pio')
    start(b, p, 0, config3(prog=p, div=1, out_base=0, out_count=1, side_base=1, in_base=2,
                           set_base=0, set_count=4, out_left=1, in_left=1, autopull=1, autopush=1,
                           pull_thresh=8, push_thresh=8),
          ['set pins, 0 side 0', 'set pindirs, 3 side 0', 'set pindirs, 11 side 0'], enable=1)
    rx = []
    for t in tx:
        b.send(0, t)
        rx.append(b.read(0))
    assert slave.received == tx and rx == replies
    await replay(dut, b, 'spi')


@cocotb.test()
async def usb_style_packet(dut):
    """Line unit: NRZI, 6-ones bit stuffing, CRC16, differential pair, EOP."""
    await begin(dut)
    rec = dv.Recorder()
    b = Bench3([rec])
    b.reset()
    p = prog('usb_tx.pio')
    data = [0x00, 0xFF, 0xFF, 0x3F]
    start(b, p, 0, config3(prog=p, div=3, out_base=0, out_count=1, set_base=0, set_count=2, pull_thresh=8,
                           line_tx=1, enc=1, stuff_n=6, stuff_ones=1, crc_out=1, crc_reflect=1,
                           poly=0xA001, diff=1),
          ['set pins, 1', 'set pindirs, 3', f'set x, {len(data) - 1}'], enable=1)
    for v in [0x80, 0xC3] + data:
        b.send(0, v)
    b.step(900)
    dp, dm = rec.pin(0), rec.pin(1)
    t0 = next(i for i, v in enumerate(dp) if v == 0)
    se0 = next(i for i in range(t0, len(dp)) if dp[i] == 0 and dm[i] == 0)
    bits, errs = dv.destuff(dv.nrzi_decode(dv.sample_bits(dp, t0, 4, (se0 - t0) // 4), 1), 6, True)
    payload = bits[16:]
    assert errs == 0 and dv.bits_to_bytes_lsb(payload[:-16]) == data and dv.usb_crc16_ok(payload)
    await replay(dut, b, 'usb')


@cocotb.test()
async def manchester_between_state_machines(dut):
    """SM0 Manchester-encodes onto G4; SM1 decodes it by edge timing."""
    await begin(dut)
    b = Bench3()
    b.reset()
    tx = prog('bitstream.pio')
    rx = prog('bitsink.pio', origin=16)
    half = 6
    start(b, tx, 0, config3(prog=tx, div=half - 1, out_base=4, out_count=1, set_base=4, set_count=1,
                            line_tx=1, enc=3, autopull=1, pull_thresh=8), ['set pins, 0', 'set pindirs, 1'])
    start(b, rx, 1, config3(prog=rx, div=(3 * half) // 2 - 1, in_base=4, line_rx=1, dec=3,
                            autopush=1, push_thresh=8))
    b.enable(3)
    data = [0x55, 0xD5, 0x12, 0xF0]
    got = []
    for d in data:
        b.send(0, d)
        if (b.status >> 3) & 1:
            got.append(b.read(1))
    while len(got) < len(data):
        got.append(b.read(1, limit=5000))
    assert got == data
    await replay(dut, b, 'manchester')


@cocotb.test()
async def i2c_with_clock_stretching(dut):
    """I2C master write then read (repeated START, ACK/NACK) against a stretching slave."""
    await begin(dut)
    slave = I2cSlave(addr=0x50, stretch=15, regs={0x10: 0xDE, 0x11: 0xAD})
    b = Bench3([slave])
    b.reset()
    p = prog('i2c_master.pio')
    kw = dict(prog=p, out_base=0, out_count=1, set_base=0, side_base=1, in_base=0,
              out_left=1, in_left=1, autopull=1, pull_thresh=9, fifo16=1)
    start(b, p, 0, config3(set_count=2, **kw), ['set pins, 3', 'set pindirs, 3'], od=0b11)
    b.configure(0, config3(set_count=1, **kw))

    def xfer(byte, ack):
        b.send(0, (byte << 8) | (ack << 7), nibbles=4)
        v = b.read(0, nibbles=4)
        return (v >> 1) & 0xFF, v & 1

    def run_from(label):
        b.enable(0)
        b.restart(0, p.addr(label))
        b.enable(1)

    run_from('start')
    assert [xfer(v, 1)[1] for v in (0xA0, 0x10)] == [0, 0]
    run_from('start')
    assert xfer(0xA1, 1)[1] == 0
    got = [xfer(0xFF, 0)[0], xfer(0xFF, 1)[0]]
    run_from('stop')
    b.step(300)
    assert got == [0xDE, 0xAD]
    await replay(dut, b, 'i2c')


@cocotb.test()
async def usb_line_to_uart_translator(dut):
    """SM-to-SM link: SM0 decodes a USB-style line, SM1 re-sends each byte as UART; host idle."""
    await begin(dut)
    data = [0xC3, 0x00, 0xFF, 0x7E]
    crc = dv.crc16_usb(dv.bytes_lsb(data[1:]))
    bits = dv.bytes_lsb([0x80]) + dv.bytes_lsb(data) + crc
    line = dv.nrzi_encode(dv.stuff(bits, 6, True), 1)
    wave = [lv for lv in line for _ in range(4)] + [0] * 8 + [1] * 4
    rec = LineRecorder(0)
    b = Bench3([dv.Waveform({2: wave}, start=1500, idle={2: 1}), rec])
    b.reset()
    rx = prog('usb_rx_bridge.pio')
    tx = prog('uart_tx_fast.pio', origin=16)
    n = len(data) + 2
    start(b, rx, 0, config3(prog=rx, div=3, in_base=2, set_base=0, set_count=2, resync=1, line_rx=1, dec=1,
                            stuff_n=6, stuff_ones=1), [f'set x, {n - 1}'])
    start(b, tx, 1, config3(prog=tx, div=1, out_count=1, set_count=1), ['set pins, 1', 'set pindirs, 1'])
    b.globals(owner=0b1, link01=1)
    b.enable(3)
    t0 = b.cycle
    b.step(1500 + len(wave) + 100 - b.cycle)
    assert uart_decode(rec.trace, 2, start=t0)[0] == data + dv.bits_to_bytes_lsb(crc)
    await replay(dut, b, 'translator')


@cocotb.test()
async def ddr_capture(dut):
    """IN_DDR: 16 consecutive half-clock samples of one pin per entry (falling-edge flops)."""
    await begin(dut)
    halves = [1, 1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 1, 0, 0, 1, 0, 1, 0, 1, 1, 0, 0]
    b = Bench3([dv.HalfWave(3, halves, start=700)])
    b.reset()
    p = prog('ddr_capture.pio')
    start(b, p, 0, config3(prog=p, in_base=3, in_ddr=1, autopush=1, fifo16=1), enable=1)
    e = b.read(0, nibbles=4)
    assert [(e >> k) & 1 for k in range(16)] == halves[2:18]
    await replay(dut, b, 'ddr')
