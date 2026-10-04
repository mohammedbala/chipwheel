// Testbench wrapper for inc_femtorv_bb: shared body in incumbents/common/tb_cpu.vh
// (compile with -I incumbents/common). Override memory size with -DPROG_WORDS=N.
`define DUT inc_femtorv_bb
`ifndef PROG_WORDS
`define PROG_WORDS 32
`endif
`include "tb_cpu.vh"
