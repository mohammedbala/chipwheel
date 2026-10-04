## How it works

Chipwheel v3 is a general programmable protocol engine. Two state machines run
programs written in the RP2040 PIO instruction encoding (16-bit data
registers), so existing PIO programs carry over with small changes. On top of
PIO-style pin control, side-set, delays, autopull/autopush and wrap, each state
machine has hardware for the per-bit work of serial protocols:

- a line unit (serializer/deserializer with its own bit timer) that inserts
  and removes stuff bits, NRZI or Manchester encodes, decodes Manchester by
  edge timing, and recovers bit timing from edges, so stuffed and encoded
  streams (USB, CAN, HDLC, Manchester) run at a uniform bit rate;
- a programmable 16-bit CRC; edge waits with timeouts; stepping on an external
  clock edge (slave modes); differential output for D+/D- pairs.

All eight `uio` pins (G0..G7) are protocol pins; the host uses `ui_in`/`uo_out`.
Specification: `v3/docs/spec.md`.

## How to test

Host bus: `ui[3:0]` write nibble, `ui[4]` write toggle (each change writes one
nibble), `ui[6:5]` channel, `ui[7]` read toggle. `uo[3:0]` shows the current read
nibble of the channel, `uo[7:4]` = SM1 RX valid, SM1 TX ready, SM0 RX valid,
SM0 TX ready. Hold data and channel for 3 clocks after each toggle.

Channels: 0/1 = data to/from SM0/SM1 (two nibbles per byte, low first),
2 = command packets (address byte then 16-bit value, high nibble first: SM
configuration, global pin setup, and actions such as enable, restart, execute
an instruction), 3 = program words (four nibbles each). The bench in
`v3/model/bench3.py` and the programs in `v3/programs` show complete
sequences; `make` in `test/` runs UART, SPI, I2C, USB-style and Manchester
scenarios against independent device models.

## External hardware

Whatever device the program speaks to, on G0..G7; I2C and 1-Wire need pull-up
resistors; USB, CAN or RS-485 links need the usual transceiver chip.
