/* SPDX-License-Identifier: Apache-2.0
 * Chipwheel v2: compact cycle-timed protocol engine. See docs/spec.md.
 */
`default_nettype none

module tt_um_chipwheel (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
    // ------------------------------------------------------------ host side
    wire hcmd = uio_in[6], htag = uio_in[5], hsel = uio_in[7];
    reg  s1, s2, s3;                 // HSTB synchronizer + edge history
    wire strobe = s2 & ~s3;
    reg  [3:0] wptr;
    reg  running, pen, txack, rxv, ovr;
    reg  [7:0] rxd;
    reg  pushp, pushhi;              // deferred push: copy SR into RXD next clock

    // ------------------------------------------------------------ memory
    // 13 x 8 latch array (10 instructions + 3 config); clock-gated writes.
    wire mem_we = strobe & hcmd & htag & ~running;
    wire [7:0] mem [0:15];
    genvar w;
    generate
        for (w = 0; w < 13; w = w + 1) begin : g_word
            wire gclk;
            chipwheel_icg icg (.clk(clk), .en(mem_we && wptr == w), .gclk(gclk));
            chipwheel_latch8 word (.g(gclk), .d(ui_in), .q(mem[w]));
        end
    endgenerate
    assign mem[13] = 8'h00;
    assign mem[14] = 8'h00;
    assign mem[15] = 8'h00;

    wire [7:0] div = mem[10];
    wire [3:0] idle = mem[11][3:0], od = mem[11][7:4];
    wire insel = mem[12][0], msb = mem[12][1], clkd = mem[12][2], ph3 = mem[12][3];
    wire [3:0] wrap_top = mem[12][7:4];
    wire cpol = ~ph3 & idle[1];      // 3-phase (I2C-style) shifts idle the clock low

    // ------------------------------------------------------------ engine state
    reg  [3:0] pc, x, sub, out, pa, pb;  // pa/pb: pin synchronizer stages
    reg  [1:0] ph;                       // clocked shift phase: 0 A, 1 M, 2 B, 3 T
    reg  busy, rel, f;
    reg  [8:0] sr;
    reg  [7:0] divc;

    wire [7:0] ins = pc < 4'd10 ? mem[pc] : 8'h00;   // 10..15 read as BR 0
    wire [1:0] op = ins[7:6];
    wire tick = divc == 8'd0;
    // Shift captures use the first synchronizer stage: protocol sample points sit
    // mid-bit, and the shorter latency lets full-duplex SPI run at clk/2.
    wire in_bit = insel ? pa[0] : pa[2];
    wire avail = s2 & ~hcmd & ~txack;
    wire [8:0] pulled = {ui_in, htag};
    wire [3:0] nbits = ins[5:2];         // SHIFT: n-1
    wire sh_d = ins[1], sh_a = ins[0];
    wire last = busy ? sub == 4'd1 : nbits == 4'd0;
    wire [3:0] next_pc = (pc == wrap_top) ? 4'd0 : pc + 4'd1;

    function [8:0] shl(input [8:0] v, input b, input m);
        shl = m ? {v[7:0], b} : {b, v[8:1]};
    endfunction
    function outb(input [8:0] v, input m);
        outb = m ? v[8] : v[0];
    endfunction

    wire shift_pull = (op == 2'd2) & sh_d & ~busy & (~clkd | ph == 2'd0);
    wire [8:0] src = shift_pull ? pulled : sr;
    wire [8:0] shifted = shl(src, in_bit, msb);

    // A released open-drain clock pin (P1) must read high before the next step.
    wire stretched = rel & od[1] & ~pb[1];
    reg stall;
    always @(*) begin
        stall = stretched;
        if (!busy)
            case (op)
                2'd2: if (shift_pull & ~avail) stall = 1'b1;
                2'd3: case (ins[5:4])
                          2'd1: if (pb[ins[3:2]] != ins[1]) stall = 1'b1;
                          2'd2: if (ins[2:0] == 3'd0 & ~avail) stall = 1'b1;
                          default: ;
                      endcase
                default: ;
            endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s1 <= 0; s2 <= 0; s3 <= 0;
            pa <= 0; pb <= 0;
            wptr <= 0; running <= 0; pen <= 0;
            txack <= 0; rxv <= 0; ovr <= 0; rxd <= 0; pushp <= 0; pushhi <= 0;
            pc <= 0; x <= 0; sub <= 0; out <= 0; ph <= 0;
            busy <= 0; rel <= 0; f <= 0; sr <= 0; divc <= 0;
        end else begin
            s1 <= uio_in[4]; s2 <= s1; s3 <= s2;
            pa <= uio_in[3:0]; pb <= pa;
            if (!s2) txack <= 0;
            if (strobe) rxv <= 0;
            if (mem_we) wptr <= wptr + 4'd1;
            pushp <= 0;
            if (pushp) begin
                rxd <= pushhi ? sr[7:0] : sr[8:1];
                rxv <= 1;
                if (rxv & ~strobe) ovr <= 1;
            end

            if (running) begin
                if (!tick) divc <= divc - 8'd1;
                else if (!stall) begin
                    divc <= div;
                    rel <= 0;
                    case (op)
                        2'd0: begin                                   // BR
                            if (ins[5:4] == 2'd1 && x != 0) x <= x - 4'd1;
                            if (ins[5:4] == 2'd0 || (ins[5:4] == 2'd1 && x != 0) ||
                                (ins[5:4] == 2'd2 && !f) || (ins[5:4] == 2'd3 && f))
                                pc <= ins[3:0];
                            else pc <= next_pc;
                        end
                        2'd1: begin                                   // SET [, delay]
                            if (!busy) begin
                                out[ins[5:4]] <= ins[3];
                                if (ins[5:4] == 2'd1 && ins[3]) rel <= 1;
                            end
                            if (busy ? sub == 4'd1 : ins[2:0] == 3'd0) begin
                                busy <= 0; pc <= next_pc;
                            end else begin
                                busy <= 1; sub <= busy ? sub - 4'd1 : {1'b0, ins[2:0]};
                            end
                        end
                        2'd2: begin                                   // SHIFT
                            if (shift_pull) txack <= 1;
                            if (!clkd) begin
                                if (sh_d) out[0] <= outb(src, msb);
                                sr <= shifted; f <= in_bit;
                                if (last) begin
                                    busy <= 0; pc <= next_pc;
                                    if (sh_a) begin pushp <= 1; pushhi <= msb ^ (nbits == 4'd8); end
                                end else begin
                                    busy <= 1; sub <= busy ? sub - 4'd1 : nbits;
                                end
                            end else case (ph)
                                2'd0: begin                           // A: clock idle
                                    out[1] <= cpol;
                                    if (!busy) begin sr <= src; sub <= nbits; busy <= 1; end
                                    else begin sr <= shifted; f <= in_bit; end
                                    if (!ph3 && sh_d) out[0] <= outb(busy ? shifted : src, msb);
                                    ph <= ph3 ? 2'd1 : 2'd2;
                                end
                                2'd1: begin                           // M: data (3-phase)
                                    if (sh_d) out[0] <= outb(sr, msb);
                                    ph <= 2'd2;
                                end
                                2'd2: begin                           // B: clock active
                                    out[1] <= ~cpol;
                                    rel <= ~cpol;
                                    if (sub == 4'd0) ph <= 2'd3;
                                    else begin sub <= sub - 4'd1; ph <= 2'd0; end
                                end
                                2'd3: begin                           // T: last capture
                                    if (ph3) out[1] <= 1'b0;
                                    sr <= shifted; f <= in_bit;
                                    if (sh_a) begin pushp <= 1; pushhi <= msb ^ (nbits == 4'd8); end
                                    busy <= 0; ph <= 0; pc <= next_pc;
                                end
                            endcase
                        end
                        2'd3: case (ins[5:4])
                            2'd0: begin x <= ins[3:0]; pc <= next_pc; end        // LDX
                            2'd1: pc <= next_pc;                                 // WAIT (met)
                            2'd2: case (ins[2:0])                                // MISC
                                3'd0: begin sr <= pulled; txack <= 1; pc <= next_pc; end
                                3'd1: begin pushp <= 1; pushhi <= msb; pc <= next_pc; end
                                3'd2: running <= 0;
                                3'd3: begin f <= outb(sr, msb); sr <= shifted; pc <= next_pc; end
                                default: pc <= next_pc;
                            endcase
                            2'd3: begin                                          // DELAY
                                if (busy ? sub == 4'd1 : ins[3:0] == 4'd0) begin
                                    busy <= 0; pc <= next_pc;
                                end else begin
                                    busy <= 1; sub <= busy ? sub - 4'd1 : ins[3:0];
                                end
                            end
                        endcase
                    endcase
                end
            end

            // host commands take priority over the engine step
            if (strobe & hcmd & ~htag)
                case (ui_in[6:4])
                    3'd0: begin
                        if (!pen) out <= idle;
                        pc <= ui_in[3:0]; running <= 1; pen <= 1;
                        busy <= 0; ph <= 0; rel <= 0; divc <= 0;
                    end
                    3'd1: running <= 0;
                    3'd2: begin out <= idle; pen <= 1; end
                    3'd3: wptr <= 0;
                    3'd4: ovr <= 0;
                    default: ;
                endcase
        end
    end

    // ------------------------------------------------------------ pads
    assign uio_out = {4'b0, out & ~od};
    assign uio_oe = {4'b0, {4{pen}} & (~od | ~out)};
    assign uo_out = hsel ? rxd : {pb, ovr, rxv, txack, running};

    wire _unused = &{ena, 1'b0};
endmodule

// Library cells are instantiated directly so synthesis cannot restructure the
// clock gate. test/cells_sim.v models them for RTL simulation.

// Clock gate: enable captured by a transparent-low latch, gclk = clk & en_d.
// en_d only changes while clk is low, so gclk cannot glitch. Built from
// discrete cells because the PDK excludes sg13cmos5l_lgcp_1 from the flow. The
// delay cell keeps en_d stable until the AND gate has seen clk fall, covering
// clock skew between the latch and the gate (fast-corner gating hold check).
module chipwheel_icg (input wire clk, input wire en, output wire gclk);
    wire en_l, en_d;
    sg13cmos5l_dllrq_1 hold (.GATE_N(clk), .D(en), .RESET_B(1'b1), .Q(en_l));
    sg13cmos5l_dlygate4sd3_1 skew (.A(en_l), .X(en_d));
    sg13cmos5l_and2_1 gate (.A(clk), .B(en_d), .X(gclk));
endmodule

// 8-bit transparent-high latch word.
module chipwheel_latch8 (input wire g, input wire [7:0] d, output wire [7:0] q);
    genvar i;
    generate
        for (i = 0; i < 8; i = i + 1) begin : g_bit
            sg13cmos5l_dlhq_1 bit_latch (.GATE(g), .D(d[i]), .Q(q[i]));
        end
    endgenerate
endmodule
