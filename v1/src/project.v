/* SPDX-License-Identifier: Apache-2.0 */
`default_nettype none
module tt_um_chipwheel (
    input wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input wire ena, clk, rst_n
);
    reg [15:0] program_mem [0:31];
    reg [4:0] pc;
    reg [11:0] wait_left;
    reg [7:0] data_shift, loop_count;
    reg pin_value, busy, error_flag, start_prev;
    wire [15:0] instruction = program_mem[pc];
    wire [3:0] opcode = instruction[15:12];
    wire [11:0] argument = instruction[11:0];
    wire start_edge = uio_in[0] && !start_prev;
    assign uo_out = {5'b0, error_flag, busy, pin_value};
    assign uio_out = 8'b0;
    assign uio_oe = 8'b0;

    task fault;
        begin
            busy <= 0;
            pin_value <= 1;
            error_flag <= 1;
        end
    endtask
    task advance;
        begin
            if (pc == 31) fault;
            else pc <= pc + 1'b1;
        end
    endtask

    always @(posedge clk) begin
        if (!rst_n) begin
            pc <= 0;
            wait_left <= 0;
            data_shift <= 0;
            loop_count <= 0;
            pin_value <= 1;
            busy <= 0;
            error_flag <= 0;
            start_prev <= 0;
        end else begin
            start_prev <= uio_in[0];
            if (ena) begin
                if (!busy) begin
                    if (uio_in[1]) begin
                        if (uio_in[2]) program_mem[uio_in[7:3]][15:8] <= ui_in;
                        else program_mem[uio_in[7:3]][7:0] <= ui_in;
                        if (start_edge) error_flag <= 1;
                    end else if (start_edge) begin
                        pc <= 0;
                        wait_left <= 0;
                        loop_count <= 0;
                        data_shift <= ui_in;
                        pin_value <= 1;
                        busy <= 1;
                        error_flag <= 0;
                    end
                end else begin
                    if (uio_in[1]) error_flag <= 1;
                    if (wait_left != 0) wait_left <= wait_left - 1'b1;
                    else begin
                        case (opcode)
                            0: if (argument != 0) fault;
                               else begin busy <= 0; pin_value <= 1; end
                            1: if (argument > 1) fault;
                               else begin pin_value <= argument[0]; advance; end
                            2: begin wait_left <= argument; advance; end
                            3: if (argument != 0) fault;
                               else begin
                                   pin_value <= data_shift[0];
                                   data_shift <= {1'b0, data_shift[7:1]};
                                   advance;
                               end
                            4: if (argument > 255) fault;
                               else begin loop_count <= argument[7:0]; advance; end
                            5: if (argument > 31) fault;
                               else if (loop_count > 1) begin
                                   loop_count <= loop_count - 1'b1;
                                   pc <= argument[4:0];
                               end else begin loop_count <= 0; advance; end
                            6: if (argument > 31) fault;
                               else pc <= argument[4:0];
                            default: fault;
                        endcase
                    end
                end
            end
        end
    end
endmodule
