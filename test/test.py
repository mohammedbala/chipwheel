"""Tiny Tapeout cocotb test for Chipwheel v2 (also run on the gate-level netlist).

Each scenario is first co-simulated on the reference model (v2/model) with a
scripted host and an independent pin-level device: strict UART line decoder,
SPI slave, or I2C slave with clock stretching. The protocol result is checked
there. The recorded per-clock inputs are then driven into the DUT here, and
every output bit must match the model on every clock. Inputs change 1 ns after
the falling edge; outputs are compared 5 ns after the rising edge.
"""
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'v2' / 'model'))
from bench import Bench, I2cSlave, LineRecorder, SpiSlave, UartSource, uart_decode  # noqa: E402
from cw2 import assemble, config  # noqa: E402

PROGRAMS = ROOT / 'v2' / 'programs'


def prog(name):
    return assemble((PROGRAMS / name).read_text())[0]


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
        got_out = int(dut.uio_out.value) if care else 0   # pads undefined before config load
        assert got_uo == uo, f'{name} cycle {i}: uo_out {got_uo:02x} expected {uo:02x}'
        assert got_oe == oe, f'{name} cycle {i}: uio_oe {got_oe:02x} expected {oe:02x}'
        assert (got_out ^ uout) & care == 0, f'{name} cycle {i}: uio_out {got_out:02x} expected {uout:02x}'
    dut._log.info(f'{name}: {len(bench.stim)} clocks match the reference model')


async def start(dut):
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    cocotb.start_soon(Clock(dut.clk, 20, unit='ns').start())


@cocotb.test()
async def uart_tx_stream(dut):
    """UART 8N1 at 1 and 3 clocks per bit, bytes streamed back to back."""
    await start(dut)
    for bit in (1, 3):
        rec = LineRecorder(0)
        b = Bench([rec])
        b.reset()
        b.load(prog('uart_tx.s'), config(div=bit - 1, od=0b1110, idle=0b1111, wrap_top=1))
        b.idle()
        b.run(0)
        data = [0x41, 0x00, 0xFF, 0x55, 0xA5, 0x3C]
        for d in data:
            b.send(d, tag=0)
        b.step(12 * bit + 10)
        got, starts = uart_decode(rec.trace, bit)
        assert got == data
        assert {y - x for x, y in zip(starts, starts[1:])} == {10 * bit}
        await replay(dut, b, f'uart_tx bit={bit}')


@cocotb.test()
async def uart_rx(dut):
    """UART receive, 8 clocks per bit, back-to-back frames."""
    await start(dut)
    data = [0x41, 0x5A, 0xC3, 0x00, 0xFF]
    b = Bench([UartSource(2, data, 8, gap=2, start=150)])
    b.reset()
    b.load(prog('uart_rx.s'), config(div=3, od=0b1111, idle=0b1111, clkd=1, wrap_top=2))
    b.run(0)
    assert [b.read_rx() for _ in data] == data
    await replay(dut, b, 'uart_rx')


@cocotb.test()
async def spi_full_duplex(dut):
    """SPI mode 0 at SCK = clk/2, full duplex against a slave model."""
    await start(dut)
    tx, replies = [0x9F, 0x00, 0xA5, 0x3C], [0x12, 0x34, 0x56, 0x78]
    slave = SpiSlave(mode=0, replies=list(replies), same_cycle=True)
    b = Bench([slave])
    b.reset()
    b.load(prog('spi.s'), config(div=0, od=0b0100, idle=0b1100, msb=1, clkd=1, wrap_top=0))
    b.idle()
    b.run(1)
    rx = []
    for t in tx:                      # simple host: send, then read the reply
        b.send(t)
        rx.append(b.read_rx())
    b.run(3)
    b.wait_halt()
    assert slave.received == tx and rx == replies
    await replay(dut, b, 'spi')


@cocotb.test()
async def i2c_write_read(dut):
    """I2C write, then repeated-START read with NACK, slave stretching SCL."""
    await start(dut)
    slave = I2cSlave(addr=0x50, stretch=12, regs={0x10: 0xDE, 0x11: 0xAD})
    b = Bench([slave])
    b.reset()
    b.load(prog('i2c.s'), config(div=3, od=0b1111, idle=0b1111, msb=1, clkd=1, ph3=1, insel=1, wrap_top=1))
    b.idle()
    b.run(4)                          # START
    for byte in (0xA0, 0x30, 0x77):
        b.send(byte, tag=1)
        b.read_rx()
    b.run(6)                          # STOP
    b.wait_halt()
    assert slave.regs[0x30] == 0x77
    b.run(4)
    for byte in (0xA0, 0x10):
        b.send(byte, tag=1)
        b.read_rx()
    b.run(2)                          # repeated START
    b.send(0xA1, tag=1)
    b.read_rx()
    got = []
    for k in range(2):
        b.send(0xFF, tag=k)           # ACK first byte, NACK the last
        got.append(b.read_rx())
    b.wait_halt()
    assert got == [0xDE, 0xAD]
    await replay(dut, b, 'i2c')
