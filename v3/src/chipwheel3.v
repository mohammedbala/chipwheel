/* SPDX-License-Identifier: Apache-2.0
 * Chipwheel v3: general programmable protocol engine. See v3/docs/spec.md.
 * Two PIO-compatible state machines with hardware line coding and CRC.
 */
`default_nettype none

module tt_um_chipwheel3 (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
    // ------------------------------------------------------------ host bus
    wire [3:0] nib = ui_in[3:0];
    wire [1:0] ch = ui_in[6:5];
    reg  w1, w2, w3, r1, r2, r3;
    wire wt_ev = w2 ^ w3;
    wire rt_ev = r2 ^ r3;
    reg  [7:0] pa, pb;
    reg  [23:0] shreg;
    reg  [2:0] ncount;
    reg  pch, cmd_ready, prog_ready;
    reg  [4:0] pptr;
    reg  [1:0] rd_nib;
    reg  [3:0] irq;

    wire [7:0] cmd_addr = shreg[23:16];
    wire [15:0] cmd_data = shreg[15:0];
    wire [3:0] cmd_tgt = cmd_addr[7:4], cmd_reg = cmd_addr[3:0];
    wire act = cmd_ready & (cmd_tgt == 4'd3);

    // ------------------------------------------------------------ latch storage
    // Program memory (32 words) and configuration (2 x 8 SM registers + 2 global),
    // each word written through a clock gate from the command shift register.
    wire [15:0] mem [0:31];
    genvar w;
    generate
        for (w = 0; w < 32; w = w + 1) begin : g_mem
            wire gclk;
            chipwheel3_icg icg (.clk(clk), .en(prog_ready && pptr == w), .gclk(gclk));
            chipwheel3_latch16 word (.g(gclk), .d(cmd_data), .q(mem[w]));
        end
    endgenerate
    wire [15:0] cfg0 [0:7];
    wire [15:0] cfg1 [0:7];
    wire [15:0] glob [0:1];
    generate
        for (w = 0; w < 8; w = w + 1) begin : g_cfg
            wire g0, g1;
            chipwheel3_icg icg0 (.clk(clk), .en(cmd_ready && cmd_addr == w), .gclk(g0));
            chipwheel3_latch16 r0 (.g(g0), .d(cmd_data), .q(cfg0[w]));
            chipwheel3_icg icg1 (.clk(clk), .en(cmd_ready && cmd_addr == 8'h10 + w), .gclk(g1));
            chipwheel3_latch16 r1 (.g(g1), .d(cmd_data), .q(cfg1[w]));
        end
        for (w = 0; w < 2; w = w + 1) begin : g_glob
            wire gg;
            chipwheel3_icg icg (.clk(clk), .en(cmd_ready && cmd_addr == 8'h20 + w), .gclk(gg));
            chipwheel3_latch16 r (.g(gg), .d(cmd_data), .q(glob[w]));
        end
    endgenerate
    wire span = glob[1][0];
    wire [7:0] od_mask = glob[0][7:0], owner = glob[0][15:8];

    // ------------------------------------------------------------ fetch
    wire [4:0] pc0, pc1, pcn0, pcn1;   // current and next program counters
    // Separate read ports: SM0 reads A (or B with SPAN) through its own port, SM1
    // reads B through another, so neither state machine's PC reaches the other's
    // instruction path.
    // Each SM registers the instruction at its *next* PC (prefetch), so the memory
    // read and the execute logic are separate timing paths.
    wire [15:0] word_a = mem[{1'b0, pcn0[3:0]}];
    wire [15:0] word_b0 = mem[{1'b1, pcn0[3:0]}];
    wire [15:0] ins0 = (span & pcn0[4]) ? word_b0 : word_a;
    wire [15:0] ins1 = mem[{1'b1, pcn1[3:0]}];

    // ------------------------------------------------------------ state machines
    wire en0, en1, txv0, txv1, rxv0, rxv1, f16_0, f16_1;
    wire [7:0] val0, val1, dir0, dir1;
    wire [3:0] set0, set1, clr0, clr1;
    wire [15:0] rxb0, rxb1;
    wire pop0 = rt_ev & ch == 2'd0 & rxv0 & (rd_nib >= (f16_0 ? 2'd3 : 2'd1));
    wire pop1 = rt_ev & ch == 2'd1 & rxv1 & (rd_nib >= (f16_1 ? 2'd3 : 2'd1));

    chipwheel3_sm #(.NUM(0)) sm0 (
        .clk(clk), .rst_n(rst_n),
        .c0(cfg0[0]), .c1(cfg0[1]), .c2(cfg0[2]), .c3(cfg0[3]),
        .c4(cfg0[4]), .c5(cfg0[5]), .c6(cfg0[6]), .c7(cfg0[7]),
        .pa(pa), .pb(pb), .fetched(ins0), .flags(irq), .blocked(1'b0),
        .host_wnib(wt_ev & ch == 2'd0), .nib(nib), .host_pop(pop0),
        .act_en(act & cmd_reg == 4'd0), .en_bit(cmd_data[0]),
        .act_restart(act & cmd_reg == 4'd1 & ~cmd_data[8]), .restart_pc(cmd_data[4:0]),
        .act_exec(act & cmd_reg == 4'd2), .exec_ins(cmd_data),
        .pc_o(pc0), .pcn_o(pcn0), .en_o(en0), .val_o(val0), .dir_o(dir0), .irq_set(set0), .irq_clr(clr0),
        .txv_o(txv0), .rxv_o(rxv0), .rxbuf_o(rxb0), .fifo16_o(f16_0));
    chipwheel3_sm #(.NUM(1)) sm1 (
        .clk(clk), .rst_n(rst_n),
        .c0(cfg1[0]), .c1(cfg1[1]), .c2(cfg1[2]), .c3(cfg1[3]),
        .c4(cfg1[4]), .c5(cfg1[5]), .c6(cfg1[6]), .c7(cfg1[7]),
        .pa(pa), .pb(pb), .fetched(ins1), .flags(irq), .blocked(span),
        .host_wnib(wt_ev & ch == 2'd1), .nib(nib), .host_pop(pop1),
        .act_en(act & cmd_reg == 4'd0), .en_bit(cmd_data[1]),
        .act_restart(act & cmd_reg == 4'd1 & cmd_data[8]), .restart_pc(cmd_data[4:0]),
        .act_exec(act & cmd_reg == 4'd3), .exec_ins(cmd_data),
        .pc_o(pc1), .pcn_o(pcn1), .en_o(en1), .val_o(val1), .dir_o(dir1), .irq_set(set1), .irq_clr(clr1),
        .txv_o(txv1), .rxv_o(rxv1), .rxbuf_o(rxb1), .fifo16_o(f16_1));

    // ------------------------------------------------------------ sequential host logic
    wire host_irq_clr = act & cmd_reg == 4'd5;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            w1 <= 0; w2 <= 0; w3 <= 0; r1 <= 0; r2 <= 0; r3 <= 0;
            pa <= 0; pb <= 0;
            shreg <= 0; ncount <= 0; pch <= 0; cmd_ready <= 0; prog_ready <= 0;
            pptr <= 0; rd_nib <= 0; irq <= 0;
        end else begin
            w1 <= ui_in[4]; w2 <= w1; w3 <= w2;
            r1 <= ui_in[7]; r2 <= r1; r3 <= r2;
            pa <= uio_in; pb <= pa;
            cmd_ready <= 0;
            prog_ready <= 0;
            if (wt_ev & ch[1]) begin
                shreg <= {shreg[19:0], nib};
                pch <= ch[0];
                if (ch[0] == 1'b0 && (pch == 1'b0 ? ncount : 3'd0) == 3'd5) begin
                    cmd_ready <= 1; ncount <= 0;
                end else if (ch[0] == 1'b1 && (pch == 1'b1 ? ncount : 3'd0) == 3'd3) begin
                    prog_ready <= 1; ncount <= 0;
                end else
                    ncount <= (pch == ch[0] ? ncount : 3'd0) + 3'd1;
            end
            if (rt_ev) begin
                if (ch == 2'd2) rd_nib <= rd_nib + 2'd1;
                else if (ch == 2'd0 && rxv0) rd_nib <= pop0 ? 2'd0 : rd_nib + 2'd1;
                else if (ch == 2'd1 && rxv1) rd_nib <= pop1 ? 2'd0 : rd_nib + 2'd1;
            end
            if (act & cmd_reg == 4'd4) pptr <= cmd_data[4:0];
            if (prog_ready) pptr <= pptr + 5'd1;
            irq <= (irq | set0 | set1) & ~(clr0 | clr1 | (host_irq_clr ? cmd_data[3:0] : 4'd0));
        end
    end

    // ------------------------------------------------------------ outputs
    wire [15:0] status_word = {pc1, pc0, en1, en0, irq};
    reg  [15:0] rd_word;
    always @(*) begin
        case (ch)
            2'd0: rd_word = rxb0;
            2'd1: rd_word = rxb1;
            2'd2: rd_word = status_word;
            default: rd_word = 16'h0000;
        endcase
    end
    wire [3:0] rd_nibble = rd_word >> {rd_nib, 2'b00};
    assign uo_out = {rxv1, ~txv1, rxv0, ~txv0, rd_nibble};
    wire [7:0] pval = (val1 & owner) | (val0 & ~owner);
    wire [7:0] pdir = (dir1 & owner) | (dir0 & ~owner);
    assign uio_out = pval & ~od_mask;
    assign uio_oe = pdir & ~(od_mask & pval);

    wire _unused = &{ena, 1'b0};
endmodule

// ------------------------------------------------------------------ state machine
module chipwheel3_sm #(parameter NUM = 0) (
    input  wire        clk, rst_n,
    input  wire [15:0] c0, c1, c2, c3, c4, c5, c6, c7,
    input  wire [7:0]  pa, pb,
    input  wire [15:0] fetched,
    input  wire [3:0]  flags,
    input  wire        blocked,
    input  wire        host_wnib,
    input  wire [3:0]  nib,
    input  wire        host_pop,
    input  wire        act_en, en_bit,
    input  wire        act_restart,
    input  wire [4:0]  restart_pc,
    input  wire        act_exec,
    input  wire [15:0] exec_ins,
    output wire [4:0]  pc_o,
    output wire [4:0]  pcn_o,
    output wire        en_o,
    output wire [7:0]  val_o, dir_o,
    output reg  [3:0]  irq_set, irq_clr,
    output wire        txv_o, rxv_o,
    output wire [15:0] rxbuf_o,
    output wire        fifo16_o
);
    localparam [1:0] SMN = NUM;
    // ---- configuration fields
    wire [15:0] div = c0;
    wire [1:0] clksrc = c1[1:0];
    wire resync = c1[2];
    wire [2:0] clk_pin = c1[5:3];
    wire in_fast = c1[6];
    wire [2:0] out_base = c2[2:0];
    wire [3:0] out_count = (c2[6:3] > 4'd8) ? 4'd8 : c2[6:3];
    wire [2:0] set_base = c2[9:7];
    wire [2:0] set_count = (c2[12:10] > 3'd5) ? 3'd5 : c2[12:10];
    wire [2:0] side_base = c2[15:13];
    wire [2:0] in_base = c3[2:0];
    wire [2:0] jmp_pin = c3[5:3];
    wire [1:0] side_count = c3[7:6];
    wire side_en = c3[8], side_pindir = c3[9];
    wire [1:0] jflag = c3[11:10];
    wire status_sel = c3[12];
    wire [4:0] wrap_bottom = c4[4:0], wrap_top = c4[9:5];
    wire in_left = c4[10], out_left = c4[11], autopush = c4[12], autopull = c4[13];
    wire fifo16 = c4[14], diff = c4[15];
    wire [4:0] push_thresh = (c5[3:0] == 4'd0) ? 5'd16 : {1'b0, c5[3:0]};
    wire [4:0] pull_thresh = (c5[7:4] == 4'd0) ? 5'd16 : {1'b0, c5[7:4]};
    wire [2:0] stuff_n = c5[10:8];
    wire stuff_ones = c5[11], line_tx = c5[12], line_rx = c5[13];
    wire line = line_tx | line_rx;
    wire [1:0] enc = c5[15:14];
    wire [1:0] dec = c6[1:0];
    wire crc_out = c6[2], crc_in = c6[3], crc_reflect = c6[4];
    wire [15:0] poly = c7;
    assign fifo16_o = fifo16;

    // ---- state
    reg en, timeout, codeerr, irq_wait;
    reg [4:0] pc, isr_cnt, osr_cnt, dly;
    reg [15:0] x, y, isr, osr, divc, crc;
    reg [7:0] val, dir, tick_prev;
    reg clk_prev, rs_prev;
    reg [2:0] tx_run, rx_run;
    reg tx_last, tx_ph, tx_cur, tx_act, tq, tqv, rx_last, rx_prev, rq, rqv;
    reg [15:0] txbuf, rxbuf;
    reg [15:0] ir;                      // instruction register: mem[pc] as of the previous clock
    reg txv, rxv;
    reg [1:0] txn;
    assign pc_o = pc; assign en_o = en; assign val_o = val; assign dir_o = dir;
    assign txv_o = txv; assign rxv_o = rxv; assign rxbuf_o = rxbuf;

    // ---- tick
    wire [7:0] ins_all = in_fast ? pa : pb;
    wire run = en & ~blocked;
    wire rs_hit = resync & (ins_all[in_base] != rs_prev);
    wire clk_cur = ins_all[clk_pin];
    wire rise = clk_cur & ~clk_prev, fall = ~clk_cur & clk_prev;
    wire tick = line ? 1'b1
              : (clksrc == 2'd0) ? (~rs_hit & divc == 16'd0)
              : (clksrc == 2'd1) ? rise : (clksrc == 2'd2) ? fall : (rise | fall);
    wire forced = act_exec & ~en;
    wire issue_n = run & tick & dly == 5'd0;
    wire issue = issue_n | forced;
    wire [15:0] ins = forced ? exec_ins : ir;

    // ---- decode helpers
    wire [2:0] op = ins[15:13];
    wire [4:0] fld = ins[12:8];
    wire [4:0] dmask = 5'h1F >> side_count;
    wire [4:0] delay = fld & dmask;
    wire [1:0] side_bits = (side_count > {1'b0, side_en}) ? side_count - {1'b0, side_en} : 2'd0;
    wire side_on = (side_count != 2'd0) & (~side_en | fld[4]);
    wire [2:0] dbits = 3'd5 - {1'b0, side_count};
    wire [4:0] side_raw = fld >> dbits;
    wire [7:0] rot_in = (ins_all >> in_base) | (ins_all << (4'd8 - {1'b0, in_base}));
    wire [4:0] cnt = (ins[4:0] == 5'd0 || ins[4:0] > 5'd16) ? 5'd16 : ins[4:0];
    wire [15:0] cmask = (cnt == 5'd16) ? 16'hFFFF : ((16'd1 << cnt) - 16'd1);
    wire [15:0] status_v = (status_sel ? rxv : ~txv) ? 16'hFFFF : 16'h0000;

    function [15:0] crc_step(input [15:0] c, input b, input refl, input [15:0] p);
        crc_step = refl ? ((c >> 1) ^ ((c[0] ^ b) ? p : 16'h0000))
                        : ((c << 1) ^ ((c[15] ^ b) ? p : 16'h0000));
    endfunction
    function [15:0] rev16(input [15:0] v);
        integer k;
        for (k = 0; k < 16; k = k + 1) rev16[k] = v[15 - k];
    endfunction
    function [15:0] pull_word(input [15:0] e, input f16, input left);
        pull_word = f16 ? e : (left ? {e[7:0], 8'h00} : {8'h00, e[7:0]});
    endfunction
    function [15:0] push_word(input [15:0] v, input f16, input left);
        push_word = f16 ? v : (left ? {8'h00, v[7:0]} : {8'h00, v[15:8]});
    endfunction

    function [7:0] rotl8(input [7:0] v, input [2:0] r);
        rotl8 = (v << r) | (v >> (4'd8 - {1'b0, r}));
    endfunction
    function [7:0] therm8(input [3:0] n);
        therm8 = (n >= 4'd8) ? 8'hFF : ((8'd1 << n) - 8'd1);
    endfunction
    // pin write data/mask (computed from the request regs below; combinational)
    wire [7:0] pw_rot, pw_mask, sd_rot, sd_mask;

    // ---- instruction execution (next-state, applied when issued)
    reg stall, jumped;
    reg [4:0] jpc;
    reg [15:0] n_x, n_y, n_isr, n_osr, n_crc, n_rxbuf;
    reg [4:0] n_isr_cnt, n_osr_cnt;
    reg [7:0] n_val, n_dir;
    reg pw_en, pw_dir, crc_upd, crc_bit;
    reg [2:0] pw_base;
    reg [3:0] pw_cnt;
    reg [7:0] pw_data;
    reg n_timeout, n_codeerr, n_irq_wait, n_txv_clr, n_rxv_set;
    reg n_tq_put, n_tq_bit, n_rq_take;
    reg [3:0] n_set, n_clr;
    // temporaries
    reg take, met, lvl, refill, stuff, done, raw, bit_v, bmode, ostall;
    reg [2:0] pin;
    reg [1:0] fsel;
    reg [4:0] ocnt, icnt;
    reg [5:0] sum6;
    reg [15:0] data, osrc, rest, mv, isr_new;
    integer k;

    always @(*) begin
        stall = 0; jumped = 0; jpc = 5'd0;
        n_x = x; n_y = y; n_isr = isr; n_osr = osr; n_crc = crc; n_rxbuf = rxbuf;
        n_isr_cnt = isr_cnt; n_osr_cnt = osr_cnt;
        n_val = val; n_dir = dir;
        pw_en = 0; pw_dir = 0; pw_base = 3'd0; pw_cnt = 4'd0; pw_data = 8'd0; crc_upd = 0; crc_bit = 0;
        n_timeout = timeout; n_codeerr = codeerr; n_irq_wait = irq_wait;
        n_txv_clr = 0; n_rxv_set = 0;
        n_tq_put = 0; n_tq_bit = 0; n_rq_take = 0;
        n_set = 4'd0; n_clr = 4'd0;
        take = 0; met = 0; lvl = 0; refill = 0; stuff = 0; done = 0; raw = 0; bit_v = 0; bmode = 0;
        ostall = 0; pin = 3'd0; fsel = 2'd0; ocnt = 5'd0; icnt = 5'd0; sum6 = 6'd0;
        data = 16'd0; osrc = 16'd0; rest = 16'd0; mv = 16'd0; isr_new = 16'd0;
        case (op)
            3'd0: begin                                                    // JMP
                case (ins[7:5])
                    3'd0: take = 1;
                    3'd1: take = (x == 16'd0);
                    3'd2: begin take = (x != 16'd0); n_x = x - 16'd1; end
                    3'd3: take = (y == 16'd0);
                    3'd4: begin take = (y != 16'd0); n_y = y - 16'd1; end
                    3'd5: take = (x != y);
                    3'd6: take = ins_all[jmp_pin];
                    default: case (jflag)
                        2'd0: take = (osr_cnt < pull_thresh);
                        2'd1: take = (crc == 16'd0);
                        2'd2: begin take = timeout; n_timeout = 0; end
                        default: begin take = codeerr; n_codeerr = 0; end
                    endcase
                endcase
                if (take) begin jumped = 1; jpc = ins[4:0]; end
            end
            3'd1: begin                                                    // WAIT
                if (ins[6:5] == 2'd3) begin
                    met = ins[7] ? (~tx_act & ~tqv) : tx_act;
                    if (!met) stall = 1;
                end else if (ins[6:5] == 2'd2) begin
                    fsel = ins[1:0] + (ins[4] ? SMN : 2'd0);
                    met = (flags[fsel] == ins[7]);
                    if (met & ins[7]) n_clr[fsel] = 1'b1;
                    if (!met) stall = 1;
                end else begin
                    pin = (ins[6:5] == 2'd1) ? in_base + ins[2:0] : ins[2:0];
                    lvl = ins_all[pin];
                    met = (lvl == ins[7]);
                    if (ins[3]) met = met & (tick_prev[pin] != ins[7]);
                    if (!met) begin
                        if (ins[4]) begin
                            if (x == 16'd0) n_timeout = 1;
                            else begin n_x = x - 16'd1; stall = 1; end
                        end else stall = 1;
                    end
                end
            end
            3'd2: begin                                                    // IN
                sum6 = {1'b0, isr_cnt} + {1'b0, cnt};
                icnt = (sum6 > 6'd16) ? 5'd16 : sum6[4:0];
                bmode = (ins[7:5] == 3'd0) & (cnt == 5'd1);
                if (autopush & (icnt >= push_thresh) & rxv) stall = 1;
                else begin
                    ostall = 0;
                    if (bmode & line_rx) begin
                        if (!rqv) ostall = 1;
                        else begin
                            n_rq_take = 1;
                            if (crc_in) begin crc_upd = 1; crc_bit = rq; end
                        end
                        data = {15'd0, rq};
                    end else begin
                        case (ins[7:5])
                            3'd0: data = {8'd0, rot_in};
                            3'd1: data = x;
                            3'd2: data = y;
                            3'd4: data = crc;
                            3'd6: data = isr;
                            3'd7: data = osr;
                            default: data = 16'd0;
                        endcase
                        data = data & cmask;
                    end
                    if (ostall) stall = 1;
                    else begin
                        isr_new = in_left ? ((isr << cnt) | data) : ((isr >> cnt) | (data << (5'd16 - cnt)));
                        n_isr = isr_new;
                        n_isr_cnt = icnt;
                        if (autopush & (icnt >= push_thresh)) begin
                            n_rxbuf = push_word(isr_new, fifo16, in_left);
                            n_rxv_set = 1; n_isr = 16'd0; n_isr_cnt = 5'd0;
                        end
                    end
                end
            end
            3'd3: begin                                                    // OUT
                refill = autopull & (osr_cnt >= pull_thresh);
                if (refill & ~txv) stall = 1;
                else begin
                    osrc = refill ? pull_word(txbuf, fifo16, out_left) : osr;
                    ocnt = refill ? 5'd0 : osr_cnt;
                    if (refill) begin n_txv_clr = 1; n_osr = osrc; n_osr_cnt = 5'd0; end
                    data = out_left ? ((osrc >> (5'd16 - cnt)) & cmask) : (osrc & cmask);
                    rest = out_left ? (osrc << cnt) : (osrc >> cnt);
                    ostall = 0;
                    if (ins[7:5] == 3'd0 && cnt == 5'd1 && line_tx) begin
                        if (tqv) ostall = 1;
                        else begin
                            n_tq_put = 1; n_tq_bit = data[0];
                            if (crc_out) begin crc_upd = 1; crc_bit = data[0]; end
                        end
                    end else begin
                        case (ins[7:5])
                            3'd0: begin pw_en = 1; pw_base = out_base; pw_cnt = out_count; pw_data = data[7:0]; end
                            3'd1: n_x = data;
                            3'd2: n_y = data;
                            3'd4: begin pw_en = 1; pw_dir = 1; pw_base = out_base; pw_cnt = out_count; pw_data = data[7:0]; end
                            3'd5: begin jumped = 1; jpc = data[4:0]; end
                            3'd6: begin n_isr = data; n_isr_cnt = cnt; end
                            3'd7: n_crc = data;
                            default: ;
                        endcase
                    end
                    if (ostall) stall = 1;
                    else begin
                        n_osr = rest;
                        sum6 = {1'b0, ocnt} + {1'b0, cnt};
                        n_osr_cnt = (sum6 > 6'd16) ? 5'd16 : sum6[4:0];
                    end
                end
            end
            3'd4: begin                                                    // PUSH / PULL
                if (!ins[7]) begin
                    if (!(ins[6] & (isr_cnt < push_thresh))) begin
                        if (rxv) begin
                            if (ins[5]) stall = 1;
                            else begin n_isr = 16'd0; n_isr_cnt = 5'd0; end
                        end else begin
                            n_rxbuf = push_word(isr, fifo16, in_left);
                            n_rxv_set = 1; n_isr = 16'd0; n_isr_cnt = 5'd0;
                        end
                    end
                end else begin
                    if (!(ins[6] & (osr_cnt < pull_thresh))) begin
                        if (!txv) begin
                            if (ins[5]) stall = 1;
                            else begin n_osr = x; n_osr_cnt = 5'd0; end
                        end else begin
                            n_osr = pull_word(txbuf, fifo16, out_left);
                            n_osr_cnt = 5'd0; n_txv_clr = 1;
                        end
                    end
                end
            end
            3'd5: begin                                                    // MOV
                case (ins[2:0])
                    3'd0: mv = {8'd0, rot_in};
                    3'd1: mv = x;
                    3'd2: mv = y;
                    3'd4: mv = crc;
                    3'd5: mv = status_v;
                    3'd6: mv = isr;
                    3'd7: mv = osr;
                    default: mv = 16'd0;
                endcase
                if (ins[4:3] == 2'd1) mv = ~mv;
                else if (ins[4:3] == 2'd2) mv = rev16(mv);
                case (ins[7:5])
                    3'd0: begin pw_en = 1; pw_base = out_base; pw_cnt = out_count; pw_data = mv[7:0]; end
                    3'd1: n_x = mv;
                    3'd2: n_y = mv;
                    3'd3: n_crc = mv;
                    3'd5: begin jumped = 1; jpc = mv[4:0]; end
                    3'd6: begin n_isr = mv; n_isr_cnt = 5'd0; end
                    3'd7: begin n_osr = mv; n_osr_cnt = 5'd0; end
                    default: ;
                endcase
            end
            3'd6: begin                                                    // IRQ
                fsel = ins[1:0] + (ins[4] ? SMN : 2'd0);
                if (ins[6]) n_clr[fsel] = 1'b1;
                else if (!ins[5]) n_set[fsel] = 1'b1;
                else if (!irq_wait) begin n_set[fsel] = 1'b1; n_irq_wait = 1; stall = 1; end
                else if (flags[fsel]) stall = 1;
                else n_irq_wait = 0;
            end
            default: begin                                                 // SET
                case (ins[7:5])
                    3'd0: begin pw_en = 1; pw_base = set_base; pw_cnt = {1'b0, set_count}; pw_data = {3'd0, ins[4:0]}; end
                    3'd1: n_x = {11'd0, ins[4:0]};
                    3'd2: n_y = {11'd0, ins[4:0]};
                    3'd4: begin pw_en = 1; pw_dir = 1; pw_base = set_base; pw_cnt = {1'b0, set_count}; pw_data = {3'd0, ins[4:0]}; end
                    default: ;
                endcase
            end
        endcase
        // one shared rotate-and-mask path for the instruction's pin write
        if (pw_en) begin
            if (pw_dir) n_dir = (dir & ~pw_mask) | (pw_rot & pw_mask);
            else n_val = (val & ~pw_mask) | (pw_rot & pw_mask);
        end
        if (crc_upd) n_crc = crc_step(crc, crc_bit, crc_reflect, poly);
        // side-set wins over the instruction's own pin writes
        if (side_on) begin
            if (side_pindir) n_dir = (n_dir & ~sd_mask) | (sd_rot & sd_mask);
            else n_val = (n_val & ~sd_mask) | (sd_rot & sd_mask);
        end
        irq_set = n_set;
        irq_clr = n_clr;
        if (!issue | (forced & stall)) begin irq_set = 4'd0; irq_clr = 4'd0; end
    end

    assign pw_rot = rotl8(pw_data, pw_base);
    assign pw_mask = rotl8(therm8(pw_cnt), pw_base);
    assign sd_rot = rotl8({5'd0, side_raw[2:0]}, side_base);
    assign sd_mask = rotl8(therm8({2'b00, side_bits}), side_base);
    wire commit = issue & ~(forced & stall);
    wire [4:0] next_pc = jumped ? jpc : ((pc == wrap_top) ? wrap_bottom : pc + 5'd1);
    assign pcn_o = act_restart ? restart_pc
                 : (commit & issue_n & ~stall) ? next_pc
                 : (commit & forced & jumped) ? jpc : pc;

    // ---- line unit (serializer / deserializer), runs every clock in line mode
    wire lrun = run & line;
    wire sbit = ins_all[in_base];
    wire edge_in = sbit != rs_prev;
    wire expire = divc == 16'd0;
    wire man = line_rx & (dec == 2'd3);
    wire man_acc = man & edge_in & expire;                          // accepted mid-bit edge
    wire rs_line = ~man & line_rx & resync & edge_in;
    wire evt = man ? man_acc : (~rs_line & expire);                 // line bit event
    wire rx_evt = line_rx & ~man & evt;
    wire rx_dbit = (dec == 2'd1) ? (sbit == rx_prev) : (dec == 2'd2) ? (sbit != rx_prev) : sbit;
    wire rx_stuffpos = (stuff_n != 3'd0) & (rx_run >= stuff_n) & (~stuff_ones | rx_last);
    wire rx_q = man_acc | (rx_evt & ~rx_stuffpos);                  // a bit is queued
    wire rx_qbit = man_acc ? sbit : rx_dbit;
    wire tx_evt = line_tx & evt;
    wire tx_second = (enc == 2'd3) & tx_ph;
    wire tx_stuffpos = (stuff_n != 3'd0) & (tx_run >= stuff_n) & (~stuff_ones | tx_last);
    wire tx_stuffbit = stuff_ones ? 1'b0 : ~tx_last;
    wire tx_bit = tx_stuffpos ? tx_stuffbit : tq;
    wire tx_new = ~tx_second & (tx_stuffpos | tqv);                 // a new line bit starts
    wire cur_lvl = val[out_base];
    wire tx_lvl = tx_second ? tx_cur
                : (enc == 2'd3) ? ~tx_bit
                : (enc == 2'd1) ? (tx_bit ? cur_lvl : ~cur_lvl)
                : (enc == 2'd2) ? (tx_bit ? ~cur_lvl : cur_lvl) : tx_bit;
    wire tx_drive = tx_evt & (tx_second | tx_new);
    wire [7:0] val_i = commit ? n_val : val;                        // instruction result first
    reg [7:0] val_l;
    always @(*) begin
        val_l = val_i;
        if (tx_drive) begin
            val_l[out_base] = tx_lvl;
            if (diff) val_l[out_base + 3'd1] = ~tx_lvl;
        end
    end
    wire [2:0] run_inc_rx = (rx_dbit == rx_last && rx_run != 3'd0) ? ((rx_run == 3'd7) ? 3'd7 : rx_run + 3'd1) : 3'd1;
    wire [2:0] run_inc_tx = (tq == tx_last && tx_run != 3'd0) ? ((tx_run == 3'd7) ? 3'd7 : tx_run + 3'd1) : 3'd1;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            en <= 0; pc <= 0; x <= 0; y <= 0; isr <= 0; osr <= 0; isr_cnt <= 0; osr_cnt <= 5'd16;
            dly <= 0; divc <= 0; val <= 0; dir <= 0; tick_prev <= 0; clk_prev <= 0; rs_prev <= 0;
            crc <= 0; tx_run <= 0; tx_last <= 0; tx_ph <= 0; tx_cur <= 0; tx_act <= 0; tq <= 0; tqv <= 0;
            rx_run <= 0; rx_last <= 0; rx_prev <= 0; rq <= 0; rqv <= 0;
            timeout <= 0; codeerr <= 0; irq_wait <= 0;
            txbuf <= 0; txv <= 0; txn <= 0; rxbuf <= 0; rxv <= 0; ir <= 0;
        end else begin
            ir <= fetched;
            clk_prev <= clk_cur;
            rs_prev <= sbit;
            // host side of the buffers
            if (host_wnib & ~txv) begin
                txbuf[{txn, 2'b00} +: 4] <= nib;
                if (!fifo16) txbuf[15:8] <= 8'h00;
                if (txn >= (fifo16 ? 2'd3 : 2'd1)) begin txv <= 1; txn <= 2'd0; end
                else txn <= txn + 2'd1;
            end
            if (host_pop) rxv <= 0;
            if (act_restart) begin
                pc <= restart_pc; isr <= 0; osr <= 0; isr_cnt <= 0; osr_cnt <= 5'd16;
                dly <= 0; divc <= 0;
                tx_run <= 0; tx_last <= 0; tx_ph <= 0; tx_cur <= 0; tx_act <= 0; tq <= 0; tqv <= 0;
                rx_run <= 0; rx_last <= 0; rx_prev <= sbit; rq <= 0; rqv <= 0;
                timeout <= 0; codeerr <= 0; irq_wait <= 0;
            end else begin
                if (run & ~line & (clksrc == 2'd0))
                    divc <= rs_hit ? (div >> 1) : (divc == 16'd0) ? div : divc - 16'd1;
                if (run & tick) begin
                    tick_prev <= ins_all;
                    if (dly != 5'd0) dly <= dly - 5'd1;
                end
                if (commit) begin
                    x <= n_x; y <= n_y; isr <= n_isr; osr <= n_osr; crc <= n_crc;
                    isr_cnt <= n_isr_cnt; osr_cnt <= n_osr_cnt;
                    val <= n_val; dir <= n_dir;
                    timeout <= n_timeout; codeerr <= n_codeerr; irq_wait <= n_irq_wait;
                    if (n_txv_clr) begin txv <= 0; txn <= 2'd0; end
                    if (n_rxv_set) begin rxbuf <= n_rxbuf; rxv <= 1; end
                    if (n_tq_put) begin tq <= n_tq_bit; tqv <= 1; end
                    if (n_rq_take) rqv <= 0;
                    if (issue_n & ~stall) begin dly <= delay; pc <= next_pc; end
                    else if (forced & jumped) pc <= jpc;
                end
                if (lrun) begin
                    if (man) begin
                        if (man_acc) divc <= div;
                        else if (divc != 16'd0) divc <= divc - 16'd1;
                    end else if (rs_line) divc <= div >> 1;
                    else divc <= expire ? div : divc - 16'd1;
                    if (rx_evt) begin
                        rx_prev <= sbit;
                        if (rx_stuffpos) begin
                            if (rx_dbit != (stuff_ones ? 1'b0 : ~rx_last)) codeerr <= 1;
                            rx_last <= rx_dbit; rx_run <= 3'd1;
                        end else begin
                            rx_run <= run_inc_rx; rx_last <= rx_dbit;
                        end
                    end
                    if (rx_q) begin
                        if (rqv & ~(commit & n_rq_take)) codeerr <= 1;      // overrun
                        rq <= rx_qbit; rqv <= 1;
                    end
                    if (tx_evt) begin
                        if (tx_second) tx_ph <= 0;
                        else if (tx_stuffpos) begin
                            tx_last <= tx_stuffbit; tx_run <= 3'd1; tx_act <= 1;
                        end else if (tqv) begin
                            tqv <= 0; tx_run <= run_inc_tx; tx_last <= tq; tx_act <= 1;
                        end else begin
                            tx_act <= 0; tx_run <= 3'd0;
                        end
                        if (tx_new & (enc == 2'd3)) begin tx_cur <= tx_bit; tx_ph <= 1; end
                    end
                    val <= val_l;
                end
            end
            if (act_en) begin
                en <= en_bit;
                if (en_bit & ~en) divc <= 16'd0;
            end
        end
    end
endmodule

// Clock gate from discrete cells (the PDK excludes its ICG cell from the flow):
// transparent-low enable latch, a delay cell for gating hold margin, AND gate.
module chipwheel3_icg (input wire clk, input wire en, output wire gclk);
    wire en_l, en_d;
    sg13cmos5l_dllrq_1 hold (.GATE_N(clk), .D(en), .RESET_B(1'b1), .Q(en_l));
    sg13cmos5l_dlygate4sd3_1 skew (.A(en_l), .X(en_d));
    sg13cmos5l_and2_1 gate (.A(clk), .B(en_d), .X(gclk));
endmodule

module chipwheel3_latch16 (input wire g, input wire [15:0] d, output wire [15:0] q);
    genvar i;
    generate
        for (i = 0; i < 16; i = i + 1) begin : g_bit
            sg13cmos5l_dlhq_1 bit_latch (.GATE(g), .D(d[i]), .Q(q[i]));
        end
    endgenerate
endmodule
