// SPDX-License-Identifier: BSD-2-Clause
// inc_pio_sm1 : single-state-machine RP2040-PIO-class incumbent.
//
// Thin wrapper around the UNMODIFIED upstream `pio` block from
// https://github.com/lawrie/fpga_pio (commit f38be97cdfa86d4551c96bb98599e3443000628b,
// BSD-2-Clause, see incumbents/upstream/fpga_pio/LICENSE), instantiated with
// NUM_MACHINES = 1. What remains is exactly one state machine plus what
// upstream builds around it:
//   * 32 x 16-bit writable instruction memory (host action INSTR)
//   * 24-bit fractional clock divider (16.8, host action DIV)
//   * X, Y, ISR, OSR, PC, delay counter, side-set / pin-group logic
//   * TX FIFO and RX FIFO, each 4 x 32 bits (upstream fifo.v)
//   * upstream's configuration registers and GPIO output coalescing registers
//
// Host interface = upstream's action bus (see pio.v): drive `action`, `index`,
// `din` for one clock; `dout` returns the RX word one clock after a PULL action.
//   action 1 INSTR  instr[index] <= din[15:0]
//          2 PEND   wrap/wrap_target/side-set-enable/... config
//          3 PULL   pop RX FIFO into dout
//          4 PUSH   push din into TX FIFO
//          5 GRPS   pin groups      7 DIV   clock divider
//          6 EN     enable/restart  9 IMM   execute din[15:0] immediately
//         10 SHIFT  shift control (autopush/pull, directions, thresholds)
// Only 8 GPIOs are brought out; upstream's remaining 24 pins are tied off
// (inputs to 0, outputs unconnected) so synthesis trims their logic.
`default_nettype none
module inc_pio_sm1 (
  input  wire        clk,
  input  wire        reset,     // synchronous, active high (upstream convention)
  input  wire [3:0]  action,
  input  wire [4:0]  index,
  input  wire [31:0] din,
  output wire [31:0] dout,
  input  wire [7:0]  gpio_in,
  output wire [7:0]  gpio_out,
  output wire [7:0]  gpio_oe,
  output wire        tx_full,
  output wire        rx_empty
);

  wire [31:0] gpio_out_w;
  wire [31:0] gpio_dir_w;
  wire [3:0]  tx_full_w;
  wire [3:0]  rx_empty_w;

  pio #(.NUM_MACHINES(1)) pio_1 (
    .clk      (clk),
    .reset    (reset),
    .mindex   (2'b00),
    .din      (din),
    .index    (index),
    .action   (action),
    .gpio_in  ({24'b0, gpio_in}),
    .gpio_out (gpio_out_w),
    .gpio_dir (gpio_dir_w),
    .dout     (dout),
    .irq0     (),
    .irq1     (),
    .tx_full  (tx_full_w),
    .rx_empty (rx_empty_w),
    .pclk     ()
  );

  assign gpio_out = gpio_out_w[7:0];
  assign gpio_oe  = gpio_dir_w[7:0];
  assign tx_full  = tx_full_w[0];
  assign rx_empty = rx_empty_w[0];

endmodule
`default_nettype wire
