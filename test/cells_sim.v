// Behavioral models of the IHP CMOS5L cells that src/project.v instantiates,
// for RTL simulation only. Gate-level runs use the PDK models instead.
`default_nettype none
`ifndef GL_TEST
module sg13cmos5l_dlhq_1 (input wire GATE, input wire D, output reg Q);
    always @(*) if (GATE) Q = D;
endmodule

module sg13cmos5l_dllrq_1 (input wire GATE_N, input wire D, input wire RESET_B, output reg Q);
    always @(*) if (!RESET_B) Q = 1'b0; else if (!GATE_N) Q = D;
endmodule

module sg13cmos5l_and2_1 (input wire A, input wire B, output wire X);
    assign X = A & B;
endmodule
`endif
