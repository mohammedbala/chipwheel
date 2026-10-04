// SPDX-License-Identifier: BSD-3-Clause
// inc_femtorv_bb : "MCU firmware" incumbent -- FemtoRV32 Quark (RV32I) bit-banging GPIO.
//
// Core: FemtoRV/RTL/PROCESSOR/femtorv32_quark.v from
// https://github.com/BrunoLevy/learn-fpga (commit 5c08c870315c09ccd9ec64ccde20ab3375b3f273,
// BSD-3-Clause, see incumbents/upstream/femtorv/LICENSE), used as
// femtorv_bb/patched/femtorv32_quark.v: identical logic, only declarations moved
// ahead of their first use because Icarus 13 rejects use-before-declaration
// (diff: femtorv_bb/patched/femtorv32_quark.patch). Build options are in
// femtorv_cfg.v (NRV_IS_IO_ADDR=0, NRV_COUNTER_WIDTH=1); ADDR_WIDTH is set to
// the minimum that covers program memory + I/O (AW+3 = 8 bits for 32 words).
// Register file: upstream 32 x 32-bit array (RV32I; Quark has no RV32E option).
//
// SoC (same shape as inc_serv_bb, but Quark has a single shared memory port):
//   * Program memory: PROG_WORDS x 32-bit plain reg array, host-writable via
//     prog_we/prog_addr/prog_wdata (load while rst=1). Combinational read
//     (Quark latches the instruction in WAIT_INSTR, PC is still on mem_addr).
//     CPU stores to this region are ignored; CPU loads from it are allowed.
//   * I/O region = address bit [AW+2] set (x0-relative -16..-4 in firmware),
//     registers selected by address bits [3:2], identical map to inc_serv_bb:
//       0 GPIO_OUT R/W, 1 GPIO_IN R, 2 TX_MBOX R (read clears valid),
//       3 RX_MBOX W (sets host_rx_valid) / R.
//     I/O read data is captured on mem_rstrb (Quark samples load data one
//     cycle after the strobe, like a synchronous RAM).
`default_nettype none
module inc_femtorv_bb #(
  parameter PROG_WORDS = 32,
  parameter AW = $clog2(PROG_WORDS)
) (
  input  wire          clk,
  input  wire          rst,           // synchronous, active high; holds the CPU in reset
  input  wire          prog_we,
  input  wire [AW-1:0] prog_addr,
  input  wire [31:0]   prog_wdata,
  input  wire          host_tx_we,
  input  wire [7:0]    host_tx_data,
  output wire          host_tx_full,
  input  wire          host_rx_re,
  output reg  [7:0]    host_rx_data,
  output reg           host_rx_valid,
  input  wire [7:0]    gpio_in,
  output reg  [7:0]    gpio_out
);

  localparam ADDR_WIDTH = AW + 3;

  reg [31:0] prog_mem [0:PROG_WORDS-1];
  always @(posedge clk)
    if (prog_we)
      prog_mem[prog_addr] <= prog_wdata;

  wire [31:0] mem_addr;
  wire [31:0] mem_wdata;
  wire [3:0]  mem_wmask;
  wire        mem_rstrb;
  wire [31:0] mem_rdata;

  FemtoRV32 #(
    .RESET_ADDR (32'h0),
    .ADDR_WIDTH (ADDR_WIDTH)
  ) cpu (
    .clk       (clk),
    .mem_addr  (mem_addr),
    .mem_wdata (mem_wdata),
    .mem_wmask (mem_wmask),
    .mem_rdata (mem_rdata),
    .mem_rstrb (mem_rstrb),
    .mem_rbusy (1'b0),
    .mem_wbusy (1'b0),
    .reset     (~rst)               // Quark reset is active low
  );

  wire       is_io  = mem_addr[AW+2];
  wire [1:0] io_sel = mem_addr[3:2];
  wire       io_wr  = is_io & (|mem_wmask);
  wire       io_rd  = is_io & mem_rstrb;

  reg        tx_valid;
  reg  [7:0] tx_data;
  reg  [8:0] io_rdata_q;            // {bit31, bits[7:0]} of the last I/O read

  reg  [8:0] io_rdata;
  always @(*) begin
    case (io_sel)
      2'd0:    io_rdata = {1'b0, gpio_out};
      2'd1:    io_rdata = {1'b0, gpio_in};
      2'd2:    io_rdata = {tx_valid, tx_data};
      default: io_rdata = {host_rx_valid, host_rx_data};
    endcase
  end

  assign mem_rdata = is_io ? {io_rdata_q[8], 23'd0, io_rdata_q[7:0]}
                           : prog_mem[mem_addr[AW+1:2]];
  assign host_tx_full = tx_valid;

  always @(posedge clk) begin
    if (io_rd)
      io_rdata_q <= io_rdata;
    if (rst) begin
      gpio_out      <= 8'd0;
      tx_valid      <= 1'b0;
      host_rx_valid <= 1'b0;
    end else begin
      if (io_wr && io_sel == 2'd0)
        gpio_out <= mem_wdata[7:0];
      if (io_rd && io_sel == 2'd2)
        tx_valid <= 1'b0;
      if (host_tx_we)
        tx_valid <= 1'b1;
      if (host_rx_re)
        host_rx_valid <= 1'b0;
      if (io_wr && io_sel == 2'd3)
        host_rx_valid <= 1'b1;
    end
    if (host_tx_we)
      tx_data <= host_tx_data;
    if (io_wr && io_sel == 2'd3)
      host_rx_data <= mem_wdata[7:0];
  end

endmodule
`default_nettype wire
