#!/usr/bin/env python3
"""Same-flow area and pre-layout timing for any design ("arena").

Every design (ours and the incumbents) goes through the identical recipe:
Yosys synth -flatten, dfflibmap and ABC onto the CMOS5L typical library, then
a small NLDM static timing analysis of the mapped netlist written here.

The STA is a pre-layout estimate, NOT signoff: ideal zero-skew clock, typical
corner, wire capacitance from the library's "2k" wire-load table, no CTS, no
routing parasitics. It is applied identically to every design so ratios are
meaningful; absolute numbers still need the official LibreLane flow.

    python3 scripts/arena.py measure --name A --top tt_um_chipwheel src/project.v
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / '.tools/pdk-lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib'
# Slow corner from the same pinned IHP-Open-PDK commit (2bbec755), timing only.
LIB_SLOW = ROOT / '.tools/pdk-lib/sg13cmos5l_stdcell_slow_1p08V_125C.lib'
YOSYS = ROOT / '.tools/flow-venv/bin/yowasp-yosys'
OUT = ROOT / 'results/arena'
CLOCK_SLEW = 0.10      # ns, ideal clock transition at every clock pin
INPUT_SLEW = 0.10      # ns, primary-input transition
WIRE_MODEL = '2k'      # library wire_load group used for net capacitance
MAX_FANOUT = 8         # library default_max_fanout; larger nets get a modelled buffer tree
REPAIR_BUF = 'sg13cmos5l_buf_4'


# ---------------------------------------------------------------- liberty
def _tokens(text):
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S).replace('\\\n', ' ')
    return re.findall(r'"[^"]*"|[{}();:,]|[^\s{}();:,"]+', text)


def parse_liberty(path):
    """Return the library as nested dicts: {'attrs':{}, 'groups':[(type,args,dict)]}."""
    toks = _tokens(Path(path).read_text())
    pos = 0

    def group():
        nonlocal pos
        node = {'attrs': {}, 'groups': []}
        while pos < len(toks):
            t = toks[pos]
            if t == '}':
                pos += 1
                return node
            name = t
            pos += 1
            if toks[pos] == ':':
                pos += 1
                val = []
                while toks[pos] != ';':
                    val.append(toks[pos])
                    pos += 1
                pos += 1
                node['attrs'][name] = ' '.join(val).strip('"')
            elif toks[pos] == '(':
                pos += 1
                args = []
                while toks[pos] != ')':
                    if toks[pos] != ',':
                        args.append(toks[pos].strip('"'))
                    pos += 1
                pos += 1
                if toks[pos] == '{':
                    pos += 1
                    node['groups'].append((name, args, group()))
                else:
                    if toks[pos] == ';':
                        pos += 1
                    node['attrs'].setdefault(name, []).append(args) if isinstance(
                        node['attrs'].get(name, []), list) else None
            else:
                pos += 1
        return node

    top = group()
    return top['groups'][0][2]


def _floats(s):
    return [float(x) for x in re.split(r'[,\s]+', s.strip()) if x]


class Table:
    def __init__(self, g, templates):
        tmpl = templates.get(g['_args'][0] if g['_args'] else '', {})
        a = g['attrs']
        idx1 = a.get('index_1') or tmpl.get('index_1')
        idx2 = a.get('index_2') or tmpl.get('index_2')
        self.vars = tmpl.get('vars', [])
        self.i1 = _floats(idx1[0][0]) if idx1 else [0.0]
        self.i2 = _floats(idx2[0][0]) if idx2 else None
        rows = [_floats(r) for r in a['values'][0]]
        self.v = rows

    @staticmethod
    def _seg(axis, x):
        if len(axis) == 1:
            return 0, 0, 0.0
        i = 0
        while i < len(axis) - 2 and x > axis[i + 1]:
            i += 1
        x0, x1 = axis[i], axis[i + 1]
        return i, i + 1, (x - x0) / (x1 - x0)

    def lookup(self, x, y=None):
        """x is variable_1, y is variable_2 (linear extrapolation outside)."""
        i0, i1, fx = self._seg(self.i1, x)
        if self.i2 is None:
            r = self.v[0] if len(self.v) == 1 else [row[0] for row in self.v]
            return r[i0] + (r[i1] - r[i0]) * fx
        j0, j1, fy = self._seg(self.i2, y)
        v = self.v
        a = v[i0][j0] + (v[i0][j1] - v[i0][j0]) * fy
        b = v[i1][j0] + (v[i1][j1] - v[i1][j0]) * fy
        return a + (b - a) * fx


class Library:
    def __init__(self, path=LIB):
        lib = parse_liberty(path)
        self.templates = {}
        self.cells = {}
        self.wire = None
        for typ, args, g in lib['groups']:
            if typ == 'lu_table_template':
                a = g['attrs']
                self.templates[args[0]] = {
                    'vars': [a.get('variable_1'), a.get('variable_2')],
                    'index_1': a.get('index_1'), 'index_2': a.get('index_2')}
            elif typ == 'wire_load' and args[0] == WIRE_MODEL:
                self.wire = (float(g['attrs']['capacitance']),
                             [tuple(map(float, x)) for x in g['attrs']['fanout_length']])
            elif typ == 'cell':
                self.cells[args[0]] = self._cell(g)

    def _table(self, g):
        g['_args'] = g.get('_args', [])
        return Table(g, self.templates)

    def _cell(self, g):
        cell = {'area': float(g['attrs'].get('area', 0)), 'pins': {}, 'kind': 'comb'}
        for typ, args, sub in g['groups']:
            if typ in ('ff', 'ff_bank'):
                cell['kind'] = 'ff'
            elif typ == 'latch':
                cell['kind'] = 'latch'
            elif typ == 'statetable':
                pass
        if 'clock_gating_integrated_cell' in g['attrs']:
            cell['kind'] = 'icg'
        for typ, args, sub in g['groups']:
            if typ != 'pin':
                continue
            for name in args:
                pa = sub['attrs']
                pin = {'dir': pa.get('direction'), 'cap': float(pa.get('capacitance', 0) or 0),
                       'clock': pa.get('clock') == 'true', 'arcs': []}
                for ttyp, targs, t in sub['groups']:
                    if ttyp != 'timing':
                        continue
                    ta = t['attrs']
                    arc = {'from': ta.get('related_pin'), 'type': ta.get('timing_type', 'combinational'),
                           'sense': ta.get('timing_sense', 'non_unate')}
                    for k, kargs, kg in t['groups']:
                        kg['_args'] = kargs
                        arc[k] = self._table(kg)
                    pin['arcs'].append(arc)
                cell['pins'][name] = pin
        return cell

    def wire_cap(self, fanout):
        if not self.wire or fanout <= 0:
            return 0.0
        cap, pts = self.wire
        pts = sorted(pts)
        if fanout <= pts[0][0]:
            length = pts[0][1] * fanout / pts[0][0]
        else:
            length = pts[-1][1]
            for (f0, l0), (f1, l1) in zip(pts, pts[1:]):
                if fanout <= f1:
                    length = l0 + (l1 - l0) * (fanout - f0) / (f1 - f0)
                    break
            else:
                f0, l0 = pts[-2]
                f1, l1 = pts[-1]
                length = l1 + (l1 - l0) * (fanout - f1) / (f1 - f0)
        return cap * length


# ---------------------------------------------------------------- STA
def sta(netlist, lib, period_ns, input_delay=0.0, output_delay=0.0):
    mods = netlist['modules']
    top = next(n for n, m in mods.items() if m.get('attributes', {}).get('top'))
    m = mods[top]
    cells = m['cells']
    drivers, sinks = {}, {}
    port_in, port_out = {}, {}
    for pname, p in m['ports'].items():
        for i, b in enumerate(p['bits']):
            if isinstance(b, int):
                (port_in if p['direction'] == 'input' else port_out)[b] = f'{pname}[{i}]'
    for cname, c in cells.items():
        lc = lib.cells[c['type']]
        for pin, bits in c['connections'].items():
            for b in bits:
                if not isinstance(b, int):
                    continue
                if lc['pins'][pin]['dir'] == 'output':
                    drivers[b] = (cname, pin)
                else:
                    sinks.setdefault(b, []).append((cname, pin))
    clk_port = {b for b, n in port_in.items() if n.startswith('clk')}
    icg_out = {b for cn, c in cells.items() if lib.cells[c['type']]['kind'] == 'icg'
               for pin, bits in c['connections'].items()
               if lib.cells[c['type']]['pins'][pin]['dir'] == 'output' for b in bits if isinstance(b, int)}
    clk_bits_pre = lambda b: clk_port | icg_out
    # loads; nets above the library max fanout get a modelled buffer tree, as the
    # real flow's repair_design would insert (driver sees the tree root only)
    load, tree = {}, {}
    buf = lib.cells[REPAIR_BUF]
    buf_cap = buf['pins']['A']['cap']
    buf_arc = buf['pins']['X']['arcs'][0]
    for b in set(drivers) | set(port_in):
        ss = sinks.get(b, [])
        c = sum(lib.cells[cells[n]['type']]['pins'][p]['cap'] for n, p in ss)
        fo = len(ss) + (1 if b in port_out else 0)
        load[b] = c + lib.wire_cap(fo) + (0.005 if b in port_out else 0.0)
        if fo > MAX_FANOUT and b not in clk_bits_pre(b):
            leaves = math.ceil(fo / MAX_FANOUT)
            levels, n = [], leaves
            leaf_load = c / leaves + lib.wire_cap(min(fo, MAX_FANOUT))
            while n > 4:
                levels.append(4 * buf_cap + lib.wire_cap(4))
                n = math.ceil(n / 4)
            load[b] = n * buf_cap + lib.wire_cap(n)
            stages = list(reversed(levels)) + [leaf_load]
            d, sl = 0.0, 0.08
            for L in stages:
                d += max(buf_arc['cell_rise'].lookup(sl, L), buf_arc['cell_fall'].lookup(sl, L))
                sl = max(buf_arc['rise_transition'].lookup(sl, L), buf_arc['fall_transition'].lookup(sl, L))
            n_buf = leaves + sum(math.ceil(leaves / 4 ** (i + 1)) for i in range(len(levels)))
            tree[b] = (d, sl, n_buf)
    # clock nets: driven by clk port or ICG outputs; latch enable pins are clocks
    clk_bits = {b for b, n in port_in.items() if n.startswith('clk')}
    for cname, c in cells.items():
        if lib.cells[c['type']]['kind'] == 'icg':
            for pin, bits in c['connections'].items():
                if lib.cells[c['type']]['pins'][pin]['dir'] == 'output':
                    clk_bits.update(b for b in bits if isinstance(b, int))
    # arrival[bit] = (rise_at, rise_slew, fall_at, fall_slew, prev)
    arr = {}
    for b, n in port_in.items():
        if b in clk_bits:
            continue
        arr[b] = [input_delay, INPUT_SLEW, input_delay, INPUT_SLEW, ('in', n)]

    def clock_arrival(cname, clkpin):
        """Arrival of the active clock edge at a sequential clock pin."""
        c = cells[cname]
        b = c['connections'][clkpin][0]
        if b in drivers:
            dn, dp = drivers[b]
            dc = lib.cells[cells[dn]['type']]
            if dc['kind'] == 'icg':
                arc = next(a for a in dc['pins'][dp]['arcs'] if 'cell_rise' in a)
                return arc['cell_rise'].lookup(CLOCK_SLEW, load[b]), arc['rise_transition'].lookup(CLOCK_SLEW, load[b])
        return 0.0, CLOCK_SLEW

    # launch points
    for cname, c in cells.items():
        lc = lib.cells[c['type']]
        if lc['kind'] not in ('ff', 'latch'):
            continue
        for pin, bits in c['connections'].items():
            p = lc['pins'][pin]
            if p['dir'] != 'output':
                continue
            b = bits[0]
            best = None
            for a in p['arcs']:
                if a['type'] not in ('rising_edge', 'falling_edge') or 'cell_rise' not in a:
                    continue
                t0, s0 = clock_arrival(cname, a['from'])
                if a['type'] == 'falling_edge':
                    t0 += period_ns / 2
                r = t0 + a['cell_rise'].lookup(s0, load[b])
                f = t0 + a['cell_fall'].lookup(s0, load[b])
                rs = a['rise_transition'].lookup(s0, load[b])
                fs = a['fall_transition'].lookup(s0, load[b])
                if b in tree:
                    r, f, rs, fs = r + tree[b][0], f + tree[b][0], tree[b][1], tree[b][1]
                cand = [r, rs, f, fs, ('launch', cname, a['from'])]
                if best is None or max(r, f) > max(best[0], best[2]):
                    best = cand
            if best:
                arr[b] = best
    # combinational propagation (topological via memoized DFS)
    comb = {n for n, c in cells.items() if lib.cells[c['type']]['kind'] == 'comb'}
    order, seen = [], set()

    def visit(n):
        stack = [(n, False)]
        while stack:
            x, done = stack.pop()
            if done:
                order.append(x)
                continue
            if x in seen:
                continue
            seen.add(x)
            stack.append((x, True))
            c = cells[x]
            lc = lib.cells[c['type']]
            for pin, bits in c['connections'].items():
                if lc['pins'][pin]['dir'] == 'output':
                    continue
                for b in bits:
                    if isinstance(b, int) and b in drivers and drivers[b][0] in comb and drivers[b][0] not in seen:
                        stack.append((drivers[b][0], False))

    for n in comb:
        visit(n)
    for n in order:
        c = cells[n]
        lc = lib.cells[c['type']]
        for opin, obits in c['connections'].items():
            op = lc['pins'][opin]
            if op['dir'] != 'output':
                continue
            ob = obits[0]
            best = None
            for a in op['arcs']:
                if 'cell_rise' not in a:
                    continue
                ib = c['connections'].get(a['from'], [None])[0]
                if ib not in arr:
                    continue
                ir, irs, if_, ifs, _ = arr[ib]
                L = load[ob]
                sense = a['sense']
                outs = []
                # output rise caused by input rise (positive) or fall (negative)
                srcs_r = [(ir, irs)] if sense == 'positive_unate' else [(if_, ifs)] if sense == 'negative_unate' else [(ir, irs), (if_, ifs)]
                srcs_f = [(if_, ifs)] if sense == 'positive_unate' else [(ir, irs)] if sense == 'negative_unate' else [(ir, irs), (if_, ifs)]
                r = max(t + a['cell_rise'].lookup(s, L) for t, s in srcs_r)
                rs = max(a['rise_transition'].lookup(s, L) for t, s in srcs_r)
                f = max(t + a['cell_fall'].lookup(s, L) for t, s in srcs_f)
                fs = max(a['fall_transition'].lookup(s, L) for t, s in srcs_f)
                if ob in tree:
                    r, f, rs, fs = r + tree[ob][0], f + tree[ob][0], tree[ob][1], tree[ob][1]
                cand = [r, rs, f, fs, ('cell', n, a['from'], ib)]
                if best is None or max(r, f) > max(best[0], best[2]):
                    best = cand
            if best:
                arr[ob] = best
    # endpoints
    ends = []
    for cname, c in cells.items():
        lc = lib.cells[c['type']]
        if lc['kind'] not in ('ff', 'latch', 'icg'):
            continue
        for pin, bits in c['connections'].items():
            p = lc['pins'][pin]
            if p['dir'] != 'input':
                continue
            b = bits[0]
            if not isinstance(b, int) or b not in arr:
                continue
            for a in p['arcs']:
                if not a['type'].startswith('setup'):
                    continue
                r, rs, f, fs, _ = arr[b]
                t_clk, s_clk = clock_arrival(cname, a['from'])
                if a['type'] == 'setup_falling':
                    t_clk += period_ns / 2
                su = 0.0
                if 'rise_constraint' in a:
                    su = max(su, a['rise_constraint'].lookup(rs, s_clk) + r - max(r, f))
                if 'fall_constraint' in a:
                    su = max(su, a['fall_constraint'].lookup(fs, s_clk) + f - max(r, f))
                need = max(r, f) + su - t_clk   # time after the capturing edge's clock arrival
                kind = 'half' if a['type'] == 'setup_falling' else 'full'
                ends.append({'bit': b, 'end': f'{cname}/{pin}', 'cell': c['type'], 'arrival': max(r, f),
                             'setup': su, 'clock': t_clk, 'kind': kind, 'need': need})
    for b, n in port_out.items():
        if b in arr:
            r, rs, f, fs, _ = arr[b]
            ends.append({'bit': b, 'end': n, 'cell': 'port', 'arrival': max(r, f), 'setup': output_delay,
                         'clock': 0.0, 'kind': 'out', 'need': max(r, f) + output_delay})

    def trace(b):
        path, guard = [], 0
        while b in arr and guard < 10000:
            r, rs, f, fs, prev = arr[b]
            path.append({'at': round(max(r, f), 4), 'node': ' '.join(str(x) for x in prev[:3]) if prev[0] != 'cell'
                         else f"{prev[1]}({cells[prev[1]]['type']}).{prev[2]}"})
            if prev[0] != 'cell':
                break
            b = prev[3]
            guard += 1
        return list(reversed(path))

    def launched_by(b):
        p = trace(b)
        return p[0]['node'] if p else '?'

    # Required period. Full-cycle endpoints: arrival+setup <= T + clk_arrival.
    # Half-cycle (latch closing at falling edge): arrival+setup <= T/2 + ...
    reg = [e for e in ends if e['kind'] in ('full', 'half') and not launched_by(e['bit']).startswith('in ')]
    inp = [e for e in ends if e['kind'] in ('full', 'half') and launched_by(e['bit']).startswith('in ')]
    outs = [e for e in ends if e['kind'] == 'out']

    def need_period(e):
        if e['kind'] == 'half':
            return 2 * (e['arrival'] + e['setup'] - (e['clock'] - period_ns / 2))
        return e['need']

    def worst(es):
        if not es:
            return None
        e = max(es, key=need_period)
        return {'endpoint': e['end'], 'cell': e['cell'], 'arrival_ns': round(e['arrival'], 4),
                'setup_ns': round(e['setup'], 4), 'required_period_ns': round(need_period(e), 4),
                'path': trace(e['bit'])}

    wr = worst(reg)
    t_min = wr['required_period_ns'] if wr else 0.0
    res = {
        'model': 'pre-layout NLDM, typical 1.20V 25C, ideal clock, wire_load %s' % WIRE_MODEL,
        'period_ns': period_ns,
        'reg2reg': wr, 'in2reg': worst(inp),
        'reg2out': worst(outs),
        'fmax_mhz_reg2reg': round(1000.0 / t_min, 2) if t_min > 0 else None,
        'slack_ns_reg2reg': round(period_ns - t_min, 4) if wr else None,
        'endpoints': len(ends),
        'repair_buffers': sum(t[2] for t in tree.values()),
        'repair_buffer_area_um2': round(sum(t[2] for t in tree.values()) * buf['area'], 2),
    }
    return res


# ---------------------------------------------------------------- synthesis
def synth(name, top, sources, defines=(), out=OUT, extra=''):
    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    dflags = ' '.join(f'-D{x}' for x in defines)
    srcs = ' '.join(str(Path(s).resolve()) for s in sources)
    script = f"""# arena recipe (identical for every design)
read_liberty -lib {LIB}
read_verilog -sv {dflags} {srcs}
hierarchy -check -top {top}
{extra}
synth -flatten -top {top}
dfflibmap -liberty {LIB}
abc -liberty {LIB}
opt_clean -purge
check -assert
tee -o {d}/stat.json stat -json -liberty {LIB}
write_json {d}/netlist.json
write_verilog -noattr {d}/netlist.v
"""
    (d / 'synth.ys').write_text(script)
    t0 = time.time()
    # Mount only the repo: the default YoWASP preopen of every /* entry can hang on autofs.
    env = dict(os.environ, YOWASP_CACHE_DIR=str(ROOT / '.tools/yowasp-cache'), YOWASP_MOUNT=f'{ROOT}={ROOT}')
    with open(d / 'synth.log', 'w') as log:
        rc = subprocess.run([str(YOSYS), '-q', '-s', str(d / 'synth.ys')], cwd=ROOT, stdout=log,
                            stderr=subprocess.STDOUT, env=env, timeout=1800).returncode
    if rc:
        sys.exit(f'yosys failed for {name}; see {d}/synth.log')
    stat = json.loads((d / 'stat.json').read_text())
    des = stat['design']
    return {'cells': des['num_cells'], 'area_um2': des['area'], 'by_type': des['num_cells_by_type'],
            'synth_seconds': round(time.time() - t0, 3)}


def categorize(by_type, lib):
    seq = comb = 0.0
    n_ff = n_latch = n_icg = 0
    for t, n in by_type.items():
        c = lib.cells.get(t)
        if not c:
            continue
        if c['kind'] == 'ff':
            n_ff += n
            seq += n * c['area']
        elif c['kind'] == 'latch':
            n_latch += n
            seq += n * c['area']
        elif c['kind'] == 'icg':
            n_icg += n
            seq += n * c['area']
        else:
            comb += n * c['area']
    return {'flops': n_ff, 'latches': n_latch, 'icgs': n_icg,
            'sequential_area_um2': round(seq, 4), 'combinational_area_um2': round(comb, 4)}


def measure(name, top, sources, defines=(), period=20.0, out=OUT, extra=''):
    s = synth(name, top, sources, defines, out, extra)
    lib = Library()
    net = json.loads((out / name / 'netlist.json').read_text())
    t = sta(net, lib, period)
    res = {'name': name, 'top': top, 'sources': [str(Path(x)) for x in sources], 'defines': list(defines),
           'library': LIB.name, **s, **categorize(s['by_type'], lib), 'timing': t}
    if LIB_SLOW.exists():
        res['timing_slow'] = sta(net, Library(LIB_SLOW), period)
        res['timing_slow']['model'] = res['timing_slow']['model'].replace('typical 1.20V 25C', 'slow 1.08V 125C')
    (out / name / 'arena.json').write_text(json.dumps(res, indent=1))
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('measure')
    m.add_argument('--name', required=True)
    m.add_argument('--top', required=True)
    m.add_argument('-D', '--define', action='append', default=[])
    m.add_argument('--period-ns', type=float, default=20.0)
    m.add_argument('sources', nargs='+')
    rs = sub.add_parser('restat', help='re-run typ+slow STA for existing arena results')
    rs.add_argument('names', nargs='+')
    s = sub.add_parser('sta', help='re-run STA on an existing netlist.json')
    s.add_argument('netlist')
    s.add_argument('--period-ns', type=float, default=20.0)
    a = ap.parse_args()
    if a.cmd == 'measure':
        r = measure(a.name, a.top, a.sources, a.define, a.period_ns)
        t = r['timing']
        print(f"{r['name']}: {r['cells']} cells, {r['area_um2']:.1f} um2 "
              f"(seq {r['sequential_area_um2']:.0f}: {r['flops']} ff, {r['latches']} latch, {r['icgs']} icg), "
              f"reg2reg fmax {t['fmax_mhz_reg2reg']} MHz")
        if t['reg2reg']:
            print('  worst reg2reg ->', t['reg2reg']['endpoint'], t['reg2reg']['required_period_ns'], 'ns')
    elif a.cmd == 'restat':
        typ, slow = Library(), Library(LIB_SLOW)
        for n in a.names:
            p = OUT / n / 'arena.json'
            r = json.loads(p.read_text())
            net = json.loads((OUT / n / 'netlist.json').read_text())
            per = r['timing']['period_ns']
            r['timing'] = sta(net, typ, per)
            r['timing_slow'] = sta(net, slow, per)
            r['timing_slow']['model'] = r['timing_slow']['model'].replace('typical 1.20V 25C', 'slow 1.08V 125C')
            p.write_text(json.dumps(r, indent=1))
            print(f"{n}: typ {r['timing']['fmax_mhz_reg2reg']} MHz, slow {r['timing_slow']['fmax_mhz_reg2reg']} MHz")
    else:
        r = sta(json.loads(Path(a.netlist).read_text()), Library(), a.period_ns)
        print(json.dumps(r, indent=1))


if __name__ == '__main__':
    main()
