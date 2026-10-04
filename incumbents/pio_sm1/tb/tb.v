// Testbench for inc_pio_sm1 (and inc_pio_full with -DFULL).
// Everything is loaded through the top's host ports (upstream action bus);
// no hierarchical preloading. Files read from the working directory:
//   prog.hex   PIO program, 16-bit words (+plen=N)
//   tx.hex     bytes to push to the TX FIFO (+nbytes=N), word = byte << +txshift
//   slave_tx.hex  bytes the SPI slave model returns on MISO
// Plusargs: +grps +pend +shift +div +imm (hex config words, upstream layout),
//           +spi=1 (pull RX words), +maxcyc, +tail.
// Output lines:  P <cycle> <gpio_out hex>    (pin change after posedge <cycle>)
//                R <cycle> <rx word>         (RX FIFO word read by host)
//                S <cycle> <byte>            (byte received by SPI slave model)
//                E <cycle>                   (state machine enabled)
//                END <cycle> | TIMEOUT <cycle>
// Pins: UART TX = gpio_out[0]; SPI SCK/MOSI = gpio_out[+sck]/gpio_out[+mosi]
// (default 1/0); MISO = gpio_in[0].
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  always #50 clk = ~clk;              // 10 MHz, like the project target
  reg  [31:0] cyc = 0;
  always @(posedge clk) cyc <= cyc + 1;

  reg         reset = 1;
  reg  [3:0]  action = 0;
  reg  [4:0]  index = 0;
  reg  [31:0] din = 0;
  wire [31:0] dout;
  wire [7:0]  gpio_out, gpio_oe;
  wire        miso;
  wire        tx_full, rx_empty;

`ifdef FULL
  wire [3:0] tx_full_v, rx_empty_v;
  inc_pio_full dut (.clk(clk), .reset(reset), .mindex(2'b00), .action(action), .index(index),
                    .din(din), .dout(dout), .gpio_in({7'b0, miso}), .gpio_out(gpio_out),
                    .gpio_oe(gpio_oe), .tx_full(tx_full_v), .rx_empty(rx_empty_v));
  assign tx_full = tx_full_v[0];
  assign rx_empty = rx_empty_v[0];
`else
  inc_pio_sm1 dut (.clk(clk), .reset(reset), .action(action), .index(index), .din(din),
                   .dout(dout), .gpio_in({7'b0, miso}), .gpio_out(gpio_out), .gpio_oe(gpio_oe),
                   .tx_full(tx_full), .rx_empty(rx_empty));
`endif

  integer sck_idx, mosi_idx;
  initial begin
    if (!$value$plusargs("sck=%d", sck_idx)) sck_idx = 1;
    if (!$value$plusargs("mosi=%d", mosi_idx)) mosi_idx = 0;
  end
  wire sck_pin  = gpio_out[sck_idx];
  wire mosi_pin = gpio_out[mosi_idx];
  spi_slave_model slave (.sck(sck_pin), .mosi(mosi_pin), .miso(miso), .cyc(cyc));

  localparam NONE = 0, INSTR = 1, PEND = 2, PULL = 3, PUSH = 4, GRPS = 5, EN = 6,
             DIV = 7, IMM = 9, SHIFT = 10;

  reg [15:0] prog [0:31];
  reg [7:0]  txb  [0:255];
  integer plen, nbytes, txshift, spi, maxcyc, tail, i, sent, got;
  reg [31:0] grps, pend, shiftc, div, imm, rxword;

  // One host transaction = 2 clocks, called and returning at a negedge: action
  // for one clock, then NONE with din held (upstream registers push/pull/imm and
  // the FIFO samples din one clock later; dout is valid one clock after PULL and
  // is overwritten by the next NONE). On return the FIFO flags are up to date.
  task act(input [3:0] a, input [31:0] d, input [4:0] idx);
    begin
      action = a; din = d; index = idx;
      @(negedge clk); rxword = dout; action = NONE;
      @(negedge clk);
    end
  endtask

  reg [7:0] last_pins = 8'hxx;
  always @(negedge clk)
    if (gpio_out !== last_pins) begin
      $display("P %0d %02x", cyc, gpio_out);
      last_pins = gpio_out;
    end

  initial begin
    if (!$value$plusargs("plen=%d", plen)) plen = 0;
    if (!$value$plusargs("nbytes=%d", nbytes)) nbytes = 0;
    if (!$value$plusargs("txshift=%d", txshift)) txshift = 0;
    if (!$value$plusargs("spi=%d", spi)) spi = 0;
    if (!$value$plusargs("maxcyc=%d", maxcyc)) maxcyc = 100000;
    if (!$value$plusargs("tail=%d", tail)) tail = 200;
    if (!$value$plusargs("grps=%h", grps)) grps = 0;
    if (!$value$plusargs("pend=%h", pend)) pend = 0;
    if (!$value$plusargs("shift=%h", shiftc)) shiftc = 0;
    if (!$value$plusargs("div=%h", div)) div = 32'h100;
    if (!$value$plusargs("imm=%h", imm)) imm = 32'hA042;   // nop
    if (plen > 0) $readmemh("prog.hex", prog, 0, plen - 1);
    if (nbytes > 0) $readmemh("tx.hex", txb, 0, nbytes - 1);

    repeat (3) @(posedge clk);
    @(negedge clk) reset = 0;
    @(negedge clk);
    for (i = 0; i < plen; i = i + 1) act(INSTR, {16'd0, prog[i]}, i);
    act(PEND, pend, 0);
    act(DIV, div, 0);
    act(GRPS, grps, 0);
    act(SHIFT, shiftc, 0);
    act(IMM, imm, 0);                 // e.g. set pindirs
    act(EN, 32'd1, 0);
    $display("E %0d", cyc);

    sent = 0; got = 0;
    while ((sent < nbytes) || (spi && got < nbytes)) begin
      if (spi && !rx_empty) begin
        act(PULL, 0, 0);
        $display("R %0d %08x", cyc, rxword);
        got = got + 1;
      end else if (sent < nbytes && !tx_full) begin
        act(PUSH, {24'd0, txb[sent]} << txshift, 0);
        sent = sent + 1;
      end else begin
        @(negedge clk);
      end
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
