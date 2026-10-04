# Chipwheel v3 specification (draft 2, 2026-10-04)

A general programmable protocol engine. Goal: any logic-level digital protocol
that fits 8 pins and edges up to clk/2 becomes firmware. Written before the
v3 RTL; `v3/model/cw3.py` is the executable reference. v2 (`src/project.v`)
remains the entry until v3 is verified and hardened.

Design rules: per-bit work (shifting, clocking, stuffing, line coding, CRC,
timing recovery) is configurable hardware that costs no instructions; per-frame
work (framing, addressing, ACKs, retries) is software. Instruction encoding is
the RP2040 PIO encoding so PIO programs and tools carry over; data registers
are 16 bits wide instead of 32.

## 1. Pins and host bus

| Pin | Use |
|---|---|
| `uio[7:0]` | protocol pins G0..G7 (bidirectional, per-pin output enable) |
| `ui_in[3:0]` | host write nibble |
| `ui_in[4]` | WT: write toggle (each level change writes one nibble) |
| `ui_in[6:5]` | CH: channel |
| `ui_in[7]` | RT: read toggle (each level change consumes one read nibble) |
| `uo_out[3:0]` | current read nibble of channel CH |
| `uo_out[7:4]` | status `{RXV1, TXR1, RXV0, TXR0}` (RX entry valid, TX buffer ready) |

WT and RT pass through two synchronizer flops plus an edge-history flop; an
*event* is a change of the synchronized level. D and CH are sampled at the
event, so the host keeps them stable for 3 clocks after toggling. `uo_out[3:0]`
is a combinational multiplexer on the live CH pins. `ena` is ignored; `rst_n` is
an asynchronous reset.

Write channels: CH=0/1: nibble to SM0/SM1's TX buffer (low nibble first; the
entry completes when the nibble count reaches 2, or 4 when FIFO16). While the entry is complete (TXR=0), more
nibbles are ignored, and so are all nibbles to a TX buffer fed by a link (section 5a). CH=2: command packets of 6 nibbles, high nibble first:
address byte A, then 16-bit data D. CH=3: program words, 4 nibbles high first,
written to `mem[PPTR]`, PPTR+1. CH=2 and CH=3 share one shift register and
nibble counter; a write on the other of the two channels restarts the count.
A completed command or word is applied on the next clock; its latches capture
the shift register during that clock's high phase, so the host must not send
another CH=2/3 nibble event on that clock (legal 3-clock pacing guarantees this).

Read channels: CH=0/1: SM0/SM1 RX entry, low nibble first. The read nibble
pointer is shared by all read channels; a read event on CH=0/1 with a valid
entry pops it when the pointer is at or past the last nibble (1, or 3 when
FIFO16), otherwise advances the pointer. The RX entry of a linked source is
never popped by the host: reads only advance the pointer (mod 4), as a peek. CH=2: status word
`{PC1[4:0], PC0[4:0], EN1, EN0, IRQ[3:0]}`, nibble 0 first, cycling. CH=3 reads 0.

Command address `A[7:4]` = target, `A[3:0]` = register:

| Target | Registers |
|---|---|
| 0, 1 | SM0, SM1 configuration (section 6) |
| 2 | global: G0 `[7:0]` open-drain mask, `[15:8]` pin owner mask (1 = SM1); G1 `[0]` SPAN, `[1]` LINK01, `[2]` LINK10 |
| 3 | actions: 0 ENABLE `D[1:0]`; 1 RESTART SM `D[8]` at PC `D[4:0]`; 2/3 EXEC instruction `D` on SM0/SM1 (only while that SM is disabled); 4 PPTR `D[4:0]`; 5 clear IRQ flags `D[3:0]` |

RESTART clears ISR/OSR, sets the ISR count to 0 and the OSR count to 16 (empty, so
the first OUT autopulls), the delay counter, codec state and
flags, and loads the PC. EXEC executes `D` once on the next clock as if issued
(side-set and pin writes included); an instruction that would stall has no
effect and no delay is applied.

## 2. Program memory

32 x 16-bit latch words in two banks: A = words 0..15, B = 16..31. Each SM has
an instruction register loaded every clock with the word at its next PC (as the
memory held it before that clock), so a word rewritten while it is about to
execute takes effect one clock later. SM1 fetches `B[PC[3:0]]`. SM0 fetches `A[PC[3:0]]`, or
`B[PC[3:0]]` when SPAN=1 and PC[4]=1. While SPAN=1, SM1 does not run.

## 3. Execution timing (per state machine)

A *tick* is when the SM may execute: CLKSRC 0: the divider counter reaches 0
(then reloads DIV, so a tick every DIV+1 clocks); CLKSRC 1/2/3: a rising,
falling or any edge on CLK_PIN (edge = sampled level differs from the previous
clock's). With RESYNC=1, a level change on the IN_BASE pin loads the divider
counter with DIV>>1, centring later ticks on the bit (timing recovery).

On a tick the SM either works off a delay (decrement), or issues the current
instruction. Issuing applies side-set first-cycle values even if the
instruction then stalls. A completed instruction loads the delay counter from
its delay field and advances the PC: `PC <= (PC == WRAP_TOP) ? WRAP_BOTTOM :
PC+1` unless it jumped. A stalled instruction is re-issued on the next tick.
A disabled SM holds state; its pins keep their values.

Input samples: IN_FAST=1 uses the first synchronizer stage, else the second.
Each pin is also sampled on the falling edge and that sample re-registered on
the rising edge (`pnr`). Per clock this gives, in time order, `pb` (rising
edge k-1), `pnr` (falling edge k-1), `pa` (rising edge k). With IN_DDR, `IN PINS`
(not the line unit's single-bit path) takes two samples per pin: the pair
(`pb`, `pnr`), or (`pnr`, `pa`) with IN_FAST. For IN_BASE-rotated pin k, data bit
2k is the earlier sample and 2k+1 the later one (swapped when shifting left), so
the earlier sample always enters the ISR first. `in pins, 2` every clock records
one pin at twice the clock rate. Other pin reads (WAIT, JMP PIN, MOV) are unchanged.

## 4. Instructions (RP2040 PIO encoding)

`[15:13]` opcode, `[12:8]` delay/side-set, `[7:0]` operands. The top SIDE_COUNT
bits of `[12:8]` are side-set (with SIDE_EN the MSB of those is an enable bit);
the rest is delay. Side-set drives SIDE_COUNT(-1 with SIDE_EN) pins from
SIDE_BASE, or their directions when SIDE_PINDIR. Side-set wins over a pin
write by the same instruction.

| Op | Fields | Effect |
|---|---|---|
| JMP 000 | cond `[7:5]`, addr `[4:0]` | always; !X; X-- (jump if X!=0, then X-1); !Y; Y--; X!=Y; PIN (JMP_PIN high); FLAG (JFLAG: 0 !OSRE, 1 CRC==0, 2 TIMEOUT, 3 CODE_ERR; flags 2/3 are cleared when tested) |
| WAIT 001 | pol `[7]`, src `[6:5]`, idx `[4:0]` | src 0 GPIO `idx[2:0]`, 1 PIN `(IN_BASE+idx[2:0])%8`: for these, `idx[3]`=EDGE (level==pol and the previous tick's level != pol), `idx[4]`=TIMEOUT (while waiting, X-- each tick; if X==0 the wait ends with TIMEOUT flag). src 2 IRQ flag `idx[1:0]` (+SM number mod 4 if `idx[4]`), cleared on completion when pol=1. src 3 LINE: pol=1 TX unit idle and queue empty, pol=0 TX unit busy |
| IN 010 | src `[7:5]`, count `[4:0]` (0 or >16 = 16) | src PINS (count pins from IN_BASE, IN_BASE is bit 0; IN_DDR: section 3), X, Y, NULL, CRC, TIME (5: the free-running 16-bit clock counter, 0 at reset, +1 every clock), ISR, OSR. Shift into ISR: right: `ISR = {data, ISR} >> count`; left: `ISR = ISR << count \| data`. Count saturates at 16. Autopush at threshold; if the RX buffer is full the IN stalls before shifting |
| OUT 011 | dest `[7:5]`, count | dest PINS (OUT_BASE..), X, Y, NULL, PINDIRS, PC, ISR (ISR=data, count=count), CRC (CRC=data). Shift out of OSR: right takes the low bits, left the high bits. Autopull: if the OSR count has reached PULL_THRESH, refill from the TX buffer first (stall if empty), then shift in the same tick |
| PUSH/PULL 100 | `[7]` 0 push / 1 pull, `[6]` if-full/if-empty, `[5]` block | as PIO. Non-blocking push with full buffer drops the data and clears ISR; non-blocking pull from empty copies X to OSR |
| MOV 101 | dest `[7:5]`, op `[4:3]`, src `[2:0]` | dest PINS (OUT group), X, Y, CRC, (EXEC = no-op), PC, ISR (count 0), OSR (count 0); op none / invert / bit-reverse / none; src PINS (8 pins rotated from IN_BASE), X, Y, NULL, CRC, STATUS (all ones if TX buffer empty, or if STATUS_SEL: RX buffer full), ISR, OSR |
| IRQ 110 | `[6]` clear, `[5]` wait, `[4]` rel, `[1:0]` flag | set or clear flag (+SM number mod 4 if rel); set+wait stalls until the flag is clear again |
| SET 111 | dest `[7:5]`, data `[4:0]` | PINS (SET_COUNT pins from SET_BASE), X, Y, (4) PINDIRS; others no-op |

FIFO entries are 8 or 16 bits (FIFO16). An 8-bit pull places the byte where
bits leave first: `OSR[7:0]` when shifting right, `OSR[15:8]` when left. An
8-bit push takes the byte where bits entered: `ISR[15:8]` right, `ISR[7:0]` left.
OSR is "empty" when its count >= PULL_THRESH.

## 5. Line unit (serializer / deserializer) and CRC

With LINE_TX or LINE_RX set, the SM issues instructions on every clock
(CLKSRC is ignored) and its divider counter becomes the *line timer*: one
expiry every DIV+1 clocks (a half-bit for Manchester TX). The line unit does
all per-bit work at a uniform rate, decoupled from instruction timing; a
one-bit queue in each direction connects it to the program.

TX (LINE_TX): `OUT PINS, 1` puts the data bit in the TX queue (stalls while
the queue is full) and updates the CRC if CRC_OUT. At each timer expiry the
unit picks the next line bit: a stuff bit if STUFF_N != 0 and the last STUFF_N
bits were identical (ones only, with STUFF_ONES) -- the complement, 0 in ones
mode; else the queued data bit; else nothing (idle: the pin is left alone and
the stuffing run restarts). It drives it on OUT_BASE (and the complement on
OUT_BASE+1 with DIFF) encoded by ENC: NRZ; NRZI (0 toggles the pin's current
level; USB); NRZI1 (1 toggles); Manchester (0 = high then low, 1 = low then
high, one half per expiry). The unit's pin write wins over the instruction's.
`WAIT 1 LINE` (WAIT source 3) waits until the TX unit is idle and its queue
empty; `WAIT 0 LINE` waits until it is busy.

RX (LINE_RX): NRZ/NRZI/NRZI1: a level change on the IN_BASE pin with RESYNC
reloads the timer with DIV>>1; otherwise each expiry samples the pin at the
bit centre, decodes it against the previous centre sample (NRZI: no change =
1), and, unless it is a stuff bit (after STUFF_N identical bits; a wrong stuff
bit sets CODE_ERR), queues it. Manchester (DEC=3): an edge seen while the timer
is 0 is a mid-bit edge: the new level is the bit (rising = 1); it is queued and
the timer reloads DIV (set DIV to about 3/4 bit) so boundary edges are ignored.
`IN PINS, 1` takes the queued bit (stalls while empty) and updates the CRC if
CRC_IN. Queuing onto a full queue overwrites it and sets CODE_ERR. If both
units are enabled they share the RX-driven timer. RESTART empties both
queues, clears the stuffing state, and loads the NRZI reference from the pin.

CRC: 16-bit register, polynomial CRC_POLY written top-aligned (CRC-W uses
the top W bits) or, with CRC_REFLECT, bottom-aligned and reflected.
Normal: `fb = CRC[15] ^ bit; CRC = CRC<<1 ^ (fb ? POLY : 0)`. Reflected:
`fb = CRC[0] ^ bit; CRC = CRC>>1 ^ (fb ? POLY : 0)`. Initialise with MOV CRC.
`CRC==0` is testable by JMP FLAG; residues via MOV X, CRC and JMP X!=Y.

## 5a. SM-to-SM links

G1 LINK01 connects SM0's RX buffer to SM1's TX buffer, LINK10 SM1's RX to SM0's
TX; both may be set. On every clock where the source's RX entry is valid and
the sink's TX buffer is empty, the 16-bit entry moves across: the sink's TX
buffer is loaded and marked full (nibble count 0) and the source's RX entry is
released. The SMs see an ordinary push and pull. Entries cross as 16-bit
values: an 8-bit push (`{00, byte}`) gives an 8-bit sink its byte and a 16-bit
sink `0x00XX`; a 16-bit entry into an 8-bit sink delivers its low byte. Host
writes to the sink's channel are ignored and host reads of the source's channel
do not pop it. One entry takes one clock to cross, so a pipeline runs at up
to one entry per two clocks with no host involvement: one SM terminates
protocol A, the other speaks protocol B.

## 6. Configuration registers (per SM, 16-bit, latched)

| # | Fields |
|---|---|
| 0 CLKDIV | DIV |
| 1 CLKCTRL | `[1:0]` CLKSRC, `[2]` RESYNC, `[5:3]` CLK_PIN, `[6]` IN_FAST, `[7]` IN_DDR |
| 2 PINCTRL | `[2:0]` OUT_BASE, `[6:3]` OUT_COUNT (0..8), `[9:7]` SET_BASE, `[12:10]` SET_COUNT (0..5), `[15:13]` SIDE_BASE |
| 3 PINCTRL2 | `[2:0]` IN_BASE, `[5:3]` JMP_PIN, `[7:6]` SIDE_COUNT, `[8]` SIDE_EN, `[9]` SIDE_PINDIR, `[11:10]` JFLAG, `[12]` STATUS_SEL |
| 4 EXECCTRL | `[4:0]` WRAP_BOTTOM, `[9:5]` WRAP_TOP, `[10]` IN_SHIFTDIR (1 = left), `[11]` OUT_SHIFTDIR (1 = left), `[12]` AUTOPUSH, `[13]` AUTOPULL, `[14]` FIFO16, `[15]` DIFF |
| 5 CODECTRL | `[3:0]` PUSH_THRESH, `[7:4]` PULL_THRESH (0 = 16), `[10:8]` STUFF_N, `[11]` STUFF_ONES, `[12]` LINE_TX, `[13]` LINE_RX, `[15:14]` ENC |
| 6 CODECTRL2 | `[1:0]` DEC, `[2]` CRC_OUT, `[3]` CRC_IN, `[4]` CRC_REFLECT |
| 7 CRC_POLY | polynomial |

Pin drive: pin i is driven by the SM selected by the owner mask; open-drain
pins output 0 with `oe = pindir & !value`, others `out = value, oe = pindir`.

## 7. Reset

All state machines disabled, PCs 0, registers and counters 0 (OSR count 16), pin values 0
and directions input (pads released), IRQ flags 0, host parser idle, PPTR 0,
synchronizers and input samples 0, clock counter 0. Program and configuration latches keep their contents and
are undefined at power-up.
