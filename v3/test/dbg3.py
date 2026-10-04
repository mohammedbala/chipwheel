"""Debug helper: replay a fuzz seed in the model and print SM state near a cycle."""
import sys
sys.path.insert(0, '../model'); sys.path.insert(0, '.')
import fuzz3
from cw3 import Chip

def trace(seed, lo, hi, sms=(0, 1), cycles=3000):
    b, _ = fuzz3.run_case(seed, cycles)
    c = Chip()
    for i, (s, e) in enumerate(zip(b.stim, b.expect)):
        if lo <= i <= hi:
            for n in sms:
                st = c.sms[n]
                try:
                    w = hex(c.fetch(st))
                except Exception:
                    w = None
                print(i, f'SM{n} pc={st.pc} en={st.en} ins={w} x={st.x:#x} y={st.y:#x} isr={st.isr:#x}/{st.isr_cnt} '
                         f'osr={st.osr:#x}/{st.osr_cnt} dly={st.dly} divc={st.divc} txv={st.txv} rxv={st.rxv} '
                         f'val={st.val:#x} dir={st.dir:#x}', 'ui', hex((s >> 8) & 255), 'pins', hex(s & 255),
                      'irq', c.irq, 'rd', c.rd_nib)
        c.edge((s >> 8) & 255, s & 255, (s >> 16) & 1)
    return c

if __name__ == '__main__':
    c = trace(int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]))
    print('cfg0', [hex(v) for v in c.cfg[0]]); print('cfg1', [hex(v) for v in c.cfg[1]]); print('glob', [hex(v) for v in c.glob])
