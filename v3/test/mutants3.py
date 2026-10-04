"""Mutation check for Chipwheel v3: every injected RTL fault must be caught by
the protocol suite or a short random differential run. Faults live in copies
under v3/build/; the real RTL is never edited. Writes v3/results/mutants.json.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RTL = HERE.parent / 'src/chipwheel3.v'
BUILD = HERE.parent / 'build'
PY = sys.executable

MUTANTS = {
    'jmp_x_no_decrement': ("3'd2: begin take = (x != 16'd0); n_x = x - 16'd1; end",
                           "3'd2: begin take = (x != 16'd0); end"),
    'side_set_ignored': ("        if (side_on) begin", "        if (1'b0) begin"),
    'autopull_ignored': ("refill = autopull & (osr_cnt >= pull_thresh);", "refill = 1'b0;"),
    'nrzi_inverted': (": (enc == 2'd1) ? (tx_bit ? cur_lvl : ~cur_lvl)", ": (enc == 2'd1) ? (tx_bit ? ~cur_lvl : cur_lvl)"),
    'no_tx_stuffing': ("wire tx_stuffpos = (stuff_n != 3'd0) & (tx_run >= stuff_n)", "wire tx_stuffpos = 1'b0 & (tx_run >= stuff_n)"),
    'no_rx_destuffing': ("wire rx_stuffpos = (stuff_n != 3'd0) & (rx_run >= stuff_n)", "wire rx_stuffpos = 1'b0 & (rx_run >= stuff_n)"),
    'crc_reflect_wrong': ("crc_step = refl ? ((c >> 1) ^ ((c[0] ^ b) ? p : 16'h0000))",
                          "crc_step = refl ? ((c >> 1) ^ ((c[15] ^ b) ? p : 16'h0000))"),
    'manchester_halves_swapped': (": (enc == 2'd3) ? ~tx_bit", ": (enc == 2'd3) ? tx_bit"),
    'wait_timeout_never': ("if (x == 16'd0) n_timeout = 1;", "if (1'b0) n_timeout = 1;"),
    'irq_wait_never_clears': ("else n_irq_wait = 0;", "else stall = 1;"),
    'owner_mask_ignored': ("wire [7:0] pval = (val1 & owner) | (val0 & ~owner);", "wire [7:0] pval = val0;"),
    'edge_clock_inverted': ("wire rise = clk_cur & ~clk_prev, fall = ~clk_cur & clk_prev;",
                            "wire rise = ~clk_cur & clk_prev, fall = clk_cur & ~clk_prev;"),
    'resync_disabled': ("wire rs_line = ~man & line_rx & resync & edge_in;", "wire rs_line = 1'b0;"),
    'in_shift_dir_swapped': ("isr_new = in_left ? ((isr << cnt) | data)", "isr_new = ~in_left ? ((isr << cnt) | data)"),
    'pull_byte_wrong_end': ("pull_word = f16 ? e : (left ? {e[7:0], 8'h00} : {8'h00, e[7:0]});",
                            "pull_word = f16 ? e : (left ? {8'h00, e[7:0]} : {8'h00, e[7:0]});"),
    'prefetch_stale': ("            ir <= fetched;\n", "            if (~run) ir <= fetched;\n"),
    'od_drives_high': ("assign uio_out = pval & ~od_mask;", "assign uio_out = pval;"),
    'manchester_rx_no_blanking': ("wire man_acc = man & edge_in & expire;", "wire man_acc = man & edge_in;"),
    'link_source_not_popped': (".host_pop(pop0 | mv01)", ".host_pop(pop0)"),
    'link_host_write_not_locked': (".host_wnib(wt_ev & ch == 2'd1 & ~link01)", ".host_wnib(wt_ev & ch == 2'd1)"),
    'link_overwrites_full_tx': ("wire mv01 = link01 & rxv0 & ~txv1;", "wire mv01 = link01 & rxv0;"),
    'ddr_no_falling_sample': ("pa <= uio_in; pb <= pa; pnr <= pn;", "pa <= uio_in; pb <= pa; pnr <= pa;"),
    'ddr_sample_order_swapped': ("wire [7:0] ddr_e = in_fast ? pnr : pb, ddr_l = in_fast ? pa : pnr;",
                                 "wire [7:0] ddr_e = in_fast ? pnr : pnr, ddr_l = in_fast ? pa : pb;"),
    'ddr_ignores_shift_dir': ("wire [7:0] ddr_lo = rotr8(in_left ? ddr_l : ddr_e, in_base);",
                              "wire [7:0] ddr_lo = rotr8(ddr_e, in_base);"),
    'time_counter_lsb_stuck': ("3'd5: data = now;", "3'd5: data = {now[15:1], 1'b0};"),
}


def run(env, args):
    r = subprocess.run([PY] + args, cwd=HERE, env=env, capture_output=True, text=True)
    return r.returncode


def main():
    src = RTL.read_text()
    BUILD.mkdir(exist_ok=True)
    results = {}
    for name, (a, b) in MUTANTS.items():
        assert a in src, f'{name}: pattern not found'
        path = BUILD / f'mutant3_{name}.v'
        path.write_text(src.replace(a, b, 1))
        env = dict(os.environ, CW3_RTL=str(path))
        caught = None
        if run(env, ['-m', 'pytest', '-q', '-x', 'test_protocols3.py', '-p', 'no:cacheprovider']):
            caught = 'protocol tests'
        elif run(env, ['fuzz3.py', '--cases', '150', '--cycles', '3000', '--seed', '9']):
            caught = 'fuzz'
        results[name] = caught
        print(f'{name:28s} {caught or "NOT CAUGHT"}', flush=True)
    out = HERE.parent / 'results/mutants.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=1))
    sys.exit(0 if all(results.values()) else 1)


if __name__ == '__main__':
    main()
