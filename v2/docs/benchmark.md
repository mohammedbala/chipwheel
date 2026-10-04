# Chipwheel v2 versus incumbents — 2026-10-03

Goal from the project owner: a design with a meaningful speed and size
improvement over incumbents, at least 10x. "Incumbents" was not defined, so
this benchmark uses every reasonable class, all built from real open-source
code and pushed through **one identical flow**.

## Method

- **Size**: mapped standard-cell area, IHP CMOS5L typical library, Yosys
  `synth -flatten` + `dfflibmap` + `abc` (`scripts/arena.py`). Also reported
  with an estimate of the buffers a real flow would insert on high-fanout nets.
- **Speed**: sustained payload bits per clock, measured in simulation with the
  data checked at the pins (strict UART decode; SPI slave model checking both
  directions). Fastest correct setting for every design, each incumbent with its
  best program (`incumbents/README.md`).
- **Clock**: all ratios are at an **equal clock**. Every design's pre-layout
  reg-to-reg limit is far above the roughly 50 MHz that Tiny Tapeout I/O allows
  (slow corner: v2 175, v1 201, PIO 102, FemtoRV 73, QERV 204, SERV 270 MHz), so
  per-clock throughput is what a user actually gets.
- **Combined**: bits per clock per mm² (throughput density).
- **Verification of v2**: 32 directed protocol tests (UART TX/RX, SPI modes
  0–3, I2C write/read/repeated-start/NACK/clock stretching, overrun, pulses),
  each replayed on RTL and on the synthesized gate-level netlist; random
  differential testing against an independent cycle model (millions of cycles,
  RTL and gates); 12 injected faults, all caught.

## Results (final entry: `src/project.v`, revision r6 with discrete clock gates)

| Design | Cell area µm² | UART TX clk/byte | SPI full-duplex clk/byte | v2 smaller | v2 faster UART / SPI | v2 throughput/area UART / SPI |
|---|---:|---:|---:|---:|---:|---:|
| **Chipwheel v2** | **13,829** | **10** | **17** | — | — | — |
| Chipwheel v1 | 46,940 | 33 | unsupported | 3.4x | 3.3x / ∞ | **11.2x** / ∞ |
| PIO-class, 1 state machine | 142,266 | 10 | 19 | **10.3x** | 1.0x / 1.1x | **10.3x / 11.5x** |
| FemtoRV32 bit-bang | 195,398 | 72 | 240 | **14.1x** | 7.2x / **14.1x** | **102x / 200x** |
| QERV bit-bang | 177,754 | 372 | 1,130 | **12.9x** | **37x / 66x** | **478x / 854x** |
| SERV bit-bang | 176,378 | 1,291 | 3,746 | **12.8x** | **129x / 220x** | **1,647x / 2,810x** |
| PIO-class, 4 state machines | 458,442 | 10 (each) | 19 (each) | 8.3x per channel | 1.0x / 1.1x | 8.3x / 9.3x per channel |

Including estimated repair buffers the size ratios are v1 3.3x,
PIO 10.8x, CPUs 12.6–14.3x. Program size: v2 needs 16 bits for UART TX
(PIO 160, v1 144, RISC-V 736) and 48 bits for SPI (PIO 304, RISC-V 832).

## What is and is not 10x

- **Throughput per area ≥ 10x against every single-engine incumbent**: v1
  11.2x, PIO 10.3x (UART) / 11.5x (SPI), CPUs 100x and up.
- **Size ≥ 10x** against PIO and all CPU bit-banging designs. Against v1 it is
  3.4x: v1 is already a small single-protocol engine.
- **Speed ≥ 10x per clock** against SERV and QERV, and against FemtoRV on SPI
  (7.2x on UART). Against PIO it is parity: both reach the physical limit of
  1 bit per clock for UART 8N1 (10 clocks/byte). Against v1 it is 3.3x per
  clock; at the clocks each is specified for (v1 10 MHz, v2 50 MHz) it is 16.5x.
- **Not 10x**: the full 4-state-machine PIO block divided per channel (8.3x
  UART, 9.3x SPI), because its 32-slot instruction memory is shared by four
  engines.

## Caveats

Pre-layout numbers only: typical and slow liberty corners, ideal clock, wire-load
estimate. The official LibreLane hardening, DRC/precheck and post-route timing
have not completed yet (see `docs/hardening.md` for the local container attempt). v2's
program memory is a latch array behind clock gates; this is a known Tiny
Tapeout technique, but its behaviour in the official flow's STA is unverified.
The incumbents are open-source clones, not RP2040 silicon; their memories are
plain flops, as upstream writes them.

## Reproduce

```sh
python3 incumbents/run_all.py                     # incumbent cycle counts
v2/test: python3 workloads.py; python3 -m pytest test_protocols.py; python3 fuzz.py; python3 mutants.py
python3 scripts/arena.py measure --name CW2_final --top tt_um_chipwheel src/project.v
python3 scripts/compare.py --v2 CW2_final         # table + results/benchmark.json
```
