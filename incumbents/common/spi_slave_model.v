// SPI mode-0 slave model for the incumbent testbenches (simulation only).
// MSB first. Samples MOSI on SCK rising edges, updates MISO on SCK falling
// edges (shift, or load the next TX byte after every 8th bit). MISO bit 7 of
// the first byte is presented from time zero (slave permanently selected).
// MISO changes MISO_DELAY time units after the SCK edge (clock-to-out).
// Only clean 0->1 / 1->0 transitions count as edges (X at start-up is ignored).
// Reads its transmit bytes from slave_tx.hex in the working directory and
// prints "S <cycle> <byte>" for every byte received on MOSI.
`timescale 1ns/1ps
module spi_slave_model #(
  parameter MISO_DELAY = 5
) (
  input  wire        sck,
  input  wire        mosi,
  output wire        miso,
  input  wire [31:0] cyc
);
  reg [7:0] tx_mem [0:255];
  reg [7:0] sh;
  reg [7:0] rx;
  reg       sck_q;
  reg       load_next;
  integer   nbit, txi;

  initial begin
    $readmemh("slave_tx.hex", tx_mem, 0, 15);
    sh = tx_mem[0];
    txi = 1; nbit = 0; rx = 0; load_next = 0; sck_q = 1'bx;
  end

  assign #(MISO_DELAY) miso = sh[7];

  always @(sck) begin
    if (sck_q === 1'b0 && sck === 1'b1) begin          // rising: sample MOSI
      rx = {rx[6:0], mosi};
      nbit = nbit + 1;
      if (nbit == 8) begin
        $display("S %0d %02x", cyc, rx);
        nbit = 0;
        load_next = 1;
      end
    end else if (sck_q === 1'b1 && sck === 1'b0) begin // falling: next MISO bit
      if (load_next) begin
        sh = tx_mem[txi];
        txi = txi + 1;
        load_next = 0;
      end else begin
        sh = {sh[6:0], 1'b0};
      end
    end
    sck_q = sck;
  end
endmodule
