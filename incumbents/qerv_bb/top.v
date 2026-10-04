// SPDX-License-Identifier: ISC
// inc_qerv_bb : same SoC as inc_serv_bb, but SERV built with W=4 ("QERV",
// upstream's 4-bits-per-cycle option; RF_WIDTH = 8, register file 128 x 8 bits).
// Secondary data point: the fastest SERV-family configuration upstream offers.
// Compile after incumbents/serv_bb/top.v.
`default_nettype none
module inc_qerv_bb #(
  parameter PROG_WORDS = 32,
  parameter AW = $clog2(PROG_WORDS)
) (
  input  wire          clk,
  input  wire          rst,
  input  wire          prog_we,
  input  wire [AW-1:0] prog_addr,
  input  wire [31:0]   prog_wdata,
  input  wire          host_tx_we,
  input  wire [7:0]    host_tx_data,
  output wire          host_tx_full,
  input  wire          host_rx_re,
  output wire [7:0]    host_rx_data,
  output wire          host_rx_valid,
  input  wire [7:0]    gpio_in,
  output wire [7:0]    gpio_out
);
  inc_serv_bb #(.PROG_WORDS(PROG_WORDS), .W(4)) soc (
    .clk(clk), .rst(rst), .prog_we(prog_we), .prog_addr(prog_addr), .prog_wdata(prog_wdata),
    .host_tx_we(host_tx_we), .host_tx_data(host_tx_data), .host_tx_full(host_tx_full),
    .host_rx_re(host_rx_re), .host_rx_data(host_rx_data), .host_rx_valid(host_rx_valid),
    .gpio_in(gpio_in), .gpio_out(gpio_out));
endmodule
`default_nettype wire
