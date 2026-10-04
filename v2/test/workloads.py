"""Measure Chipwheel v2 sustained protocol throughput (reproducible).

Each workload streams many bytes through the real host handshake, checks the
result with an independent line decoder or slave model, measures clocks per
byte from pin activity, and replays the identical stimulus on the RTL so the
measurement is of the hardware, not just the model. Writes v2/results/workloads.json.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'model'))
sys.path.insert(0, str(HERE))
from bench import Bench, Device, LineRecorder, SpiSlave, UartSource, uart_decode  # noqa: E402
from cw2 import assemble, config  # noqa: E402
import test_protocols as tp  # noqa: E402
import replay  # noqa: E402


class Rises(Device):
    def __init__(self, pin):
        super().__init__()
        self.pin, self.prev, self.at = pin, None, []

    def observe(self, lines, cycle):
        v = (lines >> self.pin) & 1
        if self.prev == 0 and v == 1:
            self.at.append(cycle)
        self.prev = v


def rtl_ok(b):
    cycles, errors, log = replay.replay(b)
    assert errors == 0, log
    return cycles


def uart_tx(nbytes=64):
    rec = LineRecorder(0)
    b = Bench([rec])
    b.reset()
    words = tp.prog('uart_tx.s')
    b.load(words, config(div=0, **tp.UART_TX_CFG))
    b.idle()
    b.run(0)
    data = [(i * 37 + 11) & 255 for i in range(nbytes)]
    for d in data:
        b.send(d, tag=0)
    b.step(30)
    got, starts = uart_decode(rec.trace, 1)
    assert got == data
    gaps = [y - x for x, y in zip(starts, starts[1:])]
    return {'setting': 'DIV=0 (1 clock per bit)', 'bytes': nbytes, 'verified_decode': True,
            'clocks_per_bit': 1, 'clocks_per_byte_sustained': sum(gaps) / len(gaps),
            'clocks_per_byte_set': sorted(set(gaps)), 'program_words': len(words),
            'program_bits': 8 * len(words), 'rtl_replay_cycles': rtl_ok(b)}


def spi_full_duplex(nbytes=48):
    slave = SpiSlave(mode=0, replies=[(i * 91 + 7) & 255 for i in range(nbytes)], same_cycle=True)
    sck = Rises(1)
    b = Bench([slave, sck])
    b.reset()
    words, entry, end = tp.spi_program(0, 0)
    b.load(words, config(div=0, od=0b0100, idle=0b1100, msb=1, clkd=1, wrap_top=0))
    b.idle()
    b.run(entry)
    tx = [(i * 53 + 3) & 255 for i in range(nbytes)]
    rx = tp.spi_stream(b, tx)
    b.run(end)
    b.wait_halt()
    assert slave.received == tx and rx == [(i * 91 + 7) & 255 for i in range(nbytes)]
    firsts = sck.at[::8]
    gaps = [y - x for x, y in zip(firsts, firsts[1:])]
    return {'setting': 'mode 0, DIV=0 (SCK = clk/2), full duplex, slave MISO valid within 1 clock',
            'bytes': nbytes, 'verified_both_directions': True,
            'clocks_per_byte_sustained': sum(gaps) / len(gaps), 'clocks_per_byte_set': sorted(set(gaps)),
            'program_words': len(words), 'program_bits': 8 * len(words), 'rtl_replay_cycles': rtl_ok(b)}


def uart_rx(nbytes=32, bit=4):
    data = [(i * 29 + 5) & 255 for i in range(nbytes)]
    src = UartSource(2, data, bit, gap=0, start=200)
    b = Bench([src])
    b.reset()
    words = tp.prog('uart_rx.s')
    b.load(words, config(div=bit // 2 - 1, od=0b1111, idle=0b1111, clkd=1, wrap_top=2))
    b.run(0)
    got = [b.read_rx(limit=100 * bit) for _ in data]
    assert got == data and not b.out[0] & 8
    return {'setting': f'{bit} clocks per bit, back-to-back frames (1 stop bit)', 'bytes': nbytes,
            'verified': True, 'clocks_per_byte_sustained': 10 * bit, 'program_words': len(words),
            'program_bits': 8 * len(words), 'rtl_replay_cycles': rtl_ok(b)}


def main():
    res = {'design': 'Chipwheel v2 (v2/src/chipwheel2.v)',
           'uart_tx': uart_tx(), 'spi_mode0': spi_full_duplex(), 'uart_rx': uart_rx(),
           'i2c': {'program_words': len(tp.prog('i2c.s')), 'program_bits': 8 * len(tp.prog('i2c.s')),
                   'note': 'write/read/repeated-start/NACK/clock stretching verified in test_protocols.py'}}
    out = HERE.parent / 'results/workloads.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
