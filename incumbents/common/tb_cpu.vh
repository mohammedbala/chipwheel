// Shared testbench body for the soft-CPU bit-bang incumbents (inc_serv_bb,
// inc_femtorv_bb). Include after `define DUT <module name>.
// The program is loaded through the top's host port (prog_we/addr/wdata) while
// rst=1; bytes go through the host TX mailbox; RX bytes come back through the
// RX mailbox. No hierarchical preloading.
// Files in the working directory: prog.hex (32-bit words, +plen=N),
// tx.hex (+nbytes=N), slave_tx.hex (SPI slave MISO bytes).
// Plusargs: +spi=1 (expect RX bytes), +maxcyc, +tail.
// Output lines: P <cycle> <gpio_out hex>, R <cycle> <byte>, S <cycle> <byte>,
//               E <cycle> (reset released), END/TIMEOUT <cycle>.
// Pins: UART TX = gpio_out[0]; SPI SCK/MOSI = gpio_out[+sck]/gpio_out[+mosi]
// (default 1/0); MISO = gpio_in[0].
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  always #50 clk = ~clk;
  reg  [31:0] cyc = 0;
  always @(posedge clk) cyc <= cyc + 1;

  parameter PROG_WORDS = `PROG_WORDS;
  localparam AW = $clog2(PROG_WORDS);

  reg          rst = 1;
  reg          prog_we = 0;
  reg [AW-1:0] prog_addr = 0;
  reg [31:0]   prog_wdata = 0;
  reg          host_tx_we = 0;
  reg [7:0]    host_tx_data = 0;
  wire         host_tx_full;
  reg          host_rx_re = 0;
  wire [7:0]   host_rx_data;
  wire         host_rx_valid;
  wire [7:0]   gpio_out;
  wire         miso;

`ifdef GATE
  `DUT dut (                          // flattened Yosys netlist: no parameters
`else
  `DUT #(.PROG_WORDS(PROG_WORDS)) dut (
`endif
    .clk(clk), .rst(rst),
    .prog_we(prog_we), .prog_addr(prog_addr), .prog_wdata(prog_wdata),
    .host_tx_we(host_tx_we), .host_tx_data(host_tx_data), .host_tx_full(host_tx_full),
    .host_rx_re(host_rx_re), .host_rx_data(host_rx_data), .host_rx_valid(host_rx_valid),
    .gpio_in({7'b0, miso}), .gpio_out(gpio_out));

  integer sck_idx, mosi_idx;
  initial begin
    if (!$value$plusargs("sck=%d", sck_idx)) sck_idx = 1;
    if (!$value$plusargs("mosi=%d", mosi_idx)) mosi_idx = 0;
  end
  wire sck_pin  = gpio_out[sck_idx];
  wire mosi_pin = gpio_out[mosi_idx];
  spi_slave_model slave (.sck(sck_pin), .mosi(mosi_pin), .miso(miso), .cyc(cyc));

  reg [31:0] prog [0:255];
  reg [7:0]  txb  [0:255];
  integer plen, nbytes, spi, maxcyc, tail, i, sent, got;

  reg [7:0] last_pins = 8'hxx;
  always @(negedge clk)
    if (gpio_out !== last_pins) begin
      $display("P %0d %02x", cyc, gpio_out);
      last_pins = gpio_out;
    end

  initial begin
    if (!$value$plusargs("plen=%d", plen)) plen = 0;
    if (!$value$plusargs("nbytes=%d", nbytes)) nbytes = 0;
    if (!$value$plusargs("spi=%d", spi)) spi = 0;
    if (!$value$plusargs("maxcyc=%d", maxcyc)) maxcyc = 200000;
    if (!$value$plusargs("tail=%d", tail)) tail = 2000;
    if (plen > 0) $readmemh("prog.hex", prog, 0, plen - 1);
    if (nbytes > 0) $readmemh("tx.hex", txb, 0, nbytes - 1);
    if (plen > PROG_WORDS) begin
      $display("ERROR program (%0d words) larger than PROG_WORDS=%0d", plen, PROG_WORDS);
      $finish;
    end

    // load the program through the host port while the CPU is held in reset
    repeat (2) @(negedge clk);
    for (i = 0; i < plen; i = i + 1) begin
      @(negedge clk); prog_we = 1; prog_addr = i; prog_wdata = prog[i];
    end
    @(negedge clk); prog_we = 0;
    repeat (2) @(negedge clk);
    rst = 0;
    $display("E %0d", cyc);

    sent = 0; got = 0;
    while ((sent < nbytes) || (spi && got < nbytes)) begin
      @(negedge clk);
      host_tx_we = 0; host_rx_re = 0;
      if (host_rx_valid) begin
        $display("R %0d %02x", cyc, host_rx_data);
        host_rx_re = 1;
        got = got + 1;
      end
      if (sent < nbytes && !host_tx_full && !host_tx_we) begin
        host_tx_we = 1; host_tx_data = txb[sent];
        sent = sent + 1;
      end
      @(negedge clk);                 // flags settle (registered), keep it simple
      host_tx_we = 0; host_rx_re = 0;
    end
    repeat (tail) @(negedge clk);
    $display("END %0d", cyc);
    $finish;
  end

  initial begin
    wait (cyc == maxcyc);
    $display("TIMEOUT %0d", cyc);
    $finish;
  end
endmodule
