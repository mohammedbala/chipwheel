# Chipwheel v2

A compact cycle-timed protocol engine for the Jane Street / Tiny Tapeout CMOS5L
protocol-emulator competition. It reads and writes pins, counts ticks, shifts
bits in and out, drives open-drain buses and streams data to and from a host.
v2 is now the Tiny Tapeout entry (`../src/project.v`); v1 is archived in `../v1/`.
This folder holds the v2 model, programs, verification and benchmark.

- [Specification](../docs/spec.md) — pins, memory map, ISA, exact timing (r6)
- [Benchmark against incumbents](docs/benchmark.md) — same-flow area, timing and speed
- RTL: `../src/project.v` (top `tt_um_chipwheel`, the Tiny Tapeout entry)
- Reference model and assembler: `model/cw2.py`; pin-level bench and devices: `model/bench.py`
- Programs: `programs/*.s` (UART TX 2 words, UART RX 3, SPI 6, I2C 10, pulses 5)

## Headline (final entry, pre-layout, equal clock)

13,829 µm² of cells (10 program words + 3 config words in a clock-gated latch
array, 70 flops). UART TX streams at 10 clocks/byte (1 bit per clock), full-duplex
SPI at 17 clocks/byte (SCK = clk/2). Throughput per area is 11.2x v1, 10.3x
(UART) / 11.5x (SPI) a one-state-machine PIO clone, and about 100x or more
soft-CPU bit-banging. See the benchmark for what is and is not 10x.

## Verify

From `v2/test` with the project's `.venv` Python:

```sh
python -m pytest -q test_protocols.py            # 32 directed tests, each replayed on RTL
CW2_GL=../../results/arena/CW2_final/netlist.v python -m pytest -q test_protocols.py   # on gates
python fuzz.py --cases 500 --cycles 4000 --seed 1                # random model-vs-RTL
python fuzz.py --cases 200 --cycles 4000 --seed 2 --gl ../../results/arena/CW2_final/netlist.v
python mutants.py                                # 12 injected faults must all be caught
python workloads.py                              # sustained throughput -> ../results/workloads.json
```

The bench co-simulates the reference model with a scripted host and
independent devices (strict UART decoder, SPI slave, I2C slave with clock
stretching), records every input, and replays the identical stimulus on Icarus
(`test/tb_replay.v`), which must reproduce every output bit on every clock.
Inputs change just after the falling edge, so even protocol-violating random
stimulus has one defined outcome.

## Writing a program

```text
; UART TX 8N1: host sends each byte with tag 0 (the start bit)
tx:   SHIFT 9, D      ; pull {byte, tag}; start bit + 8 data bits, one tick each
      SET P0, 1       ; stop bit; WRAP_TOP=1 loops back to tx for free
```

`assemble()` in `model/cw2.py` turns text into words; `config()` builds the
three configuration bytes. The host loads 13 bytes through the command channel,
then issues RUN at an entry address.
