# Incumbent reference designs

Open-source "incumbents" to benchmark the chipwheel pin engine against, wrapped
as synthesizable tops and measured in Icarus simulation. Each one loads its
program through its own host ports and is checked by decoding the pin waveform.
Area and timing come from `scripts/arena.py`, not from here. The generic Yosys
cell counts below only show that each top synthesizes and is not optimized away.

All numbers are from `python3 incumbents/run_all.py`. Raw data is in
`results.json` and `work/measurements.json`.

## Summary

| name | top | what it is | memories | UART clk/bit | UART clk/byte (sustained) | UART program | SPI clk/bit avg (min–max) | SPI clk/byte (sustained) | SPI program | generic cells (flat) / flops |
|---|---|---|---|---|---|---|---|---|---|---|
| `pio_sm1` | `inc_pio_sm1` | 1 RP2040-style PIO state machine (fpga_pio) | 32×16 instr mem; TX + RX FIFO 4×32 each | **1** | **10** | 10 words / 160 b | **2** (2–2) | **19** | 19 words / 304 b | 10,618 / 1,163 |
| `pio_full` | `inc_pio_full` | full upstream 4-SM PIO block (secondary) | 32×16 shared; 4×(TX+RX 4×32) | 1 | 10 | 10 / 160 | 2 | 19 | 19 / 304 | 40,864 / 2,972 |
| `serv_bb` | `inc_serv_bb` | SERV (W=1, bit-serial RV32I) bit-banging GPIO | prog 32×32; RF 32×32 (512×2) | **68** | **1,291** | 23 words / 736 b | **422.7** (379–481) | **3,746** | 26 words / 832 b | 5,739 / 2,181 |
| `qerv_bb` | `inc_qerv_bb` | SERV with W=4 ("QERV"), same SoC (extra) | prog 32×32; RF 32×32 (128×8) | 20 | 372 | 23 / 736 | 127.9 (115–145) | 1,130 | 26 / 832 | 5,177 / 2,216 |
| `femtorv_bb` | `inc_femtorv_bb` | FemtoRV32 Quark (RV32I) bit-banging GPIO | prog 32×32; RF 31×32 | **3** | **72** | 23 words / 736 b | **27.6** (25–31) | **240** | 26 words / 832 b | 7,746 / 2,194 |

Every case above passes. UART sends 0x41 0x55 0xA5 0x00 0xFF 0x5A 0x3C 0x81 and
decodes them from the pin. SPI exchanges 8 bytes full-duplex with a mode-0
slave model: MOSI is checked at the slave and MISO at the host. Each primary
case was also re-run on the flattened Yosys netlist and gave the same bytes and
the same clock counts (`gate_level_sim` in `results.json`).

Other program variants that were measured (all pass except the deliberate negative case):

| incumbent | case | clk/bit | clk/byte | words | note |
|---|---|---|---|---|---|
| PIO | `uart_loop_2cpb` | 2 | 20 | 4 | pico-examples `uart_tx` loop with every delay removed |
| PIO | `spi_3cpb` | 3 | 26 | 18 | textbook `spi_cpha0` with `[1]` between `out` and `in` |
| PIO | `spi_2cpb_negative` | 2 | 18 | 18 | **fails**: MISO is read one bit late (see the PIO notes) |
| SERV / QERV / Femto | `uart_pairs` | 136 / 39 / 8 | 1,529 / 440 / 90 | 27 | `sw` + `srli` per bit |
| SERV / QERV / Femto | `spi_unrolled` | 379 / 115 / 25 | 3,303 / 999 / 213 | 71 | needs ≥ 71 program words (simulated with `PROG_WORDS=72`) |

## Upstream sources, licenses, configuration

| incumbent | upstream | commit | license | files used |
|---|---|---|---|---|
| PIO | https://github.com/lawrie/fpga_pio | `f38be97cdfa86d4551c96bb98599e3443000628b` | BSD-2-Clause (`upstream/fpga_pio/LICENSE`) | `src/*.v`; `pio.v` and `machine.v` patched (below) |
| SERV, QERV | https://github.com/olofk/serv | `f200eb2ed7b69ac1c6b8eddd47654522aeee5ce8` | ISC (`upstream/serv/LICENSE`) | `rtl/*.v`, **unmodified** |
| FemtoRV | https://github.com/BrunoLevy/learn-fpga (`FemtoRV/RTL/PROCESSOR/femtorv32_quark.v`) | `5c08c870315c09ccd9ec64ccde20ab3375b3f273` | BSD-3-Clause (`upstream/femtorv/LICENSE`) | patched (below) |

Pristine upstream copies are in `upstream/`. Patched files and their diffs are
in `pio_sm1/patched/` and `femtorv_bb/patched/`. Repositories were fetched with
`git clone --depth 1`; only the FemtoRV file and its LICENSE were downloaded.

### `inc_pio_sm1` / `inc_pio_full` (fpga_pio)
- Upstream `pio` is instantiated with `NUM_MACHINES=1` or `4`. One SM keeps
  everything upstream builds around it:
  - the 32×16 writable instruction memory
  - the 24-bit (16.8) clock divider
  - X, Y, ISR, OSR, PC, delay counter, side-set and pin-group logic
  - **TX and RX FIFOs of 4×32 bits each** (upstream `fifo.v`)
  - the configuration registers and the GPIO coalescing registers
- 8 GPIOs are brought out (`gpio_in/out/oe[7:0]`). Upstream's other 24 are
  tied off and trimmed by synthesis.
- The host port is upstream's action bus: `action[3:0]`, `index[4:0]`,
  `din[31:0]`, `dout[31:0]`, `tx_full`, `rx_empty`, plus `mindex` on `pio_full`.
  Upstream quirks of that bus:
  - FIFO pushes and immediate instructions sample `din` one clock after the action, so the testbench holds `din` for 2 clocks.
  - `dout` is valid for one clock after PULL.
- Measured at **clkdiv = 1.0** (`DIV=0x100`, divider bypassed).
- Patches (`pio_sm1/patched/fpga_pio.patch`). The other 7 files are byte-identical upstream.
  1. *Yosys:* `pio.v` drives `gpio_out`/`gpio_dir`/`*_prev` from two always
     blocks. Yosys "resolves" that conflict to the constant reset value, which
     tied **every GPIO output to 0**. The reset now lives in the single driving block.
  2. *Yosys:* `machine.v` has the same problem for `output_pins`/`pin_directions`. Fixed the same way (reset/restart has priority).
  3. *ASIC:* the one-cycle strobes `push`/`pull`/`imm`/`restart`/`clkdiv_restart`
     were only cleared outside reset. After power-up they could fire one
     spurious FIFO push, immediate instruction or restart. They are now cleared
     every cycle. `use_divider` is reset together with `div`.
  4. *ASIC:* `exec1` was only initialised by a `reg = 0` initialiser. It is now cleared on reset/restart.

  Without patches 1–2 the netlist would be almost empty. Without 3–4 the gate-level simulation X-propagates.

### `inc_serv_bb` / `inc_qerv_bb` (SERV)
- Core configuration: upstream `serv_rf_top` (`serv_top` + `serv_rf_ram_if` + `serv_rf_ram`) with:
  - `W=1` (QERV: `W=4`)
  - `WITH_CSR=0`: no CSRs or interrupts, the smallest legal setting; the firmware needs none
  - `COMPRESSED=0`, `MDU=0`, `PRE_REGISTER=1`, `RESET_STRATEGY="MINI"`, `RESET_PC=0`
- **ISA is RV32I.** SERV has no RV32E option. The RF is 32×32 bits in a plain
  Verilog array: 512×2 for W=1, 128×8 for W=4.
- Program memory is **32×32 bits** (`PROG_WORDS=32`, 1024 b): a plain reg array,
  host-writable through `prog_we`/`prog_addr`/`prog_wdata` while `rst=1`, with a
  combinational read. 32 words is the smallest power of two that fits both the
  UART program (23 words) and the 2×-unrolled SPI loop (26 words).
- The SoC is Harvard and **zero-wait-state**: ibus and dbus acks equal `cyc`.
  That is one cycle per access faster than upstream servant's registered ack.
  The dbus reaches I/O only (no data RAM); the I/O map is shared with Femto (below).
- QERV is an extra data point that was not requested: upstream's fastest SERV-family option.

### `inc_femtorv_bb` (FemtoRV32 Quark)
- Options are set through upstream macros in `femtorv_bb/femtorv_cfg.v`:
  - `NRV_IS_IO_ADDR(addr)=0`: the I/O is zero-wait, so stores take 3 cycles instead of 4.
  - `NRV_COUNTER_WIDTH=1`: the firmware does not use RDCYCLE; upstream documents this knob for space-constrained designs.
- `ADDR_WIDTH=8`: the minimum that covers 32 program words plus the I/O region.
- **ISA is RV32I.**
- Same 32×32 program memory and I/O map as SERV. Quark has a single shared
  memory port, so I/O is selected by `addr[7]`. I/O read data is captured on
  `mem_rstrb`, like a synchronous RAM.
- Patches (`femtorv_bb/patched/femtorv32_quark.patch`):
  1. *Icarus:* Icarus 13 rejects use-before-declaration, so declarations were moved ahead of their first use. Logic is unchanged.
  2. *ASIC:* reads of x0 are forced to 0 and the dead x0 storage is dropped
     (`registerFile [31:1]`). Upstream never writes x0 and relies on FPGA
     power-up init (or the `BENCH` initial block). In the generic netlist x0 was
     a never-written 32-bit register, i.e. random in silicon; the gate-level
     simulation confirmed this.
- `BENCH` (upstream's simulation-only initial block) is defined for RTL
  simulation only. The gate-level run initialises `aluShamt` instead. Quark
  never resets it; that is harmless in silicon because it counts down to 0.

### Common CPU I/O map (x0-relative addresses, so no base register is needed)
| addr | register | |
|---|---|---|
| -16 | GPIO_OUT[7:0] | R/W, whole-register writes |
| -12 | GPIO_IN[7:0] | R |
| -8 | TX mailbox `{valid, 23'b0, data[7:0]}` | R; a read clears `valid`. Host writes with `host_tx_we`; `host_tx_full = valid` (1-deep FIFO) |
| -4 | RX mailbox | W: sets `host_rx_valid`; the host clears it with `host_rx_re` |

Pins:
- UART TX = `gpio_out[0]`.
- SPI on the CPUs: SCK = `gpio_out[0]`, MOSI = `gpio_out[7]`, MISO = `gpio_in[0]`.
- SPI on PIO: SCK = side-set pin 1, MOSI = out pin 0, MISO = in pin 0.

## Programs
Listings with encodings are in `programs/`: `pio_*.lst` and `rv32i_*.lst`.
The RISC-V programs are hand-assembled by `common/rvasm.py`, which has a
self-test (`python3 incumbents/common/rvasm.py`); the PIO encoders are in
`common/pioasm.py`.

- **PIO UART** (`.side_set 1 opt`, 10 words, wrap 9→0):
  `pull block side 1` (stop bit / idle), `nop side 0` (start bit), then 8× `out pins, 1`.
  Every bit is one instruction, and the pull costs nothing while the FIFO has data.
- **PIO SPI** (`.side_set 1`, 19 words, wrap 18→0):
  `pull side 0`, `out side 0`, `nop side 1`, 7× (`out side 0`, `in side 1`),
  `in side 0`, `push side 0`. The GPIO output path in upstream has one more
  register (`output_pins` → `gpio_out`) than the input path. An `in` therefore
  sees the pins as they were two instructions earlier, i.e. it samples MISO in
  the previous bit's SCK-high phase. Hence:
  - bit 7's high slot is a plain `nop`;
  - one extra `in` follows the last pulse;
  - the plain 2-clock `spi_cpha0` program reads MISO one bit late (the negative case).

  Autopull/autopush are not used: upstream autopush only fires at the *next*
  `in`, so the last RX byte would stay in the ISR.
- **CPU UART** (23 words):
  - poll the mailbox at the bottom of the loop (`lw` + taken `blt`);
  - precompute d1..d7 with seven chained `srli …, 1`;
  - then 10 identical `sw` (start, d0..d7, stop).

  The bit period is exactly one store (SERV 68, QERV 20, Femto 3 clocks). The
  stop bit is stretched by the next poll and precompute, and that is included in clk/byte.
- **CPU SPI** (26 words): 8 instructions per bit, all one-stage ALU ops apart
  from the memory accesses: `andi` MOSI, `sw` (SCK low), `ori`, `sw` (SCK high),
  `lw` GPIO_IN, `andi`, `add`, `or`. The loop is unrolled 2× with a counter, so
  bit spacing is not uniform (the min–max in the table); SPI tolerates that.

## How to re-run
From the repo root (Icarus at `.tools/icarus-verilog/13.0`, Python 3, no other dependencies):

```sh
python3 incumbents/run_all.py                 # all sims + Yosys synth + gate-level re-sims (~1.5 min)
python3 incumbents/run_all.py --no-synth      # RTL simulations only (~10 s)
python3 incumbents/run_all.py --only femtorv_bb serv_bb
python3 incumbents/run_all.py --results-only  # rebuild results.json from work/measurements.json
python3 incumbents/common/rvasm.py            # assembler self-test
```

`run_all.py` writes the program, TX and slave hex files into
`work/<name>_<case>/`, compiles, runs, and decodes `sim.log`. The exact
`iverilog`/`vvp` command for each case is the `rerun` field in
`work/measurements.json`. For example (after one `run_all.py` run, which creates the hex files):

```sh
.tools/icarus-verilog/13.0/bin/iverilog -o incumbents/work/pio_sm1_uart_1cpb/sim.vvp -s tb \
  -I incumbents/common -I incumbents/pio_sm1/tb \
  incumbents/pio_sm1/patched/pio.v incumbents/pio_sm1/patched/machine.v \
  incumbents/upstream/fpga_pio/src/{decoder,divider,pc,scratch,fifo,isr,osr}.v \
  incumbents/pio_sm1/top.v incumbents/common/spi_slave_model.v incumbents/pio_sm1/tb/tb.v
(cd incumbents/work/pio_sm1_uart_1cpb && ../../../.tools/icarus-verilog/13.0/bin/vvp -n sim.vvp \
  +plen=10 +nbytes=8 +grps=44100000 +pend=40009000 +shift=00080000 +div=00000100 +imm=e081 \
  +txshift=0 +spi=0 +maxcyc=20000 +tail=100)
```

Generic synthesis (the source lists are in `results.json`, in compile order):

```sh
incumbents/yosys.sh -p "read_verilog -sv <sources>; synth -flatten -top inc_pio_sm1; stat"
```

**Yosys hang on this Mac:** plain `yowasp-yosys` preopens every directory
under `/`, and opening `/home` (autofs) blocks forever at 0% CPU.
`incumbents/yosys.sh` is a drop-in wrapper that skips `/home` (relative paths
still work). The alternative is
`YOWASP_MOUNT=/Users=/Users:/private=/private` with absolute paths.

## Measurement method and fairness
- **Testbench.**
  - The clock is 10 MHz. Every change of `gpio_out` is logged with its cycle number.
  - Decoders in `run_all.py`:
    - UART: the bit period P is the minimum interval between line transitions. Every transition inside a frame must fall on a multiple of P. Start and stop bits are checked and bytes are compared.
    - SPI: clk/bit is the SCK rising-edge spacing within a byte. MOSI must never change on a rising edge.
  - Sustained clk/byte is the steady-state interval between frame starts (UART) or between the first SCK rising edges of consecutive bytes (SPI), with the host keeping the FIFO/mailbox full.
- **PIO:**
  - clkdiv 1, programs unrolled within its own 32-word memory.
  - Host-side FIFO handling is never the bottleneck: a push takes 2 clocks against 10 clocks per byte.
  - On the RP2040 itself a 2-flop input synchronizer changes the I/O pipeline. Here upstream has none, matching chipwheel's spec (no synchronizers).
- **CPUs, given their best reasonable setup:**
  - zero-wait-state memory and I/O, combinational instruction read;
  - Femto stores without wait states (`NRV_IS_IO_ADDR=0`);
  - the fastest correct bit-bang code I could write. Bottom-of-loop polling, chained 1-bit shifts, and `add`/`andi` instead of `slli`/`slt` cut clk/byte by 6–25 % versus a first draft (Femto UART 96 → 72, SERV SPI 4,392 → 3,746).
- **Per-byte overhead of the CPUs** includes reading and clearing the host
  mailbox (`lw` + `blt`). The mailbox is 1 deep, where PIO has 4-word FIFOs; it
  never stalls in these runs.
- **Program memory size drives CPU area.** A 32×32 program memory plus a 32×32
  register file is ~2,048 flops, and that dominates the generic cell counts.
  Fully unrolled SPI would need 72+ words, so it is reported only as an
  alternative. SERV's RF also keeps x0 storage, which upstream masks on read.
- **Generic cell counts are pre-liberty Yosys gates.**
  - `generic_cells` uses `synth -flatten` (same as the arena recipe), so unused upstream logic (24 tied-off PIO pins, unused config bits) is trimmed.
  - `generic_cells_hier` is the plain `synth -top`: 14,647 for `pio_sm1`, 5,912 for `serv_bb`, 6,984 for `femtorv_bb`. It cannot trim across module boundaries.
  - Both parse with plain `read_verilog` as well as `-sv`.
- **Host interfaces are wider than chipwheel's Tiny Tapeout pins:**
  - PIO: 32-bit `din`/`dout`;
  - CPUs: 32-bit `prog_wdata`.

  Ports cost no cells, but an 8-bit TT wrapper would add a small deserializer to each incumbent.

## Files
```
incumbents/
  README.md  results.json  run_all.py  yosys.sh  .gitignore
  common/      rvasm.py pioasm.py spi_slave_model.v tb_cpu.vh
  programs/    pio_*.lst rv32i_*.lst
  upstream/    pristine upstream sources + LICENSEs (fpga_pio, serv, femtorv)
  pio_sm1/     top.v  tb/tb.v  patched/{pio.v,machine.v,fpga_pio.patch}
  pio_full/    top.v  tb/tb.v (includes pio_sm1/tb/tb.v with FULL)
  serv_bb/     top.v  tb/tb.v
  qerv_bb/     top.v  tb/tb.v (wraps inc_serv_bb with W=4)
  femtorv_bb/  top.v  femtorv_cfg.v  tb/tb.v  patched/{femtorv32_quark.v,.patch}
  work/        generated: per-case logs/hex, netlists, measurements.json (git-ignored except the json)
```
