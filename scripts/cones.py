#!/usr/bin/env python3
"""Attribute each combinational cell's area to the registers/ports it feeds
(split equally among them). Rough guide to where logic area goes."""
import json, re, sys
from collections import defaultdict
sys.path.insert(0, 'scripts')
import arena

def main(name, top=30):
    L = arena.Library()
    net = json.load(open(f'results/arena/{name}/netlist.json'))
    m = next(v for v in net['modules'].values() if v.get('attributes', {}).get('top'))
    cells = m['cells']
    bitname = {}
    for n, w in m['netnames'].items():
        for i, b in enumerate(w['bits']):
            if isinstance(b, int) and (b not in bitname or not n.startswith('$')):
                bitname[b] = n
    sinks = defaultdict(list)
    for cn, c in cells.items():
        for p, bits in c['connections'].items():
            if L.cells[c['type']]['pins'][p]['dir'] != 'output':
                for b in bits:
                    if isinstance(b, int):
                        sinks[b].append(cn)
    seq = {cn for cn, c in cells.items() if L.cells[c['type']]['kind'] != 'comb'}
    outs = {b: 'port:' + pn for pn, pw in m['ports'].items() if pw['direction'] == 'output' for b in pw['bits']}

    def label(cn):
        c = cells[cn]
        q = [b for p, bs in c['connections'].items() if L.cells[c['type']]['pins'][p]['dir'] == 'output' for b in bs]
        return re.sub(r'\[\d+\]$', '', bitname.get(q[0], cn)).replace('\\', '') if q else cn
    memo = {}
    sys.setrecursionlimit(100000)

    def fan(cn):
        if cn in memo:
            return memo[cn]
        memo[cn] = set()
        res = set()
        c = cells[cn]
        for p, bs in c['connections'].items():
            if L.cells[c['type']]['pins'][p]['dir'] != 'output':
                continue
            for b in bs:
                if b in outs:
                    res.add(outs[b])
                for s in sinks.get(b, []):
                    res |= {label(s)} if s in seq else fan(s)
        memo[cn] = res
        return res
    cost = defaultdict(float)
    for cn, c in cells.items():
        a = L.cells[c['type']]['area']
        if cn in seq:
            cost['SEQ:' + L.cells[c['type']]['kind']] += a
            continue
        f = fan(cn) or {'(none)'}
        for e in f:
            cost[e] += a / len(f)
    for k, v in sorted(cost.items(), key=lambda kv: -kv[1])[:top]:
        print(f'{k:40s}{v:9.1f}')

if __name__ == '__main__':
    main(sys.argv[1])
