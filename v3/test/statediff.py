"""Find the first cycle where RTL internal state diverges from the model (fuzz seed)."""
import sys, subprocess, tempfile
from pathlib import Path
sys.path.insert(0, '../model'); sys.path.insert(0, '.')
import fuzz3, replay
from cw3 import Chip

def model_rows(b):
    c = Chip()
    rows = []
    for s in b.stim:
        c.edge((s >> 8) & 255, s & 255, (s >> 16) & 1)
        a, d = c.sms
        rows.append((a.pc, a.en, a.dly, a.divc, a.x, a.y, a.isr, a.osr, a.isr_cnt, a.osr_cnt, a.val, a.dir, a.txv, a.rxv,
                     d.pc, d.en, d.x, d.y, d.isr, d.osr, d.isr_cnt, d.osr_cnt, d.val, d.dir, d.dly, d.divc))
    return rows

NAMES = 'pc0 en0 dly0 divc0 x0 y0 isr0 osr0 icnt0 ocnt0 val0 dir0 txv0 rxv0 pc1 en1 x1 y1 isr1 osr1 icnt1 ocnt1 val1 dir1 dly1 divc1'.split()
HEX = {4, 5, 6, 7, 10, 11, 16, 17, 18, 19, 22, 23}

def main(seed, cycles=3000):
    b, _ = fuzz3.run_case(seed, cycles)
    vvp = replay.build(defines=('STATE_TRACE',), tag='trace')
    cyc, err, log = replay.replay(b, vvp)
    rtl = {}
    for line in log.splitlines():
        if line.startswith('ST '):
            f = line.split()[1:]
            rtl[int(f[0])] = tuple(int(v, 16) if i in HEX else int(v) for i, v in enumerate(f[1:]))
    for i, m in enumerate(model_rows(b)):
        r = rtl.get(i)
        if r is None:
            break
        if r != m:
            diffs = [f'{NAMES[k]}: rtl={r[k]:#x} model={m[k]:#x}' for k in range(len(m)) if r[k] != m[k]]
            print(f'first state divergence at cycle {i}:', '; '.join(diffs))
            return i
    print('no state divergence in', len(rtl), 'cycles')

if __name__ == '__main__':
    main(int(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 3000)
