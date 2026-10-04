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
    tt_um_chipwheel dut (
        .ui_in(ui_in), .uo_out(uo_out), .uio_in(uio_in), .uio_out(uio_out),
        .uio_oe(uio_oe), .ena(ena), .clk(clk), .rst_n(rst_n));
`else
    tt_um_chipwheel dut (.*);
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
