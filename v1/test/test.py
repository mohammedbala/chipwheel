import json
import os
import random
import sys
import time
from pathlib import Path

import cocotb
from cocotb.triggers import Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from programs.encode import uart, pulse, word, validate_payload
from checker import check_uart, uart_expected


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.cycles = 0
        self.completed = 0
        self.checked = 0
        self.instruction_cases = 0
        self.timeouts = 0

    async def tick(self, **inputs):
        for name, value in inputs.items():
            getattr(self.dut, name).value = value
        self.dut.clk.value = 0
        await Timer(50, unit='ns')
        self.dut.clk.value = 1
        await Timer(50, unit='ns')
        self.cycles += 1
        result = int(self.dut.uo_out.value)
        assert result >> 3 == 0
        assert int(self.dut.uio_out.value) == 0
        assert int(self.dut.uio_oe.value) == 0
        return result

    async def reset(self):
        assert await self.tick(rst_n=0, ena=1, ui_in=0, uio_in=0) == 1
        assert await self.tick(rst_n=1) == 1

    async def load(self, words):
        assert 0 < len(words) <= 32
        for address, value in enumerate(words):
            assert 0 <= value <= 65535
            await self.tick(ui_in=value & 255, uio_in=(address << 3) | 2)
            await self.tick(ui_in=value >> 8, uio_in=(address << 3) | 6)
        await self.tick(uio_in=0)

    async def start(self, byte=0):
        validate_payload(byte)
        await self.tick(uio_in=0)
        assert await self.tick(ui_in=byte, uio_in=1) == 3

    async def trace(self, length, expected_pin, error=0):
        # Fixed specification deadline; a stalled implementation fails here.
        for edge in range(1, length + 1):
            actual = await self.tick()
            expected_busy = edge < length
            if edge == length and actual & 2:
                self.timeouts += 1
            expected_error = error if edge == length else 0
            assert actual == (expected_error << 2) | (int(expected_busy) << 1) | expected_pin[edge - 1], (
                f'edge={edge}: public output {actual}, expected pin={expected_pin[edge-1]}, busy={expected_busy}, error={error}')
        self.instruction_cases += 1

    async def frame(self, byte, duration, gap=0, demo=False):
        for _ in range(gap):
            assert await self.tick(uio_in=0) == 1
        await self.start(byte)
        observed = []
        for edge in range(1, 2 + 10 * duration + 1):
            actual = await self.tick()
            observed.append(actual & 1)
            assert actual & 4 == 0, f'unexpected error on edge {edge}'
            expected_busy = edge < 2 + 10 * duration
            if not expected_busy and actual & 2:
                self.timeouts += 1
            assert bool(actual & 2) == expected_busy, f'busy/completion at edge {edge}'
        self.completed += 1
        check_uart(byte, duration, observed)
        self.checked += 1
        if demo:
            demo_path = Path(os.environ.get('CW_DEMO', 'output/demo-A.json'))
            demo_path.parent.mkdir(parents=True, exist_ok=True)
            demo_path.write_text(json.dumps({
                'byte': byte, 'character': 'A', 'bit_cycles': duration,
                'edge_origin': 'START acceptance=E0; first array sample=E1',
                'expected': uart_expected(byte, duration), 'observed': observed,
                'decoded': byte}, indent=2) + '\n')


async def instruction_regression(b):
    # Literal edge traces derived from spec, not an RTL interpreter.
    for delay in (0, 1, 2, 4095):
        await b.load([word('OUT', 0), word('WAIT', delay), word('OUT', 1), word('HALT')])
        await b.start()
        await b.trace(delay + 4, [0] * (delay + 2) + [1, 1])
    for count in (0, 1, 2, 255):
        await b.load([word('COUNT', count), word('OUT', 0), word('DJNZ', 2), word('OUT', 1), 0])
        await b.start()
        n = max(count, 1)
        await b.trace(n + 4, [1] + [0] * (n + 1) + [1, 1])
    await b.load([word('JMP', 2), 0xffff, word('OUT', 0), 0])
    await b.start()
    await b.trace(3, [1, 0, 1])
    # SHIFT shifts zeros into the most significant position, including ninth shift.
    await b.load([word('SHIFT')] * 9 + [0])
    await b.start(0x81)
    await b.trace(10, [1, 0, 0, 0, 0, 0, 0, 1, 0, 1])
    # Every undefined opcode, plus each reserved-argument class.
    for invalid in [i << 12 for i in range(7, 16)] + [1, 0x1002, 0x3001, 0x4100, 0x5020, 0x6020]:
        await b.load([invalid])
        await b.start()
        await b.trace(1, [1], error=1)
    # Both a legal jump and illegal fallthrough at the last memory address.
    await b.load([word('JMP', 31)] + [0] * 30 + [word('JMP', 1)])
    await b.start()
    await b.trace(3, [1, 1, 1])
    await b.load([word('JMP', 31)] + [0] * 30 + [word('OUT', 0)])
    await b.start()
    await b.trace(2, [1, 1], error=1)
    await b.load([word('JMP', 31)] + [0] * 30 + [word('DJNZ', 0)])
    await b.start()
    await b.trace(2, [1, 1], error=1)
    await b.load([word('COUNT', 2), word('JMP', 31)] + [0] * 29 + [word('DJNZ', 2)])
    await b.start()
    await b.trace(4, [1, 1, 1, 1])
    await b.load([word('JMP', 0)])
    await b.start()
    for _ in range(12):
        assert await b.tick() == 3
    await b.reset()
    # Pulse timing: four low clocks, six high, three low, halt high.
    await b.load(pulse())
    await b.start()
    await b.trace(14, [0]*4 + [1]*6 + [0]*3 + [1])
    b.completed += 1
    b.checked += 1


async def control_regression(b):
    await b.load([word('OUT', 0), word('WAIT', 2), 0])
    await b.start()
    assert await b.tick() == 2
    # A write to the next instruction while busy is rejected, not executed.
    assert await b.tick(ui_in=255, uio_in=(1 << 3) | 2) == 6
    assert await b.tick(uio_in=0) == 6
    assert await b.tick() == 6
    assert await b.tick() == 5
    # Memory survives reset, held START doesn't restart after HALT.
    await b.reset()
    await b.start()
    await b.trace(5, [0, 0, 0, 0, 1])
    for _ in range(4):
        assert await b.tick() == 1
    # Pause the low pulse: disabled START consumed, then resume correctly.
    await b.start()
    assert await b.tick(uio_in=0) == 2
    for _ in range(4):
        assert await b.tick(ena=0, uio_in=1) == 2
    assert await b.tick(ena=1) == 2
    assert await b.tick() == 2
    assert await b.tick() == 2
    assert await b.tick() == 1
    assert await b.tick() == 1
    # Disabled requests don't execute upon enable.
    await b.tick(uio_in=0)
    assert await b.tick(ena=0, uio_in=1) == 1
    assert await b.tick(ena=1) == 1
    # Simultaneous write/start: write wins, error sets; subsequent START clears.
    await b.tick(uio_in=0)
    assert await b.tick(ui_in=0, uio_in=3) == 5
    assert await b.tick(uio_in=0) == 5
    await b.start()
    await b.trace(5, [0, 0, 0, 0, 1])


@cocotb.test(timeout_time=180, timeout_unit='sec')
async def chipwheel_regression(dut):
    b = Bench(dut)
    started = time.perf_counter()
    failures = 0
    try:
        await b.reset()
        mode = os.environ.get('CW_MODE', 'short')
        if mode == 'mutation':
            await b.load(uart(8))
            await b.frame(65, 8, demo=True)
        elif mode == 'campaign':
            rng = random.Random(int(os.environ.get('CW_SEED', '20261003')))
            previous = None
            for _ in range(int(os.environ.get('CW_TRANSACTIONS', '2000'))):
                duration = rng.choice([3, 4, 8, 17, 31, 80])
                if duration != previous:
                    await b.load(uart(duration))
                    previous = duration
                await b.frame(rng.randrange(256), duration, rng.randrange(13))
        else:
            await b.load(uart(8))
            await b.frame(65, 8, demo=True)
            await instruction_regression(b)
            # Control checks live separately to pinpoint handshake regressions.
            await b.load([0])
            # Accepted start immediately followed by reset while ena=0.
            await b.start()
            assert await b.tick(rst_n=0, ena=0) == 1
            await b.reset()
            for duration in (3, 4, 8, 17):
                await b.load(uart(duration))
                for byte in range(256):
                    await b.frame(byte, duration, byte % 4)
            await b.load(uart(4096))
            await b.frame(65, 4096)
            # Busy starts never queue; changing the payload doesn't change latched data.
            await b.load(uart(8))
            await b.start(65)
            observed = []
            for edge in range(1, 83):
                actual = await b.tick(ui_in=255, uio_in=0 if edge == 3 else 1)
                observed.append(actual & 1)
                assert bool(actual & 2) == (edge < 82)
            check_uart(65, 8, observed)
            b.completed += 1; b.checked += 1
            assert await b.tick() == 1
            # Abort at every edge in a short frame; exact reset output and recovery.
            for abort in range(82):
                await b.start(0)
                for _ in range(abort):
                    await b.tick()
                assert await b.tick(rst_n=0) == 1
                await b.tick(rst_n=1, uio_in=0)
                await b.frame(65, 8)
            await control_regression(b)
    except BaseException:
        failures = 1
        raise
    finally:
        elapsed = time.perf_counter() - started
        Path(os.environ.get('CW_METRICS', 'metrics.json')).write_text(json.dumps({
            'completed_transactions': b.completed, 'checked_transactions': b.checked,
            'simulator_python': sys.version,
            'instruction_cases': b.instruction_cases, 'failures': failures,
            'timeouts': b.timeouts, 'simulated_clock_cycles': b.cycles,
            'simulation_wall_seconds': elapsed,
            'checked_transactions_per_second': b.checked / elapsed,
        }, indent=2) + '\n')
