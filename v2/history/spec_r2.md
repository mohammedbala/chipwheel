# Chipwheel v2 (CW2) behavioral specification

Written before the v2 RTL, 2026-10-03. Goal: a much smaller and faster
protocol engine than v1 that also reads pins, drives open-drain buses and
streams data. v1 (`src/project.v`, `docs/spec.md`) is unchanged.

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

`uio_oe[7:4]` and `uio_out[7:4]` are 0. `ena` is ignored. `rst_n` is an
asynchronous active-low reset. All state changes on rising `clk` except the
instruction/config memory, which is a latch array written through clock gates.

P0..P3 inputs pass through two-flop synchronizers (`pa`, `pb`). WAIT,
clock-stretch checks and status use `pb` (pad value two edges earlier). Shift
captures (`in`) use `pa` (one edge earlier): protocol sample points sit mid-bit,
away from transitions, and the shorter latency allows full-duplex SPI at clk/2. `HSTB` is likewise
synchronized (`s1`,`s2`) with a third flop `s3` for edge detection.
`D`, `HTAG`, `HCMD` are not synchronized: the host must hold them stable from
`HSTB` rising until 3 clocks after `HSTB` falls. `HSEL` only steers the output
multiplexer (combinational).

## Memory

20 x 8-bit words, undefined at power-up: words 0..15 instructions, then

| Word | Name | Meaning |
|---|---|---|
| 16 | `DIV` | one tick = `DIV+1` clocks |
| 17 | `MODE` | 2 bits per pin, `[2i+1:2i]` for Pi: 00 input, 01/11 push-pull, 10 open-drain |
| 18 | `CFG` | `[3:0]` IDLE pin values, `[4]` INSEL, `[5]` MSB, `[6]` CLKD, `[7]` PH3 |
| 19 | `WRAP` | `[3:0]` WRAP_TOP, `[7:4]` WRAP_TGT |

Pad drive (all gated by `PEN`, which reset clears and RUN/IDLE sets):
push-pull: `oe=1, out=OUT[i]`; open-drain: `oe=!OUT[i], out=0`; input: `oe=0`.

## Host interface

A **strobe event** occurs on the clock where `s2 & !s3` (HSTB rose ~2 clocks
earlier). Every strobe event clears `RXV`.

Command channel (`HCMD=1`), executed at a strobe event:
- `HTAG=1`: memory write `MEM[WPTR] <= D`, `WPTR <= WPTR+1`, only while
  stopped (ignored while running; WPTR >= 20 writes nothing).
  The latch captures `D` during the high phase after the event edge.
- `HTAG=0`, `D[6:4]`: 000 RUN at entry `D[3:0]` (PC <= entry, RUN <= 1, PEN <= 1,
  sub-state cleared, first step on the next clock; legal while running = redirect),
  001 STOP, 010 IDLE (`OUT <= IDLE`, PEN <= 1), 011 WPTR <= 0, 100 clear OVR.
  Others: no effect (besides clearing RXV).

Data channel (`HCMD=0`), 4-phase handshake: data is *available* when
`s2 & !HCMD & !TXACK`. A pull consumes `{D, HTAG}` and sets `TXACK`; `TXACK`
clears whenever `s2=0`. Host: drive D/HTAG, raise HSTB, wait TXACK=1, lower
HSTB, wait TXACK=0.

Receive: a push loads `RXD`, sets `RXV`; if `RXV` was already set, `OVR` is set
(sticky). Host reads `RXD` with `HSEL=1`, then any strobe event clears `RXV`.

## Engine timing

`RUN` enables execution. The tick counter `DIVC` decrements each clock; a
*step* happens on clocks where `DIVC=0` and the instruction state advances.
After a step that completes or makes progress, `DIVC <= DIV`. If the current
instruction is **stalled** (WAIT false, pull with no data, clock stretched),
nothing changes and `DIVC` stays 0, so the condition is re-checked every clock
and the instruction proceeds on the first clock it is satisfied.
RUN sets `DIVC <= 0`.

`advance`: `PC <= (PC==WRAP_TOP) ? WRAP_TGT : PC+1` (mod 16). Taken branches
set PC directly. Every instruction takes at least one step.

## Shift register

`SR` is 9 bits. Pull loads `SR <= {D[7:0], HTAG}`. `MSB=1`: out bit `SR[8]`,
shift `SR <= {SR[7:0], in}`. `MSB=0`: out bit `SR[0]`, shift
`SR <= {in, SR[8:1]}`. `in` = synchronized `INSEL ? P0 : P2`. Each capture also
sets `F <= in`. A push of an `n`-bit SHIFT selects `RXD = (MSB ^ (n==9)) ?
SR[7:0] : SR[8:1]` (the first 8 captured bits for n = 8 or 9); the PUSH
instruction uses the n=8 selection.

## Instructions (8 bits)

| Encoding | Name | Effect |
|---|---|---|
| `00 cc tttt` | BR | cc=00 always; 01 if X!=0 {X--, jump}; 10 if F==0; 11 if F==1. Else advance |
| `01 pp v ddd` | SET | `OUT[pp] <= v`; then `ddd` idle steps |
| `10 nnnn d a` | SHIFT | n = nnnn+1 bits. d: pull before first bit (stall until available) and drive P0. a: push after |
| `11 00 kkkk` | LDX | `X <= k` |
| `11 01 pp v x` | WAIT | stall until `P[pp]==v` (x reserved, write 0) |
| `11 10 x ooo` | MISC | 000 PULL, 001 PUSH, 010 HALT (RUN <= 0), 011 TST (`F <= out bit`, shift in 0), others NOP |
| `11 11 dddd` | DELAY | `dddd` idle steps after its first step |

SHIFT with `CLKD=0` (plain): n steps; each step drives P0 (if d) with the
current out bit, then shifts/captures. First step with d pulls first and uses the
pulled value (stall happens before any pin change).

SHIFT with `CLKD=1` (clocked, clock pin P1, CPOL = IDLE[1] when PH3=0, else 0): per bit j the steps
are A_j (P1 <= CPOL; if j>1 shift/capture; if !PH3 drive the post-shift out bit),
then M_j only when PH3 (drive out bit), then B_j (P1 <= !CPOL); after bit n a
tail step T (shift/capture only; the clock pin keeps its active level, so
programs restore the idle level with SET when needed). So n bits take 2n+1 (or 3n+1) steps
and capture happens one step after the active clock level (bit centre for
UART receive with tick = half a bit). If P1 is open-drain, the step after a B
stalls until synchronized P1 == !CPOL (clock stretching).

## Reset

Async reset: RUN=0, PEN=0 (all pads released), PC/SUB/phase/X/F/SR/DIVC/OUT=0,
TXACK/RXV/OVR=0, WPTR=0, synchronizers 0. Memory latches keep their values.
