# How it works

Chipwheel executes programs from a 32-word writable instruction memory to
control a pin at exact clock intervals. Instructions set the output, shift
payload bits out, wait, count repetitions, branch and halt. UART and pulse
patterns use the same execution engine. Target clock: 10 MHz.

# How to test

Run the local cocotb regression described in the project README. It loads
programs using the actual dedicated/bidirectional input ports and checks the
public output waveform each clock. Example UART program: 8N1, LSB first,
eight clocks per bit. See docs/spec.md for precise instruction timing.

Host loading: ui=data byte; uio[7:3]=word address; uio[2]=high-byte select;
uio[1]=write; uio[0]=start. Load both bytes while idle. Supply payload on ui,
then raise start. Outputs: uo[0]=pin, uo[1]=busy, uo[2]=error.

# External hardware

No external hardware was used. All host signals must be synchronous to clk.
No silicon validation or completed physical layout is claimed yet.
