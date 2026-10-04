// SPDX-License-Identifier: ISC
// inc_serv_bb : "MCU firmware" incumbent -- SERV bit-serial RV32I core bit-banging GPIO.
//
// Core: UNMODIFIED SERV from https://github.com/olofk/serv
// (commit f200eb2ed7b69ac1c6b8eddd47654522aeee5ce8, ISC, see
// incumbents/upstream/serv/LICENSE), used through upstream serv_rf_top
// (serv_top + serv_rf_ram_if + serv_rf_ram) with:
//   W=1 (classic bit-serial SERV), RF_WIDTH=2, WITH_CSR=0 (no CSRs/interrupts:
//   smallest legal config; the firmware needs none), PRE_REGISTER=1,
//   RESET_STRATEGY="MINI", COMPRESSED=0, MDU=0, RESET_PC=0. ISA: RV32I
//   (SERV has no RV32E option), so the register file is 32 x 32 bits held in
//   serv_rf_ram as 512 x 2-bit words (1024 bits, plain Verilog array).
//
// SoC around it (this file), Harvard and zero-wait-state:
//   * Program memory: PROG_WORDS x 32-bit plain reg array, host-writable
//     through prog_we/prog_addr/prog_wdata (load it while rst=1).
//     Combinational read; ibus ack = cyc (no wait states).
//   * Data bus -> I/O only (no data RAM; the bit-bang firmware needs none).
//     Zero-wait-state: dbus ack = cyc. Decoded by address bits [3:2]
//     (firmware uses x0-relative addresses -16, -12, -8, -4):
//       [3:2]=0  GPIO_OUT  R/W  bits[7:0] drive gpio_out[7:0]
//       [3:2]=1  GPIO_IN   R    bits[7:0] = gpio_in[7:0]
//       [3:2]=2  TX_MBOX   R    {valid,23'b0,data[7:0]}; a read clears valid
//       [3:2]=3  RX_MBOX   W    data[7:0] -> host_rx_data, sets host_rx_valid
//                          R    {host_rx_valid,23'b0,host_rx_data}
//   * TX_MBOX is written by the host (host_tx_we) and is a 1-deep FIFO:
//     host_tx_full = valid. RX_MBOX is cleared by the host with host_rx_re.
`default_nettype none
module inc_serv_bb #(
  parameter PROG_WORDS = 32,
  parameter AW = $clog2(PROG_WORDS),
  parameter W = 1                     // SERV datapath width: 1 = SERV, 4 = QERV
) (
  input  wire          clk,
  input  wire          rst,           // synchronous, active high; holds the CPU in reset
  // host: program load
  input  wire          prog_we,
  input  wire [AW-1:0] prog_addr,
  input  wire [31:0]   prog_wdata,
  // host: byte mailboxes
  input  wire          host_tx_we,
  input  wire [7:0]    host_tx_data,
  output wire          host_tx_full,
  input  wire          host_rx_re,
  output reg  [7:0]    host_rx_data,
  output reg           host_rx_valid,
  // pins
  input  wire [7:0]    gpio_in,
  output reg  [7:0]    gpio_out
);

  // ---------------- program memory ----------------
  reg [31:0] prog_mem [0:PROG_WORDS-1];
  always @(posedge clk)
    if (prog_we)
      prog_mem[prog_addr] <= prog_wdata;

  // ---------------- core ----------------
  wire [31:0] ibus_adr;
  wire        ibus_cyc;
  wire [31:0] dbus_adr;
  wire [31:0] dbus_dat;
  wire [3:0]  dbus_sel;
  wire        dbus_we;
  wire        dbus_cyc;
  reg  [31:0] dbus_rdt;

  serv_rf_top #(
    .RESET_PC       (32'd0),
    .COMPRESSED     (1'b0),
    .MDU            (1'b0),
    .PRE_REGISTER   (1),
    .RESET_STRATEGY ("MINI"),
    .WITH_CSR       (0),
    .W              (W)
  ) cpu (
    .clk          (clk),
    .i_rst        (rst),
    .i_timer_irq  (1'b0),
    .o_ibus_adr   (ibus_adr),
    .o_ibus_cyc   (ibus_cyc),
    .i_ibus_rdt   (prog_mem[ibus_adr[AW+1:2]]),
    .i_ibus_ack   (ibus_cyc),
    .o_dbus_adr   (dbus_adr),
    .o_dbus_dat   (dbus_dat),
    .o_dbus_sel   (dbus_sel),
    .o_dbus_we    (dbus_we),
    .o_dbus_cyc   (dbus_cyc),
    .i_dbus_rdt   (dbus_rdt),
    .i_dbus_ack   (dbus_cyc),
    .o_ext_rs1    (),
    .o_ext_rs2    (),
    .o_ext_funct3 (),
    .i_ext_rd     (32'd0),
    .i_ext_ready  (1'b0),
    .o_mdu_valid  ()
  );

  // ---------------- I/O ----------------
  reg        tx_valid;
  reg  [7:0] tx_data;
  wire [1:0] io_sel = dbus_adr[3:2];
  wire       io_wr  = dbus_cyc &  dbus_we;
  wire       io_rd  = dbus_cyc & ~dbus_we;

  always @(*) begin
    case (io_sel)
      2'd0:    dbus_rdt = {24'd0, gpio_out};
      2'd1:    dbus_rdt = {24'd0, gpio_in};
      2'd2:    dbus_rdt = {tx_valid, 23'd0, tx_data};
      default: dbus_rdt = {host_rx_valid, 23'd0, host_rx_data};
    endcase
  end

  assign host_tx_full = tx_valid;

  always @(posedge clk) begin
    if (rst) begin
      gpio_out      <= 8'd0;
      tx_valid      <= 1'b0;
      host_rx_valid <= 1'b0;
    end else begin
      if (io_wr && io_sel == 2'd0)
        gpio_out <= dbus_dat[7:0];
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
      host_rx_data <= dbus_dat[7:0];
  end

endmodule
`default_nettype wire
