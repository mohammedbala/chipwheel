// FemtoRV32 Quark build options for inc_femtorv_bb (compile BEFORE femtorv32_quark.v).
// Both are knobs documented in the upstream file header; no upstream edits.
//
// NRV_IS_IO_ADDR(addr) = 0 : the SoC's I/O completes writes in the same cycle
//   (zero wait states), so stores need no WAIT_ALU_OR_MEM state (3-cycle stores
//   instead of 4). Loads always take their wait state regardless.
// NRV_COUNTER_WIDTH = 1    : the RDCYCLE counter is not used by the firmware;
//   upstream offers this knob "for space-constrained designs".
`define NRV_IS_IO_ADDR(addr) 1'b0
`define NRV_COUNTER_WIDTH 1
