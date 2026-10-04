## How it works

Chipwheel is a tiny programmable protocol engine. A host loads a program of up
to 10 eight-bit instructions plus 3 configuration bytes, and the engine then
drives and samples four bidirectional pins (P0–P3) with exact timing: UART
transmit and receive, SPI master (all four modes, full duplex at SCK = clk/2),
I2C master with open-drain pins, ACK/NACK and clock stretching, and timed
pulse trains. New protocols only need new programs.

One instruction step happens per *tick* (`DIV+1` clocks). Instructions:

| Encoding | Name | Effect |
|---|---|---|
| `00 cc tttt` | BR | jump to `t`: always / if X≠0 (decrement X) / if F=0 / if F=1 |
| `01 pp v ddd` | SET | pin `pp` ← `v`, then `ddd` idle ticks |
| `10 nnnn d a` | SHIFT | shift `n+1` bits through a 9-bit shift register; `d`: take the next host byte and drive P0; `a`: hand the received byte to the host |
| `11 00 kkkk` | LDX | X ← `k` |
| `11 01 pp v 0` | WAIT | wait until pin `pp` reads `v` |
| `11 10 0 ooo` | MISC | PULL, PUSH, HALT, TST (F ← next out bit) |
| `11 11 dddd` | DELAY | `dddd` idle ticks |

Shifts are plain (one bit per tick, e.g. UART) or clocked on P1 (two or three
ticks per bit, e.g. SPI and I2C). A free loop (`WRAP_TOP`) and a 9th "tag" bit
supplied with each host byte make UART transmit a 2-instruction program
(start bit + 8 data bits from one SHIFT, then the stop bit) that streams at one
bit per clock. The program store is a clock-gated latch array, which keeps the
whole design to about 14,000 µm² of standard cells.

Full specification: `docs/spec.md`.

## How to test

Host pins: `ui_in` = data byte D; `uio[4]` HSTB strobe, `uio[5]` HTAG,
`uio[6]` HCMD (1 = command, 0 = data), `uio[7]` HSEL (0 = status on `uo_out`,
1 = received byte). Hold D/HTAG/HCMD stable from raising HSTB until 3 clocks
after lowering it. Status bits: `uo[0]` RUN, `uo[1]` TXACK, `uo[2]` RXV,
`uo[3]` overrun, `uo[7:4]` pin inputs.

1. Command `0x30` (HCMD=1, HTAG=0) resets the write pointer.
2. Write 13 bytes with HCMD=1, HTAG=1: 10 instructions, then DIV, PADS
   (`[3:0]` idle pin levels, `[7:4]` open-drain mask), CFG (`[0]` capture from
   P0, `[1]` MSB first, `[2]` clocked, `[3]` 3-phase, `[7:4]` wrap address).
3. Command `0x00 + entry` starts the program at that entry (RUN). Send data bytes with HCMD=0: raise HSTB,
   wait for TXACK=1, lower HSTB, wait for TXACK=0. Read received bytes when
   RXV=1 by setting HSEL=1; any strobe acknowledges.

Example, UART TX at 1 bit per tick: program `0xA2 0x48`, DIV = clocks per
bit − 1, PADS `0xEF`, CFG `0x10`; send each byte with HTAG=0. Ready-made
programs and an assembler are in `v2/programs` and `v2/model/cw2.py`.
`make` in `test/` runs UART, SPI and I2C scenarios against independent device
models.

## External hardware

Whatever protocol device you want to talk to: a UART adapter on P0/P2, an SPI
device on P0–P3, or an I2C device on P0 (SDA) and P1 (SCL) with pull-up
resistors. The host can be the Tiny Tapeout demo board's RP2040.
