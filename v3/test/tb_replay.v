`default_nettype none
`timescale 1ns / 1ps

// Trace-replay bench: applies recorded inputs one per clock (1 ns after the
// falling edge) and compares every output a quarter period after each rising
// edge with the reference-model expectation. Stops at the first mismatch.
module tb_replay;
    reg clk = 0, rst_n = 0, ena = 1;
    reg [7:0] ui_in = 0, uio_in = 0;
    wire [7:0] uo_out, uio_out, uio_oe;

`ifdef GL_TEST
    tt_um_chipwheel3 dut (
        .ui_in(ui_in), .uo_out(uo_out), .uio_in(uio_in), .uio_out(uio_out),
        .uio_oe(uio_oe), .ena(ena), .clk(clk), .rst_n(rst_n));
`else
    tt_um_chipwheel3 dut (.*);
`endif

    integer fs, fe, rc, cycle, errors;
    reg [31:0] s, e;
    reg [1023:0] stim_name, exp_name;
    initial begin
        if (!$value$plusargs("stim=%s", stim_name)) $fatal(1, "no +stim");
        if (!$value$plusargs("exp=%s", exp_name)) $fatal(1, "no +exp");
        if ($test$plusargs("waves")) begin
            $dumpfile("replay.fst");
            $dumpvars(0, tb_replay);
        end
        fs = $fopen(stim_name, "r");
        fe = $fopen(exp_name, "r");
        cycle = 0;
        errors = 0;
        #10;
        while (!$feof(fs) && errors == 0) begin
            rc = $fscanf(fs, "%h\n", s);
            if (rc == 1) begin
                rc = $fscanf(fe, "%h\n", e);
                #1 {rst_n, ui_in, uio_in} = s[16:0];
                #9 clk = 1;
                #5;
`ifdef STATE_TRACE
                $display("ST %0d %0d %0d %0d %0d %h %h %h %h %0d %0d %h %h %0d %0d %0d %0d %h %h %h %h %0d %0d %h %h %0d %0d", cycle,
                    dut.sm0.pc, dut.sm0.en, dut.sm0.dly, dut.sm0.divc, dut.sm0.x, dut.sm0.y, dut.sm0.isr, dut.sm0.osr,
                    dut.sm0.isr_cnt, dut.sm0.osr_cnt, dut.sm0.val, dut.sm0.dir, dut.sm0.txv, dut.sm0.rxv,
                    dut.sm1.pc, dut.sm1.en, dut.sm1.x, dut.sm1.y, dut.sm1.isr, dut.sm1.osr,
                    dut.sm1.isr_cnt, dut.sm1.osr_cnt, dut.sm1.val, dut.sm1.dir, dut.sm1.dly, dut.sm1.divc);
`endif
`ifdef INS_TRACE
                $display("IT %0d ins0=%h fetched=%h tick=%b issue=%b forced=%b stall=%b clk_prev=%b clk_cur=%b pa=%h mem0=%h",
                    cycle, dut.sm0.ins, dut.ins0, dut.sm0.tick, dut.sm0.issue, dut.sm0.forced, dut.sm0.stall,
                    dut.sm0.clk_prev, dut.sm0.clk_cur, dut.pa, dut.mem[0]);
`endif
                if (uo_out !== e[31:24] || uio_oe !== e[15:8] ||
                    ((uio_out ^ e[23:16]) & e[7:0]) !== 8'h00) begin
                    $display("MISMATCH cycle %0d: uo_out=%h exp %h uio_out=%h exp %h (mask %h) uio_oe=%h exp %h",
                             cycle, uo_out, e[31:24], uio_out, e[23:16], e[7:0], uio_oe, e[15:8]);
                    errors = errors + 1;
                end
                #5 clk = 0;
                cycle = cycle + 1;
            end
        end
        $display("REPLAY cycles=%0d errors=%0d", cycle, errors);
        $finish;
    end
endmodule
