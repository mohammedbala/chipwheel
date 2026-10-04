#!/usr/bin/env python3
"""Build, simulate and measure the incumbent reference designs.

    python3 incumbents/run_all.py            # all simulations + generic Yosys synth
    python3 incumbents/run_all.py --no-synth # simulations only (fast)
    python3 incumbents/run_all.py --only pio_sm1 serv_bb

For every incumbent this assembles the protocol programs (UART TX 8N1 and
SPI mode-0 master), loads them through the top's host ports in an Icarus
testbench, decodes the pin waveform, checks the bytes, and writes
incumbents/results.json (raw data and logs in incumbents/work/). With synthesis
enabled it also runs generic Yosys synthesis through incumbents/yosys.sh
(`synth -flatten` -> generic_cells, plain `synth` -> generic_cells_hier) and
re-runs the primary UART/SPI cases on the flattened gate-level netlist.
"""
import argparse
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

INC = Path(__file__).resolve().parent
ROOT = INC.parent
sys.path.insert(0, str(INC / 'common'))
from pioasm import Pio, grps, pend, shift  # noqa: E402
from rvasm import assemble  # noqa: E402

IVERILOG = ROOT / '.tools/icarus-verilog/13.0/bin/iverilog'
VVP = ROOT / '.tools/icarus-verilog/13.0/bin/vvp'
YOSYS = INC / 'yosys.sh'
SIMCELLS = ROOT / '.tools/flow-venv/lib/python3.13/site-packages/yowasp_yosys/share/simcells.v'
WORK = INC / 'work'

UART_BYTES = [0x41, 0x55, 0xA5, 0x00, 0xFF, 0x5A, 0x3C, 0x81]
SPI_TX = [0x41, 0x55, 0xA5, 0x00, 0xFF, 0x5A, 0x3C, 0x81]
SPI_SLAVE = [0x96, 0x0F, 0xF0, 0x33, 0xCC, 0x01, 0x80, 0x7E]


def rel(p):
    return str(Path(p).resolve().relative_to(ROOT))


# pio.v / machine.v: upstream + minimal Yosys/ASIC patches (pio_sm1/patched/fpga_pio.patch);
# the other seven files are byte-identical upstream.
PIO_SRC = [INC / 'pio_sm1/patched/pio.v', INC / 'pio_sm1/patched/machine.v'] + \
          [INC / 'upstream/fpga_pio/src' / f for f in
           ['decoder.v', 'divider.v', 'pc.v', 'scratch.v', 'fifo.v', 'isr.v', 'osr.v']]
SERV_SRC = [INC / 'upstream/serv/rtl' / f for f in
            ['serv_bufreg.v', 'serv_bufreg2.v', 'serv_alu.v', 'serv_csr.v', 'serv_ctrl.v',
             'serv_decode.v', 'serv_immdec.v', 'serv_mem_if.v', 'serv_rf_if.v',
             'serv_rf_ram_if.v', 'serv_rf_ram.v', 'serv_state.v', 'serv_debug.v',
             'serv_top.v', 'serv_rf_top.v', 'serv_aligner.v', 'serv_compdec.v']]
# patched/femtorv32_quark.v = upstream + Icarus declaration-order fix + x0 ASIC fix (see .patch)
FEMTO_SRC = [INC / 'femtorv_bb/femtorv_cfg.v', INC / 'femtorv_bb/patched/femtorv32_quark.v']

DESIGNS = {
    'pio_sm1': dict(top='inc_pio_sm1', sources=PIO_SRC + [INC / 'pio_sm1/top.v'],
                    tb=INC / 'pio_sm1/tb/tb.v', kind='pio', tb_defines=[]),
    'pio_full': dict(top='inc_pio_full', sources=PIO_SRC + [INC / 'pio_full/top.v'],
                     tb=INC / 'pio_full/tb/tb.v', kind='pio', tb_defines=[]),
    'serv_bb': dict(top='inc_serv_bb', sources=SERV_SRC + [INC / 'serv_bb/top.v'],
                    tb=INC / 'serv_bb/tb/tb.v', kind='cpu', tb_defines=[]),
    'qerv_bb': dict(top='inc_qerv_bb',
                    sources=SERV_SRC + [INC / 'serv_bb/top.v', INC / 'qerv_bb/top.v'],
                    tb=INC / 'qerv_bb/tb/tb.v', kind='cpu', tb_defines=[]),
    'femtorv_bb': dict(top='inc_femtorv_bb', sources=FEMTO_SRC + [INC / 'femtorv_bb/top.v'],
                       tb=INC / 'femtorv_bb/tb/tb.v', kind='cpu',
                       # BENCH: upstream's simulation-only initial block (aluShamt = 0,
                       # cycles = 0, x0 = 0) so Icarus does not hang on X
                       tb_defines=['BENCH'],
                       # gate-level stand-in for BENCH: Quark never resets aluShamt
                       # (harmless in silicon -- it counts down to 0 -- but X in sim)
                       gate_init='module gate_init;\n'
                                 '  initial tb.dut.\\cpu.aluShamt  = 5\'d0;\n'
                                 'endmodule\n'),
}

# ---------------------------------------------------------------- programs
P_OPT = Pio(side_bits=1, side_opt=True)    # .side_set 1 opt
P_SS = Pio(side_bits=1)                    # .side_set 1
P0 = Pio()

PIO_PROGRAMS = {
    # Canonical PIO UART TX (pull / start bit / 8 x out / stop bit), unrolled so
    # every bit is exactly one instruction = 1 clock at clkdiv 1.
    'uart_1cpb': dict(
        prog=[P_OPT.pull(block=True, side=1),        # stop bit / idle, wait for data
              P_OPT.nop(side=0)]                     # start bit
             + [P_OPT.out('pins', 1)] * 8,           # d0..d7, wrap -> pull
        asm=['pull block side 1', 'nop side 0'] + ['out pins, 1'] * 8,
        grps=grps(out_base=0, set_base=0, side_base=0, out_count=1, set_count=1, side_count=2),
        pend=pend(wrap_top=9, wrap_target=0, sideset_enable_bit=1),
        shift=shift(out_right=1), imm=P0.set('pindirs', 1), txshift=0),
    # pico-examples uart_tx loop with all delays removed (2 clocks per bit).
    'uart_loop_2cpb': dict(
        prog=[P_OPT.pull(block=True, side=1, delay=1),
              P_OPT.set('x', 7, side=0, delay=1),
              P_OPT.out('pins', 1),
              P_OPT.jmp(2, 'x--')],
        asm=['pull block side 1 [1]', 'set x, 7 side 0 [1]', 'bitloop: out pins, 1',
             'jmp x-- bitloop'],
        grps=grps(out_base=0, set_base=0, side_base=0, out_count=1, set_count=1, side_count=2),
        pend=pend(wrap_top=3, wrap_target=0, sideset_enable_bit=1),
        shift=shift(out_right=1), imm=P0.set('pindirs', 1), txshift=0),
    # pico-examples spi_cpha0 structure (out pins side 0 / in pins side 1),
    # unrolled 8x with explicit pull/push. One delay cycle between `out` and
    # `in` is the minimum that samples MISO correctly (see README).
    'spi_3cpb': dict(
        prog=[P_SS.pull(block=True, side=0)]
             + [P_SS.out('pins', 1, side=0, delay=1), P_SS.in_('pins', 1, side=1)] * 8
             + [P_SS.push(block=True, side=0)],
        asm=['pull block side 0'] + ['out pins, 1 side 0 [1]', 'in pins, 1 side 1'] * 8
            + ['push block side 0'],
        grps=grps(out_base=0, set_base=0, side_base=1, in_base=0, out_count=1, set_count=2,
                  side_count=1),
        pend=pend(wrap_top=17, wrap_target=0), shift=shift(), imm=P0.set('pindirs', 3),
        txshift=24),
    # 2 clocks/bit SPI that is correct on upstream: the GPIO output path has one
    # register more than the input path, so an `in` sees the pins as they were
    # two instructions earlier, i.e. it samples MISO during the PREVIOUS bit's
    # SCK-high phase. So bit 7's high slot only raises SCK (nop), the `in` of
    # slots 6..0 sample bits 7..1, and one extra `in ... side 0` right after the
    # last pulse samples bit 0 (still inside its high phase on the pins).
    'spi_2cpb': dict(
        prog=[P_SS.pull(block=True, side=0),
              P_SS.out('pins', 1, side=0), P_SS.nop(side=1)]
             + [P_SS.out('pins', 1, side=0), P_SS.in_('pins', 1, side=1)] * 7
             + [P_SS.in_('pins', 1, side=0),
                P_SS.push(block=True, side=0)],
        asm=['pull block side 0', 'out pins, 1 side 0', 'nop side 1']
            + ['out pins, 1 side 0', 'in pins, 1 side 1'] * 7
            + ['in pins, 1 side 0', 'push block side 0'],
        grps=grps(out_base=0, set_base=0, side_base=1, in_base=0, out_count=1, set_count=2,
                  side_count=1),
        pend=pend(wrap_top=18, wrap_target=0), shift=shift(), imm=P0.set('pindirs', 3),
        txshift=24),
    # spi_3cpb without the delay (plain spi_cpha0 at 2 clocks/bit). Expected to
    # FAIL on upstream: MISO is read one bit late (output path has one extra register).
    'spi_2cpb_negative': dict(
        prog=[P_SS.pull(block=True, side=0)]
             + [P_SS.out('pins', 1, side=0), P_SS.in_('pins', 1, side=1)] * 8
             + [P_SS.push(block=True, side=0)],
        asm=['pull block side 0'] + ['out pins, 1 side 0', 'in pins, 1 side 1'] * 8
            + ['push block side 0'],
        grps=grps(out_base=0, set_base=0, side_base=1, in_base=0, out_count=1, set_count=2,
                  side_count=1),
        pend=pend(wrap_top=17, wrap_target=0), shift=shift(), imm=P0.set('pindirs', 3),
        txshift=24),
}

# I/O map (x0-relative): -16 GPIO_OUT, -12 GPIO_IN, -8 TX_MBOX, -4 RX_MBOX
# All CPU programs poll the TX mailbox at the bottom of the loop
# (`lw` + taken `blt` back to the top) so steady-state streaming pays no `j`.
CPU_UART_BURST = """
        addi s1, x0, 1
        sw   s1, -16(x0)        # line idle high
        j    poll
send:   srli t1, a0, 1          # precompute d1..d7 into bit 0 of t1..t6,a1
        srli t2, t1, 1          # (chained 1-bit shifts: cheapest on both cores)
        srli t3, t2, 1
        srli t4, t3, 1
        srli t5, t4, 1
        srli t6, t5, 1
        srli a1, t6, 1
        sw   x0, -16(x0)        # start bit  } ten identical stores:
        sw   a0, -16(x0)        # d0         } uniform bit period = one sw
        sw   t1, -16(x0)        # d1
        sw   t2, -16(x0)
        sw   t3, -16(x0)
        sw   t4, -16(x0)
        sw   t5, -16(x0)
        sw   t6, -16(x0)
        sw   a1, -16(x0)        # d7
        sw   s1, -16(x0)        # stop bit (stretched by the next poll/precompute)
poll:   lw   a0, -8(x0)         # TX mailbox {valid, 23'b0, data}; read clears valid
        blt  a0, x0, send       # bit31 set -> byte available
        j    poll
"""

CPU_UART_PAIRS = """
        addi t0, x0, 1
        sw   t0, -16(x0)
        j    poll
send:   slli a0, a0, 1          # start bit = 0 in bit 0, valid bit shifted out
        ori  a0, a0, 0x200      # stop bit
""" + "        sw   a0, -16(x0)\n        srli a0, a0, 1\n" * 9 + """
        sw   a0, -16(x0)        # stop bit
poll:   lw   a0, -8(x0)
        blt  a0, x0, send
        j    poll
"""

# CPU SPI pins: SCK = gpio_out[0], MOSI = gpio_out[7], MISO = gpio_in[0].
# a0[7:0] holds the TX byte; RX bits shift in at a0[0] while TX bits move up,
# so after k bits a0[7] is still the next TX bit (all one-stage ALU ops).
SPI_BIT = """
        andi t1, a0, 0x80       # MOSI (pin 7) = a0[7], SCK (pin 0) = 0
        sw   t1, -16(x0)        # SCK falling edge + MOSI change
        ori  t1, t1, 1
        sw   t1, -16(x0)        # SCK rising edge
        lw   t2, -12(x0)        # GPIO_IN
        andi t2, t2, 1          # MISO
        add  a0, a0, a0         # shift left (cheaper than slli on both cores)
        or   a0, a0, t2         # MISO into bit 0
"""
CPU_SPI_PINS = dict(sck=0, mosi=7)
PIO_SPI_PINS = dict(sck=1, mosi=0)
CPU_SPI_PINS_D = dict(sck_pin=0, mosi_pin=7)
PIO_SPI_PINS_D = dict(sck_pin=1, mosi_pin=0)

CPU_SPI_LOOP2 = """
        sw   x0, -16(x0)        # SCK low, MOSI low
        j    poll
byte:   addi t3, x0, 4          # 4 iterations x 2 bits
loop:
""" + SPI_BIT * 2 + """
        addi t3, t3, -1
        bne  t3, x0, loop
        sw   x0, -16(x0)        # final falling edge, SCK idles low
        sw   a0, -4(x0)         # RX byte (bits 7:0) to host mailbox
poll:   lw   a0, -8(x0)         # bit 31 = valid, a0[7:0] = TX byte
        blt  a0, x0, byte
        j    poll
"""

CPU_SPI_UNROLLED = """
        sw   x0, -16(x0)
        j    poll
byte:
""" + SPI_BIT * 8 + """
        sw   x0, -16(x0)
        sw   a0, -4(x0)
poll:   lw   a0, -8(x0)
        blt  a0, x0, byte
        j    poll
"""

CPU_PROGRAMS = {
    'uart_burst': dict(src=CPU_UART_BURST, words=32),
    'uart_pairs': dict(src=CPU_UART_PAIRS, words=32),
    'spi_loop2': dict(src=CPU_SPI_LOOP2, words=32),
    'spi_unrolled': dict(src=CPU_SPI_UNROLLED, words=72),  # needs > 32 words
}


PRIMARY = {'pio': ['uart_1cpb', 'spi_2cpb'], 'cpu': ['uart_burst', 'spi_loop2']}

# ---------------------------------------------------------------- simulation
def run_sim(name, case, prog_words, word_bits, plusargs, tx_bytes, defines=(), prog_mem_words=None,
            gate=False):
    d = DESIGNS[name]
    wd = WORK / (f'{name}_{case}' + ('_gate' if gate else ''))
    wd.mkdir(parents=True, exist_ok=True)
    fmt = '{:04x}' if word_bits == 16 else '{:08x}'
    (wd / 'prog.hex').write_text('\n'.join(fmt.format(w) for w in prog_words) + '\n')
    (wd / 'tx.hex').write_text('\n'.join(f'{b:02x}' for b in tx_bytes) + '\n')
    (wd / 'slave_tx.hex').write_text('\n'.join(f'{b:02x}' for b in SPI_SLAVE + [0] * 8) + '\n')
    defs = list(d['tb_defines']) + list(defines)
    if prog_mem_words:
        defs.append(f'PROG_WORDS={prog_mem_words}')
    cmd = [str(IVERILOG), '-o', str(wd / 'sim.vvp'), '-s', 'tb',
           '-I', str(INC / 'common'), '-I', str(d['tb'].parent)]
    srcs = d['sources']
    if gate:
        # flattened generic netlist from synth() + Yosys' cell models
        srcs = [WORK / f'{name}_synth/netlist.v', SIMCELLS]
        defs = [x for x in defs if x != 'BENCH'] + ['GATE']
        if d.get('gate_init'):
            (wd / 'gate_init.v').write_text(d['gate_init'])
            srcs = srcs + [wd / 'gate_init.v']
            cmd += ['-s', 'gate_init']
    cmd += [f'-D{x}' for x in defs]
    cmd += [str(p) for p in srcs] + [str(INC / 'common/spi_slave_model.v'), str(d['tb'])]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f'iverilog failed for {name}/{case}:\n{r.stdout}{r.stderr}')
    args = [f'+plen={len(prog_words)}', f'+nbytes={len(tx_bytes)}'] + plusargs
    r = subprocess.run([str(VVP), '-n', 'sim.vvp'] + args, cwd=wd, capture_output=True, text=True,
                       timeout=600)
    (wd / 'sim.log').write_text(r.stdout + r.stderr)
    (wd / 'sim.vvp').unlink(missing_ok=True)          # large; rebuilt by the rerun command
    extra_s = ['-s', 'gate_init'] if (gate and d.get('gate_init')) else []
    cmdline = ' '.join([rel(IVERILOG), '-o', rel(wd / 'sim.vvp'), '-s', 'tb'] + extra_s
                       + ['-I', 'incumbents/common', '-I', rel(d['tb'].parent)]
                       + [f'-D{x}' for x in defs] + [rel(p) for p in srcs]
                       + ['incumbents/common/spi_slave_model.v', rel(d['tb'])])
    cmdline += f' && (cd {rel(wd)} && ../../../{rel(VVP)} -n sim.vvp ' + ' '.join(args) + ')'
    return r.stdout.splitlines(), wd, cmdline


def parse(lines):
    ev = {'P': [], 'R': [], 'S': [], 'E': None, 'END': None, 'TIMEOUT': None}
    for ln in lines:
        f = ln.split()
        if not f:
            continue
        if f[0] == 'P':
            ev['P'].append((int(f[1]), int(f[2], 16) if re.fullmatch(r'[0-9a-fA-F]+', f[2]) else 0))
        elif f[0] in ('R', 'S'):
            ev[f[0]].append((int(f[1]), int(f[2], 16) if re.fullmatch(r'[0-9a-fA-F]+', f[2]) else -1))
        elif f[0] in ('E', 'END', 'TIMEOUT'):
            ev[f[0]] = int(f[1])
    return ev


def level_fn(trans):
    def level(t):
        v = None
        for tt, vv in trans:
            if tt <= t:
                v = vv
            else:
                break
        return v
    return level


def decode_uart(ev, expected, pin=0):
    tx = []
    for t, v in ev['P']:
        b = (v >> pin) & 1
        if not tx or tx[-1][1] != b:
            tx.append((t, b))
    # wait for the line to go idle-high first
    k = next((i for i, (_, b) in enumerate(tx) if b == 1), None)
    res = {'verified': False}
    if k is None:
        res['error'] = 'line never idles high'
        return res
    tx = tx[k:]
    iv = [tx[i + 1][0] - tx[i][0] for i in range(len(tx) - 1)]
    if not iv:
        res['error'] = 'no activity'
        return res
    P = min(iv)
    level = level_fn(tx)
    frames, errors = [], []
    i = 1
    search_from = tx[0][0]
    falls = [t for t, b in tx if b == 0]
    times = [t for t, _ in tx]
    for s in falls:
        if s < search_from:
            continue
        # transitions inside the frame must be on bit boundaries
        for t in times:
            if s < t < s + 10 * P and (t - s) % P:
                errors.append(f'frame@{s}: transition at +{t - s} not a multiple of P={P}')
        bits = [level(s + j * P + P // 2) for j in range(10)]
        stop_ok = all(level(t) == 1 for t in range(s + 9 * P, s + 10 * P))
        if bits[0] != 0 or not stop_ok:
            errors.append(f'frame@{s}: bad start/stop {bits}')
        byte = sum(bits[1 + j] << j for j in range(8))
        frames.append((s, byte))
        search_from = s + 10 * P
    got = [b for _, b in frames]
    starts = [s for s, _ in frames]
    deltas = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
    steady = deltas[1:] if len(deltas) > 2 else deltas
    res.update(clocks_per_bit=P, bytes_sent=[f'0x{b:02X}' for b in expected],
               bytes_decoded=[f'0x{b:02X}' for b in got], frame_start_deltas=deltas,
               clocks_per_byte_sustained=max(steady) if steady else None,
               errors=errors[:10])
    res['verified'] = (got == expected and not errors and ev['TIMEOUT'] is None)
    return res


def decode_spi(ev, expected_mosi, expected_miso, sck_pin, mosi_pin, rx_mask=0xFF):
    sck_tr, mosi_tr = [], []
    for t, v in ev['P']:
        s, m = (v >> sck_pin) & 1, (v >> mosi_pin) & 1
        if not sck_tr or sck_tr[-1][1] != s:
            sck_tr.append((t, s))
        if not mosi_tr or mosi_tr[-1][1] != m:
            mosi_tr.append((t, m))
    rises = [t for i, (t, s) in enumerate(sck_tr) if s == 1 and i > 0 and sck_tr[i - 1][1] == 0]
    falls = [t for i, (t, s) in enumerate(sck_tr) if s == 0 and i > 0 and sck_tr[i - 1][1] == 1]
    mosi_change = {t for t, _ in mosi_tr[1:]}
    errors = []
    for r in rises:
        if r in mosi_change:
            errors.append(f'MOSI changes together with SCK rising edge at {r}')
    nbytes = len(rises) // 8
    intra, per_byte, high = [], [], []
    for k in range(nbytes):
        rr = rises[8 * k: 8 * k + 8]
        intra += [rr[i + 1] - rr[i] for i in range(7)]
        if k + 1 < nbytes:
            per_byte.append(rises[8 * (k + 1)] - rises[8 * k])
    for r in rises:
        f = next((x for x in falls if x > r), None)
        if f is not None:
            high.append(f - r)
    slave_got = [b for _, b in ev['S']]
    host_got = [b & rx_mask for _, b in ev['R']]
    steady = per_byte[1:] if len(per_byte) > 2 else per_byte
    res = dict(clocks_per_bit=(round(statistics.mean(intra), 3) if intra else None),
               clocks_per_bit_min=min(intra) if intra else None,
               clocks_per_bit_max=max(intra) if intra else None,
               sck_high_min=min(high) if high else None,
               clocks_per_byte_sustained=max(steady) if steady else None,
               byte_deltas=per_byte,
               mosi_sent=[f'0x{b:02X}' for b in expected_mosi],
               mosi_at_slave=[f'0x{b:02X}' for b in slave_got],
               miso_sent=[f'0x{b:02X}' for b in expected_miso[:len(expected_mosi)]],
               miso_at_host=[f'0x{b:02X}' for b in host_got], errors=errors[:10])
    res['verified'] = (slave_got == expected_mosi and host_got == expected_miso[:len(expected_mosi)]
                       and not errors and ev['TIMEOUT'] is None)
    return res


def sim_pio(name, case, gate=False):
    p = PIO_PROGRAMS[case]
    spi = case.startswith('spi')
    data = SPI_TX if spi else UART_BYTES
    plus = [f'+grps={p["grps"]:08x}', f'+pend={p["pend"]:08x}', f'+shift={p["shift"]:08x}',
            '+div=00000100', f'+imm={p["imm"]:04x}', f'+txshift={p["txshift"]}',
            f'+spi={int(spi)}', '+maxcyc=20000', '+tail=100',
            f'+sck={PIO_SPI_PINS["sck"]}', f'+mosi={PIO_SPI_PINS["mosi"]}']
    lines, wd, cmd = run_sim(name, case, p['prog'], 16, plus, data, gate=gate)
    ev = parse(lines)
    m = decode_spi(ev, SPI_TX, SPI_SLAVE, **PIO_SPI_PINS_D) if spi else decode_uart(ev, UART_BYTES)
    lst = INC / 'programs' / f'pio_{case}.lst'
    lst.parent.mkdir(exist_ok=True)
    lst.write_text(f'# {case}: PIO, {len(p["prog"])} words; GRPS=0x{p["grps"]:08X} PEND=0x{p["pend"]:08X} '
                   f'SHIFT=0x{p["shift"]:08X} DIV=0x00000100 IMM=0x{p["imm"]:04X}\n'
                   + '\n'.join(f'{i:02d}: {w:04x}  {a}' for i, (w, a) in enumerate(zip(p['prog'], p['asm'])))
                   + '\n')
    m.update(program_words=len(p['prog']), program_bits=16 * len(p['prog']), listing=rel(lst),
             program=[f'0x{w:04X}  {a}' for w, a in zip(p['prog'], p['asm'])],
             config={'GRPS': f'0x{p["grps"]:08X}', 'PEND': f'0x{p["pend"]:08X}',
                     'SHIFT': f'0x{p["shift"]:08X}', 'DIV': '0x00000100 (clkdiv 1.0)'},
             log=rel(wd / 'sim.log'), rerun=cmd)
    return m


def sim_cpu(name, case, gate=False):
    p = CPU_PROGRAMS[case]
    words, listing, _ = assemble(p['src'])
    spi = case.startswith('spi')
    data = SPI_TX if spi else UART_BYTES
    plus = [f'+spi={int(spi)}', '+maxcyc=400000', '+tail=3000',
            f'+sck={CPU_SPI_PINS["sck"]}', f'+mosi={CPU_SPI_PINS["mosi"]}']
    lines, wd, cmd = run_sim(name, case, words, 32, plus, data,
                             prog_mem_words=p['words'] if p['words'] != 32 else None, gate=gate)
    ev = parse(lines)
    m = decode_spi(ev, SPI_TX, SPI_SLAVE, **CPU_SPI_PINS_D) if spi else decode_uart(ev, UART_BYTES)
    lst = INC / 'programs' / f'rv32i_{case}.lst'
    lst.parent.mkdir(exist_ok=True)
    lst.write_text(f'# {case}: RV32I, {len(words)} words, assembled by incumbents/common/rvasm.py\n'
                   + '\n'.join(f'{a:04x}: {w:08x}  {t}' for a, w, t in listing) + '\n')
    m.update(program_words=len(words), program_bits=32 * len(words), prog_mem_words=p['words'],
             listing=rel(lst), log=rel(wd / 'sim.log'), rerun=cmd)
    return m


# ---------------------------------------------------------------- synthesis
def _stat_counts(text):
    """(total cells, flip-flops) from the LAST `stat` block of a yosys log."""
    blk = text[text.rfind('Printing statistics'):]
    if '=== design hierarchy ===' in blk:
        blk = blk[blk.index('=== design hierarchy ==='):]
    m = re.search(r'Number of cells:\s+(\d+)', blk)
    cells = int(m.group(1))
    flops = sum(int(n) for typ, n in re.findall(r'^\s+\$_(\w+)_\s+(\d+)\s*$', blk, re.M)
                if 'DFF' in typ or 'DLATCH' in typ)
    return cells, flops


def synth(name):
    """Generic Yosys synthesis (no liberty), two ways:
    flat : read_verilog -sv; synth -flatten -top T; stat   (what the arena recipe does;
           cross-boundary constant/unused-logic trimming) -> generic_cells
    hier : read_verilog -sv; synth -top T; stat            (the exact command from the
           brief; per-module, no cross-boundary trimming) -> generic_cells_hier
    The flat netlist is written out and re-simulated with the RTL testbench."""
    d = DESIGNS[name]
    srcs = ' '.join(str(p) for p in d['sources'])
    wd = WORK / f'{name}_synth'
    wd.mkdir(parents=True, exist_ok=True)
    out = {}
    for tag, cmd in (('flat', f'synth -flatten -top {d["top"]}; stat; '
                              f'write_verilog -noattr {wd / "netlist.v"}'),
                     ('hier', f'synth -top {d["top"]}; stat')):
        log = wd / f'yosys_{tag}.log'
        r = subprocess.run([str(YOSYS), '-q', '-l', str(log), '-p', f'read_verilog -sv {srcs}; {cmd}'],
                           capture_output=True, text=True, timeout=3600)
        if r.returncode:
            raise RuntimeError(f'yosys failed for {name}:\n{r.stderr[-3000:]}')
        text = log.read_text()
        out[tag] = _stat_counts(text)
        out[tag + '_warnings'] = sorted({w for w in re.findall(r'^Warning: (.*)$', text, re.M)})
    srel = ' '.join(rel(p) for p in d['sources'])
    return dict(generic_cells=out['flat'][0], generic_flops=out['flat'][1],
                generic_cells_hier=out['hier'][0], generic_flops_hier=out['hier'][1],
                yosys_cmd=f'incumbents/yosys.sh -p "read_verilog -sv {srel}; synth -flatten -top {d["top"]}; stat"',
                yosys_cmd_hier=f'incumbents/yosys.sh -p "read_verilog -sv {srel}; synth -top {d["top"]}; stat"',
                yosys_warnings=out['flat_warnings'][:20], netlist=rel(wd / 'netlist.v'),
                yosys_log=rel(wd / 'yosys_flat.log'))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-synth', action='store_true')
    ap.add_argument('--only', nargs='*')
    ap.add_argument('--results-only', action='store_true', help='rebuild results.json from work/measurements.json')
    a = ap.parse_args()
    if a.results_only:
        write_results(json.loads((WORK / 'measurements.json').read_text()))
        return
    names = a.only or list(DESIGNS)
    out = {}
    for name in names:
        d = DESIGNS[name]
        res = {}
        if d['kind'] == 'pio':
            for case in PIO_PROGRAMS:
                res[case] = sim_pio(name, case)
        else:
            for case in CPU_PROGRAMS:
                res[case] = sim_cpu(name, case)
        if not a.no_synth:
            res['synth'] = synth(name)
            gl = {}
            for case in PRIMARY[d['kind']]:
                m = (sim_pio if d['kind'] == 'pio' else sim_cpu)(name, case, gate=True)
                ref = res[case]
                same = (m['verified'] and m['clocks_per_bit'] == ref['clocks_per_bit']
                        and m['clocks_per_byte_sustained'] == ref['clocks_per_byte_sustained'])
                gl[case] = dict(verified=m['verified'], same_timing_as_rtl=same, log=m['log'])
            res['synth']['gate_level_sim'] = gl
        out[name] = res
        for case, m in res.items():
            if case == 'synth':
                print(f'{name:11s} synth: flat {m["generic_cells"]} cells / {m["generic_flops"]} flops; '
                      f'hier {m["generic_cells_hier"]} / {m["generic_flops_hier"]}; gate-level: '
                      + ', '.join(f'{c}={v["verified"]}/{v["same_timing_as_rtl"]}'
                                  for c, v in m['gate_level_sim'].items()))
                for w in m['yosys_warnings'][:6]:
                    print('    warning:', w[:150])
            else:
                print(f'{name:11s} {case:18s} verified={m["verified"]!s:5s} cpb={m.get("clocks_per_bit")} '
                      f'cpB={m.get("clocks_per_byte_sustained")} words={m["program_words"]} '
                      f'{"" if m["verified"] else m.get("errors")}')
    WORK.mkdir(exist_ok=True)
    raw = WORK / 'measurements.json'
    old = json.loads(raw.read_text()) if raw.exists() else {}
    for name, res in out.items():
        old.setdefault(name, {}).update(res)      # keeps an earlier 'synth' with --no-synth
    raw.write_text(json.dumps(old, indent=1) + '\n')
    print(f'wrote {rel(raw)}')
    write_results(old)


UPSTREAM = {
    'fpga_pio': dict(url='https://github.com/lawrie/fpga_pio',
                     commit='f38be97cdfa86d4551c96bb98599e3443000628b', license='BSD-2-Clause',
                     license_file='incumbents/upstream/fpga_pio/LICENSE'),
    'serv': dict(url='https://github.com/olofk/serv',
                 commit='f200eb2ed7b69ac1c6b8eddd47654522aeee5ce8', license='ISC',
                 license_file='incumbents/upstream/serv/LICENSE'),
    'femtorv': dict(url='https://github.com/BrunoLevy/learn-fpga',
                    path='FemtoRV/RTL/PROCESSOR/femtorv32_quark.v',
                    commit='5c08c870315c09ccd9ec64ccde20ab3375b3f273', license='BSD-3-Clause',
                    license_file='incumbents/upstream/femtorv/LICENSE'),
}

PIO_PATCHES = [
    'pio.v [yosys-patch]: gpio_out/gpio_dir/output_pins_prev/pin_directions_prev were driven by two '
    'always blocks; Yosys resolved the conflict to the constant reset value (all GPIO outputs tied 0). '
    'Reset moved into the single driving block.',
    'machine.v [yosys-patch]: same two-always-block problem for output_pins/pin_directions; reset '
    'moved into the pin-update block (reset/restart priority).',
    'pio.v [asic-patch]: one-cycle strobes (push/pull/imm/restart/clkdiv_restart) cleared every cycle '
    'incl. reset (upstream left them at their power-up value through reset); use_divider reset with div.',
    'machine.v [asic-patch]: exec1 cleared on reset/restart (was only an initial value).',
    'Full diff: incumbents/pio_sm1/patched/fpga_pio.patch; decoder/divider/pc/scratch/fifo/isr/osr unmodified.',
]

CPU_IO = ('zero-wait-state I/O, x0-relative: -16 GPIO_OUT[7:0] (W/R), -12 GPIO_IN[7:0] (R), '
          '-8 TX mailbox {valid,23b0,data} (R, read clears valid; 1-deep, host_tx_full), '
          '-4 RX mailbox (W, sets host_rx_valid)')


def _uart(m):
    return dict(case=m['_case'], clocks_per_bit=m['clocks_per_bit'],
                clocks_per_byte_sustained=m['clocks_per_byte_sustained'],
                program_words=m['program_words'], program_bits=m['program_bits'],
                verified=m['verified'], bytes_sent=m['bytes_sent'], bytes_decoded=m['bytes_decoded'])


def _spi(m):
    return dict(case=m['_case'], clocks_per_bit=m['clocks_per_bit'],
                clocks_per_bit_min=m['clocks_per_bit_min'], clocks_per_bit_max=m['clocks_per_bit_max'],
                clocks_per_byte_sustained=m['clocks_per_byte_sustained'],
                program_words=m['program_words'], program_bits=m['program_bits'],
                verified=m['verified'], mosi_at_slave=m['mosi_at_slave'], miso_at_host=m['miso_at_host'])


def _alts(res, cases):
    out = []
    for c in cases:
        if c in res:
            m = res[c]
            out.append(dict(case=c, verified=m['verified'], clocks_per_bit=m['clocks_per_bit'],
                            clocks_per_byte_sustained=m['clocks_per_byte_sustained'],
                            program_words=m['program_words'],
                            **({'prog_mem_words_needed': m['prog_mem_words']} if 'prog_mem_words' in m else {})))
    return out


def write_results(meas):
    entries = []
    for name, res in meas.items():
        d = DESIGNS[name]
        for c, m in res.items():
            if c != 'synth':
                m['_case'] = c
        e = dict(name=name, top=d['top'], sources=[rel(p) for p in d['sources']], defines=[])
        syn = res.get('synth', {})
        if d['kind'] == 'pio':
            full = name == 'pio_full'
            e['upstream'] = UPSTREAM['fpga_pio']
            e['config'] = dict(
                state_machines=4 if full else 1,
                instruction_memory='32 x 16 bit, writable through host action INSTR'
                                   + (' (shared by the 4 SMs)' if full else ''),
                tx_fifo='4 x 32 bit' + (' per SM' if full else ''),
                rx_fifo='4 x 32 bit' + (' per SM' if full else ''),
                clock_divider='upstream 24-bit (16.8 fractional); measured at clkdiv 1.0 (DIV=0x100)',
                gpio='8 pins brought out (upstream has 32; the other 24 are tied off and trimmed by synthesis)',
                host_interface='upstream action bus: action[3:0], index[4:0], din[31:0], dout[31:0]'
                               + (', mindex[1:0]' if full else ''),
                patches=PIO_PATCHES)
            e['uart_tx'] = _uart(res['uart_1cpb'])
            e['uart_tx']['program'] = res['uart_1cpb']['program']
            e['spi_mode0'] = _spi(res['spi_2cpb'])
            e['spi_mode0']['program'] = res['spi_2cpb']['program']
            e['alternatives'] = _alts(res, ['uart_loop_2cpb', 'spi_3cpb', 'spi_2cpb_negative'])
            e['notes'] = (
                'Measured with clkdiv = 1 (no divider), 10 MHz-clock testbench, programs loaded through the '
                'action bus. UART: canonical pull / start / 8x out / stop, unrolled so each bit is one '
                'instruction (1 clk/bit, 10 clk/byte, FIFO pull included). The canonical delay-free loop '
                '(pull side1 [1]; set x,7 side0 [1]; out pins,1; jmp x--) gives 2 clk/bit in 4 words. '
                'SPI: spi_cpha0 structure (out pins side 0 / in pins side 1) at 2 clk/bit; upstream\'s GPIO '
                'output path has one more register than its input path, so `in` sees the pins of two '
                'instructions earlier: the plain 2-clk/bit program reads MISO one bit late '
                '(spi_2cpb_negative, fails); the measured program raises SCK for bit 0 with a nop and '
                'adds one `in` after the last pulse (19 words, 19 clk/byte incl. pull+push). '
                'The textbook variant with [1] delay is 3 clk/bit, 26 clk/byte. Autopull/autopush were not '
                'used: upstream autopush only fires at the next `in`, so the last RX byte would stay in ISR.')
            if full:
                e['notes'] += ' Secondary reference: full 4-SM block; measured on SM0 with the same programs.'
        else:
            core = 'femtorv' if name == 'femtorv_bb' else 'serv'
            e['upstream'] = UPSTREAM[core]
            if core == 'serv':
                w = 4 if name == 'qerv_bb' else 1
                e['config'] = dict(
                    core=f'SERV via upstream serv_rf_top, W={w}' + (' (QERV)' if w == 4 else ' (classic bit-serial)'),
                    isa='RV32I (SERV has no RV32E option)', WITH_CSR=0, COMPRESSED=0, MDU=0,
                    PRE_REGISTER=1, RESET_STRATEGY='MINI', RF_WIDTH=2 * w,
                    register_file=f'serv_rf_ram {1024 // (2 * w)} x {2 * w} bit = 1024 bits (32 x 32, plain Verilog array)',
                    prog_mem='32 x 32 bit = 1024 bits, plain reg array, host-writable (prog_we/prog_addr/prog_wdata), '
                             'combinational read',
                    bus='Harvard: ibus -> program memory, dbus -> I/O only (no data RAM); ack = cyc on both '
                        '(zero wait states)',
                    io=CPU_IO, patches=[])
            else:
                e['config'] = dict(
                    core='FemtoRV32 Quark', isa='RV32I (Quark has no RV32E option)', ADDR_WIDTH=8,
                    NRV_IS_IO_ADDR='0 (zero-wait I/O: 3-cycle stores)', NRV_COUNTER_WIDTH=1,
                    register_file='31 x 32 bit (x0 storage removed by the x0 patch)',
                    prog_mem='32 x 32 bit = 1024 bits, plain reg array, host-writable, combinational read; '
                             'shared instruction/data port as Quark requires (I/O when addr[7]=1)',
                    io=CPU_IO, sim_only_defines=['BENCH'],
                    patches=['[icarus-patch] declarations moved ahead of first use (Icarus 13 rejects '
                             'use-before-declaration); logic unchanged',
                             '[asic-patch] x0 reads forced to 0 and x0 storage dropped (registerFile [31:1]): '
                             'upstream never writes x0 and relies on FPGA power-up init; in the generic netlist '
                             'x0 was a never-written 32-bit register (random at power-up)',
                             'Full diff: incumbents/femtorv_bb/patched/femtorv32_quark.patch'])
            e['uart_tx'] = _uart(res['uart_burst'])
            e['spi_mode0'] = _spi(res['spi_loop2'])
            e['alternatives'] = _alts(res, ['uart_pairs', 'spi_unrolled'])
            e['notes'] = (
                'Firmware hand-assembled (incumbents/common/rvasm.py), loaded through the host port, '
                'listings in incumbents/programs/rv32i_<case>.lst. UART: poll mailbox, precompute '
                'd1..d7 with seven 1-bit srli, then ten back-to-back identical sw (start, d0..d7, stop) so '
                'the bit period is exactly one store; stop bit is stretched by the next poll/precompute '
                '(clocks_per_byte includes it). Alternative sw/srli pairs: see alternatives. SPI: 8 '
                'instructions/bit (andi MOSI, sw SCK low, ori, sw SCK high, lw GPIO_IN, andi, add, or), '
                'loop unrolled 2x with a counter to fit the 32-word memory; the fully unrolled version '
                'needs 71 words (see alternatives, simulated with PROG_WORDS=72). Pins: UART TX=gpio_out[0]; '
                'SPI SCK=gpio_out[0], MOSI=gpio_out[7], MISO=gpio_in[0]. Other GPIO_OUT bits are '
                'don\'t-care during UART TX (whole-register writes).')
        if syn:
            e['generic_cells'] = syn['generic_cells']
            e['generic_flops'] = syn['generic_flops']
            e['generic_cells_hier'] = syn['generic_cells_hier']
            e['gate_level_sim'] = {c: v['verified'] and v['same_timing_as_rtl']
                                   for c, v in syn.get('gate_level_sim', {}).items()}
            e['yosys_cmd'] = syn['yosys_cmd']
        entries.append(e)
    order = ['pio_sm1', 'pio_full', 'serv_bb', 'qerv_bb', 'femtorv_bb']
    entries.sort(key=lambda e: order.index(e['name']))
    (INC / 'results.json').write_text(json.dumps(entries, indent=1) + '\n')
    print(f'wrote {rel(INC / "results.json")}')


if __name__ == '__main__':
    main()
