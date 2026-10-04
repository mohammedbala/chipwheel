// Testbench wrapper for inc_pio_full: reuses incumbents/pio_sm1/tb/tb.v with FULL
// (compile with -I incumbents/pio_sm1/tb). Drives state machine 0 only.
`define FULL
`include "../../pio_sm1/tb/tb.v"
