# Roadmap and controlled experiments

Next single milestone: complete the pinned official CMOS5L LibreLane hardening
flow at 100 ns, record mapped area, detailed routing result, DRC/precheck and
worst setup/hold slack, then run the official post-layout gate regression.
Prerequisites: enough disk space, a compatible local container engine and the
full pinned PDK. No administrator install, cloud run or publishing was performed.

After that:

1. Loader hardening: v1 already loads both byte halves through real input pins.
   Add a completeness bitmap/commit command so partial programs cannot run;
   choose a synchronized serial loading interface to reduce host wiring. Verify
   reset/write interruption and byte errors. Memory currently survives reset
   but has no defined power-up contents.
2. Input sampling: synchronized digital inputs, explicit sample/conditional
   branch instructions. Define latency before writing tests. Avoid using raw
   asynchronous input edges as if they were clocked state.
3. SPI: add independently writable clock/data outputs and an input sample.
   Write programs for CPOL/CPHA modes; use an independent peripheral checker.
4. I2C: implement actual open-drain drive controls on the available uio pins,
   external pull-up assumptions, ACK/NACK sampling and clock stretching.
   Write programs rather than adding a fixed I2C controller.

## Controlled experiment proposal — not run

Hold pins, 10 MHz constraint, payload suite, host and tools fixed. Evaluate
three separate branches from this baseline: (a) 16 x 16 memory, (b) current
32 x 16, (c) 32 x 16 plus a fused SHIFT-and-hold instruction. Do not mix
changes until their independent effects are known. Version each ISA spec.

For each candidate first require the same public waveform/decode regressions
and both mutation checks. Then compare UART cycles/frame, minimum supported
bit duration, UART/pulse word counts, host load cycles, mapped cell area,
post-route setup/hold slack and routing/precheck outcome. Treat a correctness
failure as disqualifying. Report unavailable physical metrics as unavailable,
not zero or estimated success. No architecture search has been launched.
