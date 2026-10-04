# Chipwheel v1 behavioral specification

Written before RTL implementation, 2026-10-03. One engine, fixed 32 x 16-bit
writable instruction memory. No architecture parameters in v1. A program counter
(PC) selects the next instruction; a down-counter repeats a short sequence.
The memory is implemented as registers initially, not an SRAM macro.

## Clock and public interface

Use the unchanged Tiny Tapeout port shape (8 dedicated inputs, 8 dedicated
outputs, 8 bidirectional pins, clk, rst_n, ena). Target clock: 10 MHz (100 ns).
All state changes happen on rising clk edges. Inputs must be synchronous and
stable before the edge; this interface has no synchronizers. rst_n=0 is a
synchronous reset with highest priority, even with ena=0.

- ui_in[7:0]: payload at START; instruction byte during write.
- uio_in[0]: START, accepted on a low-to-high edge while idle and ena=1.
- uio_in[1]: WRITE enable (level), one byte written each enabled idle edge.
- uio_in[2]: byte select, 0=low byte, 1=high byte.
- uio_in[7:3]: instruction address 0..31.
- uo_out[0]: programmable pin, idle high at reset/start/halt/fault.
- uo_out[1]: busy, high from START acceptance through HALT/fault/reset.
- uo_out[2]: sticky error, cleared by reset or an accepted START.
- uo_out[7:3], uio_out, uio_oe: zero. All uio pins are inputs.

Reset aborts transmission immediately at its sampled edge, drives pin high,
clears busy/error, PC, wait/count/data registers and START history. Memory is
preserved and is undefined on power-up. Load complete words before executing;
partial/uninitialized words are invalid usage (no hardware validity bitmap).
No execution happens at the START acceptance edge E0. The payload is copied
from ui_in into an 8-bit shift register, PC=0, counters cleared, busy=1,
pin=1. First instruction executes at E1. START held high never retriggers.
START during busy is ignored and consumed; lower it before a new request.
WRITE during busy is rejected, leaves memory unchanged, sets error, and
execution continues. WRITE and a START edge while idle: write takes priority,
sets error, and consumes START; no execution starts. ena=0 freezes all state
except START history, which still samples every clock to consume disabled
requests. Reset always wins. WRITE while disabled is ignored.

## Instruction encoding and exact timing

Word = opcode[15:12] | unsigned argument[11:0]. Asynchronous memory read,
one instruction per enabled edge unless waiting. No fetch pipeline overhead.
Every instruction consumes one execution edge. Output changes only on OUT,
SHIFT, START, HALT, fault or reset edges. Reserved argument bits must be zero.

| Opcode | Name | Valid argument | Effect at execution edge |
| --- | --- | --- | --- |
| 0 | HALT | 0 | busy=0, pin=1; PC unchanged |
| 1 | OUT | 0..1 | pin=argument; PC advances |
| 2 | WAIT | 0..4095 | PC advances; stall the next N enabled edges |
| 3 | SHIFT | 0 | pin=data bit 0; shift right, zero fill; PC advances |
| 4 | COUNT | 0..255 | loop counter=argument; PC advances |
| 5 | DJNZ | 0..31 | if count>1 decrement and jump; else count=0 and advance |
| 6 | JMP | 0..31 | PC=argument |

WAIT N costs 1+N clocks including its own instruction edge. WAIT 0 costs
one clock, WAIT 4095 costs 4096. If OUT executes at edge k, followed by
WAIT N and another OUT, the output changes at k+N+2. A taken or untaken DJNZ
costs one clock; count=0 and count=1 both fall through. JMP self loops forever
until reset; it is legal. Ordinary advance beyond address 31 is a fault.
A taken branch at address 31 is legal. Undefined opcodes, reserved arguments
and fallthrough overflow stop busy, drive pin high and set error in one edge.
Memory writes are not instructions and occur only while idle.

## UART program

8N1, LSB first, idle high, integer bit duration B in 3..4096 clocks.
COUNT 8; OUT 0; WAIT B-2; SHIFT; WAIT B-3; DJNZ 3;
OUT 1; WAIT B-2; HALT. Nine words, loop at addresses 3..5.
Start bit begins at E2; data bit 0 at E(2+B), bit i at E(2+(i+1)B);
stop bit at E(2+9B); busy clears at E(2+10B). Thus frame occupies
10B clocks, and acceptance-to-completion is 2+10B clocks.
Loading and idle gaps are separate from protocol timing. Busy requests are
not queued. Reset truncates a frame; no valid-byte claim applies to that frame.
Invalid B/payload/address/word requests are rejected by the Python encoder
before any pin writes. Hardware invalid-instruction behavior is defined above.

## Pulse program

OUT 0; WAIT 2; OUT 1; WAIT 4; OUT 0; WAIT 1; HALT.
Edges E1..E14: low at 1, high at 5, low at 11, HALT/high at 14.
Durations: low 4 clocks, high 6 clocks, low 3 clocks. Seven words.


## Loading

For each word, drive its low byte on ui_in, address on uio[7:3],
WRITE=1, byte select=0 for one edge; then high byte, byte select=1
for one edge. Set WRITE=0 and START=0, then supply payload and raise START.
Tests use only these actual ports, never hierarchical memory preloads.
This is verified RTL reprogramming through external inputs; manufactured
silicon operation, metastability handling and electrical timing are unverified.

## Recorded correction

The first program listing used WAIT B-1 for the start/stop bits. The
independent checker caught completion one clock late. The instruction timing
rule (OUT-to-OUT = N+2) and UART waveform requirements stay unchanged;
those two waits are corrected to B-2. See the saved failing regression log.
