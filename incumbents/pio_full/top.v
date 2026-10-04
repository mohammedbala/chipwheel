// SPDX-License-Identifier: BSD-2-Clause
// inc_pio_full : the full upstream 4-state-machine PIO block (secondary reference).
//
// Wrapper around the UNMODIFIED upstream `pio` from https://github.com/lawrie/fpga_pio
// (commit f38be97cdfa86d4551c96bb98599e3443000628b, BSD-2-Clause), NUM_MACHINES = 4:
// shared 32 x 16 instruction memory, 4 state machines, 4 x (TX FIFO + RX FIFO)
// of 4 x 32 bits each. Same 8-GPIO host-facing shape as inc_pio_sm1, plus
// `mindex` to select which machine a configuration / FIFO action targets.
`default_nettype none
module inc_pio_full (
  input  wire        clk,
  input  wire        reset,
  input  wire [1:0]  mindex,
  input  wire [3:0]  action,
  input  wire [4:0]  index,
  input  wire [31:0] din,
  output wire [31:0] dout,
  input  wire [7:0]  gpio_in,
  output wire [7:0]  gpio_out,
  output wire [7:0]  gpio_oe,
  output wire [3:0]  tx_full,
  output wire [3:0]  rx_empty
);

  wire [31:0] gpio_out_w;
  wire [31:0] gpio_dir_w;

  pio #(.NUM_MACHINES(4)) pio_1 (
    .clk      (clk),
    .reset    (reset),
    .mindex   (mindex),
    .din      (din),
    .index    (index),
    .action   (action),
    .gpio_in  ({24'b0, gpio_in}),
    .gpio_out (gpio_out_w),
    .gpio_dir (gpio_dir_w),
    .dout     (dout),
    .irq0     (),
    .irq1     (),
    .tx_full  (tx_full),
    .rx_empty (rx_empty),
    .pclk     ()
  );

  assign gpio_out = gpio_out_w[7:0];
  assign gpio_oe  = gpio_dir_w[7:0];

endmodule
`default_nettype wire
