# Chipwheel v2 (CW2) behavioral specification — revision r6 (Tiny Tapeout entry)

First written before the v2 RTL on 2026-10-03 (kept as
`v2/history/spec_r2.md`), then revised as area work changed the design. This
text matches `src/project.v` and the reference model `v2/model/cw2.py`.
v1 is archived in `v1/` (RTL, config and its specification `v1/docs/spec.md`).

## Pins

| Pin | Use |
|---|---|
| `ui_in[7:0]` | host data byte `D` |
| `uio[3:0]` | protocol pins P0..P3 (bidirectional, engine-driven `uio_oe`) |
| `uio[4]` in | `HSTB` host strobe |
| `uio[5]` in | `HTAG` tag bit (data channel) / memory-write select (command channel) |
| `uio[6]` in | `HCMD` 0 = data channel, 1 = command channel |
| `uio[7]` in | `HSEL` output select: 0 = status, 1 = received byte |
| `uo_out[7:0]` | `HSEL=0`: `{P3,P2,P1,P0, OVR, RXV, TXACK, RUN}`; `HSEL=1`: `RXD` |

`uio_oe[7:4]`, `uio_out[7:4]` are 0. `ena` is ignored. `rst_n` is an
asynchronous active-low reset. All state changes on rising `clk` except the
program/config memory, a latch array written through per-word clock gates (a
transparent-low latch plus AND gate each; the PDK excludes its integrated
clock-gate cell from the flow).

P0..P3 pass through two synchronizer flops, `pa` then `pb`. WAIT, the
clock-stretch check and the status byte use `pb` (pad value two edges earlier).
Shift captures use `pa` (one edge earlier): protocol sample points sit mid-bit,
away from transitions, and the shorter latency lets full-duplex SPI run at
clk/2. `HSTB` passes through `s1`,`s2`; `s3` remembers `s2` for edge detection.
`D`, `HTAG`, `HCMD` are not synchronized: the host keeps them stable from
`HSTB` rising until 3 clocks after `HSTB` falls. `HSEL` only steers the output
multiplexer.

## Memory (13 x 8 bits, undefined at power-up)

Words 0..9 are instructions; fetching from PC 10..15 returns `0x00` (`BR 0`).

| Word | Meaning |
|---|---|
| 10 `DIV` | one tick = `DIV+1` clocks |
| 11 `PADS` | `[3:0]` IDLE pin values, `[7:4]` OD: 1 = open-drain (an open-drain pin left at 1 is an input) |
| 12 `CFG` | `[0]` INSEL (capture from P0, else P2), `[1]` MSB first, `[2]` CLKD (clocked shifts), `[3]` PH3 (3-phase clocked shifts), `[7:4]` WRAP_TOP |

Pad drive, gated by `PEN` (cleared by reset, set by RUN or IDLE):
push-pull pin: `oe=1, out=OUT[i]`; open-drain pin: `oe=!OUT[i], out=0`.

## Host interface

A **strobe event** is a clock where `s2 & !s3`. Every strobe event clears `RXV`.

Command channel (`HCMD=1`) at a strobe event:
- `HTAG=1`: memory write `MEM[WPTR] <= D`, `WPTR <= WPTR+1` (4 bits), only while
  stopped (ignored while running; WPTR 13..15 writes nothing). The latch
  captures `D` during the clock-high phase after the event edge.
- `HTAG=0`, by `D[6:4]`: 000 RUN at entry `D[3:0]` (if `PEN=0` first set
  `OUT <= IDLE`; then PC <= entry, RUN <= 1, PEN <= 1, busy/phase/release
  flags cleared, `DIVC <= 0`; legal while running, i.e. a redirect);
  001 STOP; 010 IDLE (`OUT <= IDLE`, PEN <= 1); 011 WPTR <= 0; 100 clear OVR;
  others no effect besides clearing RXV.

Data channel (`HCMD=0`), 4-phase handshake: data is *available* when
`s2 & !HCMD & !TXACK`. A pull consumes `{D, HTAG}` and sets `TXACK`; `TXACK`
clears on any clock where `s2=0`.

Receive: a push copies a byte of `SR` into `RXD` **on the following clock**
(deferred push), sets `RXV`, and sets sticky `OVR` if `RXV` was already set and
no strobe event clears it on that clock. The host reads `RXD` with `HSEL=1`;
any strobe event clears `RXV`.

## Engine timing

`DIVC` decrements each clock while running. A *step* happens on a clock with
`DIVC=0` unless the instruction is stalled; a step reloads `DIVC <= DIV`. A
stalled instruction changes nothing and leaves `DIVC=0`, so it is re-checked
every clock and proceeds on the first clock its condition holds.

Stall conditions: WAIT condition false; a pull (PULL, or the first step of a
SHIFT with D) without available data; or **release**: the previous step
released the clock pin P1 (SET P1,1 or a clocked B step with CPOL=0), P1 is
open-drain, and `pb[1]` still reads 0 (clock stretching / synchronization).

`advance`: `PC <= (PC == WRAP_TOP) ? 0 : PC + 1`. Taken branches set PC directly.

## Shift register

`SR` is 9 bits. Pull loads `SR <= {D, HTAG}`. MSB first: out bit `SR[8]`,
shift `SR <= {SR[7:0], in}`. LSB first: out bit `SR[0]`, shift
`SR <= {in, SR[8:1]}`. `in` = `INSEL ? pa[0] : pa[2]`; every capture sets
`F <= in`. A push from an n-bit SHIFT copies `SR[7:0]` if `MSB ^ (n == 9)` else
`SR[8:1]` (the first 8 captured bits for n = 8 or 9); PUSH uses `MSB`.

## Instructions (8 bits)

| Encoding | Name | Effect |
|---|---|---|
| `00 cc tttt` | BR | cc=00 always; 01 if X!=0 {X--, jump}; 10 if F==0; 11 if F==1; else advance |
| `01 pp v ddd` | SET | `OUT[pp] <= v`, then `ddd` idle steps |
| `10 nnnn d a` | SHIFT | n = nnnn+1 bits. d: pull before the first bit (stall until available) and drive P0. a: push after the last bit |
| `11 00 kkkk` | LDX | `X <= k` |
| `11 01 pp v x` | WAIT | stall until `pb[pp] == v` |
| `11 10 x ooo` | MISC | 000 PULL; 001 PUSH; 010 HALT (RUN <= 0, PC kept); 011 TST (`F <= out bit`, shift capturing `in`); others NOP |
| `11 11 dddd` | DELAY | `dddd` idle steps after its own step |

SHIFT, plain (`CLKD=0`): n steps; each drives P0 with the current out bit (if
d; the first step uses the freshly pulled value) and shifts/captures.

SHIFT, clocked (`CLKD=1`, clock pin P1, CPOL = `PH3 ? 0 : IDLE[1]`): per bit
j: A_j (P1 <= CPOL; if j>1 shift/capture; if !PH3 and d drive the post-shift
out bit), M_j only when PH3 (drive the out bit), B_j (P1 <= !CPOL). After bit n
a tail step T shifts/captures; with PH3 it also drives P1 low, otherwise the
clock keeps its level (programs restore the idle level with SET). n bits take
2n+1 steps (3n+1 with PH3); each capture lands one step after the active clock
level began, which is the bit centre for UART receive with tick = half a bit.

## Reset

RUN=0, PEN=0 (all pads released), PC/X/SUB/phase/busy/release/F/SR/DIVC/OUT=0,
TXACK/RXV/OVR/pending push=0, WPTR=0, synchronizers 0. Memory keeps its value.
