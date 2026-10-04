/* SPDX-License-Identifier: Apache-2.0
 * Chipwheel v2: compact cycle-timed protocol engine. See v2/docs/spec.md.
 */
`default_nettype none

module tt_um_chipwheel2 (
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
    reg  [4:0] wptr;
    reg  running, pen, txack, rxv, ovr;
    reg  [7:0] rxd;

    // ------------------------------------------------------------ memory
    // 20 x 8 latch array; each word written through an integrated clock gate.
    wire mem_we = strobe & hcmd & htag & ~running;
    wire [7:0] mem [0:19];
    genvar w;
    generate
        for (w = 0; w < 20; w = w + 1) begin : g_word
            wire gclk;
            cw2_icg icg (.clk(clk), .en(mem_we && wptr == w), .gclk(gclk));
            cw2_latch8 word (.g(gclk), .d(ui_in), .q(mem[w]));
        end
    endgenerate

    wire [7:0] div  = mem[16];
    wire [7:0] mode = mem[17];
    wire [7:0] cfg  = mem[18];
    wire [7:0] wrap = mem[19];
    wire [3:0] idle = cfg[3:0];
    wire insel = cfg[4], msb = cfg[5], clkd = cfg[6], ph3 = cfg[7];
    wire cpol = ~ph3 & idle[1];         // I2C-style 3-phase shifts idle the clock low
    wire p1_od = mode[3:2] == 2'b10;

    // ------------------------------------------------------------ engine state
    reg  [3:0] pc, x, sub, out, pa, pb;  // pa/pb: pin synchronizer stages
    reg  [1:0] ph;                       // clocked shift phase: 0 A, 1 M, 2 B, 3 T
    reg  dly, f;
    reg  [8:0] sr;
    reg  [7:0] divc;

    wire [7:0] ins = mem[pc];
    wire [1:0] op = ins[7:6];
    wire tick = divc == 8'd0;
    wire in_bit = insel ? pb[0] : pb[2];
    wire avail = s2 & ~hcmd & ~txack;
    wire [8:0] pulled = {ui_in, htag};
    wire [3:0] nbits = ins[5:2];         // SHIFT: n-1
    wire sh_d = ins[1], sh_a = ins[0];
    wire first = sub == 4'd0;
    wire last = sub == nbits;
    wire [3:0] next_pc = (pc == wrap[3:0]) ? wrap[7:4] : pc + 4'd1;

    function [8:0] shl(input [8:0] v, input b, input m);
        shl = m ? {v[7:0], b} : {b, v[8:1]};
    endfunction
    function outb(input [8:0] v, input m);
        outb = m ? v[8] : v[0];
    endfunction
    function [7:0] rxsel(input [8:0] v, input hi);
        rxsel = hi ? v[7:0] : v[8:1];
    endfunction

    // shift datapath shared by the SHIFT variants
    wire shift_pull = sh_d & first & (clkd ? ph == 2'd0 : 1'b1);
    wire [8:0] src = shift_pull ? pulled : sr;
    wire [8:0] shifted = shl(src, in_bit, msb);
    wire stretch = p1_od & (pb[1] != ~cpol);

    reg stall;
    always @(*) begin
        stall = 1'b0;
        if (!dly)
            case (op)
                2'd2: if (shift_pull & ~avail) stall = 1'b1;
                      else if (clkd & ((ph == 2'd0 & ~first) | ph == 2'd3) & stretch) stall = 1'b1;
                2'd3: case (ins[5:4])
                          2'd1: stall = pb[ins[3:2]] != ins[1];
                          2'd2: stall = ins[2:0] == 3'd0 & ~avail;
                          default: stall = 1'b0;
                      endcase
                default: stall = 1'b0;
            endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s1 <= 0; s2 <= 0; s3 <= 0;
            pa <= 0; pb <= 0;
            wptr <= 0; running <= 0; pen <= 0;
            txack <= 0; rxv <= 0; ovr <= 0; rxd <= 0;
            pc <= 0; x <= 0; sub <= 0; out <= 0; ph <= 0;
            dly <= 0; f <= 0; sr <= 0; divc <= 0;
        end else begin
            s1 <= uio_in[4]; s2 <= s1; s3 <= s2;
            pa <= uio_in[3:0]; pb <= pa;
            if (!s2) txack <= 0;
            if (strobe) rxv <= 0;
            if (mem_we) wptr <= wptr + 5'd1;

            if (running) begin
                if (!tick) divc <= divc - 8'd1;
                else if (!stall) begin
                    divc <= div;
                    if (dly) begin
                        if (sub == 4'd1) begin dly <= 0; sub <= 0; pc <= next_pc; end
                        else sub <= sub - 4'd1;
                    end else case (op)
                        2'd0: begin                                   // BR
                            if (ins[5:4] == 2'd1 && x != 0) x <= x - 4'd1;
                            if (ins[5:4] == 2'd0 || (ins[5:4] == 2'd1 && x != 0) ||
                                (ins[5:4] == 2'd2 && !f) || (ins[5:4] == 2'd3 && f))
                                pc <= ins[3:0];
                            else pc <= next_pc;
                        end
                        2'd1: begin                                   // SET
                            out[ins[5:4]] <= ins[3];
                            if (ins[2:0] == 3'd0) pc <= next_pc;
                            else begin sub <= {1'b0, ins[2:0]}; dly <= 1; end
                        end
                        2'd2: begin                                   // SHIFT
                            if (shift_pull) txack <= 1;
                            if (!clkd) begin
                                if (sh_d) out[0] <= outb(src, msb);
                                sr <= shifted; f <= in_bit;
                                if (last) begin
                                    sub <= 0; pc <= next_pc;
                                    if (sh_a) begin
                                        rxd <= rxsel(shifted, msb ^ (nbits == 4'd8));
                                        rxv <= 1; if (rxv & ~strobe) ovr <= 1;
                                    end
                                end else sub <= sub + 4'd1;
                            end else case (ph)
                                2'd0: begin                           // A
                                    out[1] <= cpol;
                                    if (first) sr <= src;
                                    else begin sr <= shifted; f <= in_bit; end
                                    if (!ph3 && sh_d) out[0] <= outb(first ? src : shifted, msb);
                                    ph <= ph3 ? 2'd1 : 2'd2;
                                end
                                2'd1: begin                           // M
                                    if (sh_d) out[0] <= outb(sr, msb);
                                    ph <= 2'd2;
                                end
                                2'd2: begin                           // B
                                    out[1] <= ~cpol;
                                    if (last) ph <= 2'd3;
                                    else begin sub <= sub + 4'd1; ph <= 2'd0; end
                                end
                                2'd3: begin                           // T (capture only)
                                    sr <= shifted; f <= in_bit;
                                    if (sh_a) begin
                                        rxd <= rxsel(shifted, msb ^ (nbits == 4'd8));
                                        rxv <= 1; if (rxv & ~strobe) ovr <= 1;
                                    end
                                    sub <= 0; ph <= 0; pc <= next_pc;
                                end
                            endcase
                        end
                        2'd3: case (ins[5:4])
                            2'd0: begin x <= ins[3:0]; pc <= next_pc; end        // LDX
                            2'd1: pc <= next_pc;                                 // WAIT (met)
                            2'd2: case (ins[2:0])                                // MISC
                                3'd0: begin sr <= pulled; txack <= 1; pc <= next_pc; end
                                3'd1: begin
                                    rxd <= rxsel(sr, msb); rxv <= 1;
                                    if (rxv & ~strobe) ovr <= 1;
                                    pc <= next_pc;
                                end
                                3'd2: running <= 0;
                                3'd3: begin f <= outb(sr, msb); sr <= shl(sr, 1'b0, msb); pc <= next_pc; end
                                default: pc <= next_pc;
                            endcase
                            2'd3: begin                                          // DELAY
                                if (ins[3:0] == 4'd0) pc <= next_pc;
                                else begin sub <= ins[3:0]; dly <= 1; end
                            end
                        endcase
                    endcase
                end
            end

            // host commands take priority over the engine step
            if (strobe & hcmd & ~htag)
                case (ui_in[6:4])
                    3'd0: begin
                        pc <= ui_in[3:0]; running <= 1; pen <= 1;
                        sub <= 0; ph <= 0; dly <= 0; divc <= 0;
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
    genvar i;
    generate
        for (i = 0; i < 4; i = i + 1) begin : g_pad
            wire od = mode[2*i+1:2*i] == 2'b10;
            assign uio_out[i] = ~od & out[i];
            assign uio_oe[i] = pen & (mode[2*i] | (od & ~out[i]));
        end
    endgenerate
    assign uio_out[7:4] = 4'b0;
    assign uio_oe[7:4] = 4'b0;
    assign uo_out = hsel ? rxd : {pb, ovr, rxv, txack, running};

    wire _unused = &{ena, 1'b0};
endmodule

// Integrated clock gate: enable latched while clk is low, gclk = clk & en.
module cw2_icg (input wire clk, input wire en, output wire gclk);
`ifdef SYNTHESIS
    sg13cmos5l_lgcp_1 cell (.CLK(clk), .GATE(en), .GCLK(gclk));
`else
    reg en_l;
    always @(clk or en) if (!clk) en_l = en;
    assign gclk = clk & en_l;
`endif
endmodule

// 8-bit transparent-high latch word.
module cw2_latch8 (input wire g, input wire [7:0] d, output wire [7:0] q);
`ifdef SYNTHESIS
    sg13cmos5l_dlhq_1 bit_[7:0] (.GATE(g), .D(d), .Q(q));
`else
    reg [7:0] l;
    always @(*) if (g) l = d;
    assign q = l;
`endif
endmodule
