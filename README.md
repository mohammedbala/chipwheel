# Chipwheel

A compact programmable protocol engine for Jane Street's protocol emulator ASIC
competition (Tiny Tapeout, IHP CMOS5L). A host loads a program of up to 10
eight-bit instructions; the engine drives and samples four pins with exact
timing: UART transmit/receive, SPI master in all four modes (full duplex at
SCK = clk/2), I2C master with open-drain pins and clock stretching, and timed
pulse trains. The entry is **Chipwheel v2**: `src/project.v`, top
`tt_um_chipwheel`, 50 MHz target, one tile.

[Competition](https://blog.janestreet.com/protocol-emulator-asic-competition/)
· [Datasheet](docs/info.md) · [Specification](docs/spec.md)
· [Benchmark vs incumbents](v2/docs/benchmark.md) · [v2 workbench](v2/README.md)
· [Hardening log](docs/hardening.md) · [Progress](docs/progress.md)

## Result

Same synthesis and timing flow for every design, equal clock (pre-layout):

| Design | Cell area µm² | UART TX clk/byte | SPI clk/byte | v2 throughput per area |
|---|---:|---:|---:|---:|
| **Chipwheel v2** | **13,829** | **10** | **17** | — |
| Chipwheel v1 (archived) | 46,940 | 33 | unsupported | 11.2x |
| PIO-class engine, 1 state machine | 142,266 | 10 | 19 | 10.3x / 11.5x |
| FemtoRV32 / QERV / SERV bit-banging | 176k–195k | 72–1,291 | 240–3,746 | 100x and more |

Size is 10.3–14.1x smaller than the PIO clone and the CPUs (3.4x vs v1); speed
per clock ties PIO at the 1-bit-per-clock UART limit. Full method, caveats and
what is not 10x: [v2/docs/benchmark.md](v2/docs/benchmark.md).

## Test

```sh
sh scripts/setup.sh                      # local Python + Icarus (see v1 notes below)
make -C test                             # Tiny Tapeout cocotb test (UART, SPI, I2C), needs .venv + Icarus on PATH
cd v2/test && ../../.venv/bin/python -m pytest -q test_protocols.py   # 32 directed tests, RTL replay
../../.venv/bin/python fuzz.py --cases 500 --cycles 4000             # random model-vs-RTL
../../.venv/bin/python mutants.py                                    # 12 injected faults
```

The cocotb test and the v2 suites co-simulate an independent reference model
(`v2/model/cw2.py`) with pin-level devices (strict UART decoder, SPI slave, I2C
slave with clock stretching), check the protocol result there, and require the
RTL (or gate-level netlist) to reproduce every output bit on every clock.

# Chipwheel v1 (archived)

v1 was a 32 x 16-bit flip-flop instruction memory driving one output pin at
10 MHz. Its RTL, config, `info.yaml`, datasheet, spec and cocotb test are kept
in [`v1/`](v1/). The v1 tooling described below (`scripts/run.py`,
`scripts/mutations.py`, `scripts/campaign.py`, the simulator bench) was written
for v1 at the repository root and still names `src/project.v` and
`test/test.py`; copy the `v1/` files back to those paths to rerun it. The
ten-million-check campaign keeps running from its own frozen snapshot.

## Run on this Mac

From this project directory:

```sh
sh scripts/setup.sh
.venv/bin/python -m pytest -q test/test_encoder.py
.venv/bin/python scripts/run.py short
.venv/bin/python scripts/run.py campaign --seed 20261003 --transactions 2000
.venv/bin/python scripts/mutations.py
.venv/bin/python scripts/run.py mutation --waves --name replay-A
```

Setup uses a local Python environment. On Apple Silicon it can extract an
Icarus bottle under `.tools` using the existing Homebrew, without installing
system packages. The measured version is Icarus 13.0, cocotb 2.0.1, pytest
8.4.2. Other platforms can use an existing `iverilog`/`vvp` installation.
No hardware, paid services or administrator installations are required for
simulation. `scripts/run.py` checks the test-result XML and exits nonzero on
failure. Bare template simulation remains available with:

```sh
PATH="$PWD/.venv/bin:$PWD/.tools/icarus-verilog/13.0/bin:$PATH" make -C test
```

Bulk runs suppress waveforms. Replay writes `results/replay-A-work/tb.fst`.
To replay a failed campaign, repeat its recorded seed and count with `--waves`
and a different `--name`. To replay the deliberately shortened-wait fault:

```sh
.venv/bin/python scripts/run.py mutation --source results/short-wait-work/project.v --waves --name replay-short-wait
```

This command is expected to fail, with diagnostic waveforms. Fault copies
are produced by `scripts/mutations.py`; correct RTL is never edited by it.

## What the machine does

32 writable 16-bit words, an 8-bit payload shift register, 8-bit loop counter,
12-bit wait counter and one programmable output. OUT sets the pin, SHIFT
outputs the next payload bit, WAIT holds state, COUNT/DJNZ repeat, JMP branches
and HALT stops. One instruction normally runs per rising clock edge.
WAIT N adds N stalled edges after its own instruction edge. For example,
OUT 0; WAIT 2; OUT 1 produces a four-clock low pulse.

The loader uses `ui_in` as the data byte, `uio[7:3]` as address, `uio[2]`
as byte select, `uio[1]` as write, and `uio[0]` as START. `uo[0]` is the
output, `uo[1]` busy and `uo[2]` error. Every bidirectional pin is currently
configured as an input. This interface requires synchronous host signals.
Reset aborts execution and makes the output high, preserving program memory.
See the specification for priorities, invalid instructions and pause behavior.

Generate program words:

```sh
.venv/bin/python programs/encode.py uart --bit-cycles 8
.venv/bin/python programs/encode.py pulse
```

`programs/uart8.hex` and `programs/pulse.hex` are examples. The public-port
loader is `Bench.load` in `test/test.py`: two input writes per word. No test
preloads internal memory. Physical silicon programmability is still unverified.

## Tangible UART demonstration: byte 65, “A”

UART 8N1, least significant bit first, eight clocks per bit. Each column
below spans eight clocks; `_` means low and `‾` means high:

```text
slot:      start d0 d1 d2 d3 d4 d5 d6 d7 stop
expected:    _   ‾  _  _  _  _  _  ‾  _   ‾
observed:    _   ‾  _  _  _  _  _  ‾  _   ‾
```

START acceptance is E0, first instruction E1, start bit E2, data bits
E10/E18/E26/E34/E42/E50/E58/E66, stop E74 and completion E82.
[Expected and observed every clock](results/demo-A.json) includes the decoded
byte. [CSV timeline](results/demo-A.csv) records edge, time and both values.
The independent Python checker constructs wire-level 8N1 framing and compares
every clock plus a center-sampled decode. It reads no internal engine state.

## Actual measurements

| Run | Checked transactions | Cycles | Simulation time | Checked/s |
| --- | ---: | ---: | ---: | ---: |
| Short regression | 1,110 | 144,368 | 5.26 s | 211.1 |
| Seeded random campaign | 2,000 | 530,943 | 19.23 s | 104.0 |
| Mapped functional regression | 1,110 | 144,368 | 10.28 s | 107.9 |

These initial measured runs all passed. Build time is recorded separately in
each JSON (cached builds are much shorter). The short run contains 1,109 UART
messages and one pulse transaction; its 32 instruction traces are separate
from transaction counts. Reset-aborted frames are not counted as completed.

The campaign seed is 20261003. At its measured workload, 100,000 messages
would take approximately 16 minutes and one million about 2.7 hours, excluding
initial setup/build. These are extrapolations, not completed campaigns.
No large architecture search or million-message run was launched.

All records under `results/` contain source hashes, tool versions, seed,
completed/checked transactions, failures/timeouts, cycles, build and simulation
wall times, throughput and available peak process memory. Content hashes
identify local work beyond the upstream Git revision. Different instruction
mixes and bit durations have different throughput. One cocotb test function
contains many transactions; test functions, transactions and cycles differ.

## Synthesis and physical flow

```sh
.venv/bin/python scripts/setup_flow.py
.venv/bin/python scripts/physical.py synth
.venv/bin/python scripts/run.py short --source results/mapped-netlist.v --cell-model .tools/pdk-lib/sg13cmos5l_functional.v --name mapped-short
```

Standalone Yosys/ABC mapping using the pinned CMOS5L typical 1.20 V/25 C
library produced **1,782 cells, 46,939.662 µm²**. The memory becomes flip-flops
and multiplexers. This is a preliminary cell-area estimate, not an official
physical signoff result. Clock tree, placement, routing, wire delays, voltage
corners and timing closure are not included. The mapped regression uses the
PDK logic/UDP tables with timing-only specify blocks removed and delayed
inputs connected directly, because Icarus rejects this revision's edge-sensitive
`ifnone` paths. It proves zero-delay behavior only.

The actual official workflow was attempted:

```sh
.venv/bin/python scripts/physical.py layout
```

It failed with **“No compatible container engine found.”** The full PDK is
also absent, and this Mac had under 250 MiB of free disk space during setup.
No routing result, timing slack or completed GDS is claimed. After arranging
sufficient disk space and an approved local Docker/Podman installation:

```sh
.venv/bin/python scripts/setup_flow.py --full-pdk
.venv/bin/python scripts/physical.py layout
```

The wrapper invokes the official `tt_tool.py --create-user-config --ihp` and
`--harden --ihp`; it does not substitute a physical configuration. The
unchanged `.github/workflows/gds.yaml` also defines official GDS, precheck and
post-layout tests, but nothing was published or dispatched to GitHub.
Pinned support/action/PDK revisions are recorded in `docs/progress.md`.

The next milestone is a completed official physical build and timing report.
# Visual experiment bench

Run `python3 scripts/simulator.py` and open <http://127.0.0.1:8766/simulator/>.
The 2D bench shows instruction execution, engine state and pin timing. Explore
UART/pulse programs, compare deliberate faults and keep notes in a browser
notebook. Recorded RTL verification results are shown separately from the
teaching model. See [simulator/README.md](simulator/README.md) for details.

## Ten-million-check architecture campaign

The bench now includes a real RTL campaign panel with start, pause, resume,
committed progress, four candidate designs, coverage and measured area results.
The campaign compares 16/32-word memories with and without SHIFTWAIT while
preserving the original source. See [the campaign specification](docs/campaign.md).

```sh
.venv/bin/python scripts/campaign.py init
# Use the printed campaign ID below:
.venv/bin/python scripts/campaign.py start CAMPAIGN_ID
.venv/bin/python scripts/campaign.py status CAMPAIGN_ID
.venv/bin/python scripts/campaign.py pause CAMPAIGN_ID
.venv/bin/python scripts/campaign.py replay CAMPAIGN_ID B holdout 123 --waves
```

`start` also resumes an existing campaign. The worker uses immutable input
snapshots, one low-priority simulation process, and a transactional ledger.
Closing the web page does not stop it. Physical-flow prerequisites remain
separate; missing routing/timing evidence produces only a provisional leader.
Qualification, acceptance campaigns and diagnostic replays are excluded from
the ten-million-check count.

Audit the committed ledger independently without interrupting the worker:

```sh
.venv/bin/python scripts/audit_campaign.py CAMPAIGN_ID
# Require every stage budget and the current finalists' allocations to be complete:
.venv/bin/python scripts/audit_campaign.py CAMPAIGN_ID --require-complete
```

The audit reconstructs scenario digests and coverage from the frozen generator,
checks contiguous ranges and disqualification boundaries, verifies qualification
and source provenance, and writes `ledger-audit.json`. An incomplete campaign
cannot pass `--require-complete`. Physical eligibility is reported separately.
The bench links the most recent audited snapshot and its exact checked count.
