"""Chipwheel v3 cycle-accurate reference model (see v3/docs/spec.md).

`Chip.edge(ui_in, uio_in, rst_n, uio_late)` advances one rising clock edge.
Inputs are those present at the edge (the bench changes them just after the
falling edge). `uio_late` is the uio value at the falling edge that follows
(default `uio_in`; DDR sampling). Returns the outputs after the edge:
(uo_out, uio_out, uio_oe, uio_out_care_mask).
"""

M16 = 0xFFFF


def bitrev16(v):
    return int(f'{v & M16:016b}'[::-1], 2)


class Cfg:
    """Decoded per-SM configuration (8 x 16-bit registers)."""

    def __init__(self, regs):
        r = [x if x is not None else 0 for x in regs]
        self.defined = all(x is not None for x in regs)
        self.div = r[0]
        self.clksrc = r[1] & 3
        self.resync = (r[1] >> 2) & 1
        self.clk_pin = (r[1] >> 3) & 7
        self.in_fast = (r[1] >> 6) & 1
        self.in_ddr = (r[1] >> 7) & 1
        self.out_base = r[2] & 7
        self.out_count = min((r[2] >> 3) & 15, 8)
        self.set_base = (r[2] >> 7) & 7
        self.set_count = min((r[2] >> 10) & 7, 5)
        self.side_base = (r[2] >> 13) & 7
        self.in_base = r[3] & 7
        self.jmp_pin = (r[3] >> 3) & 7
        self.side_count = (r[3] >> 6) & 3
        self.side_en = (r[3] >> 8) & 1
        self.side_pindir = (r[3] >> 9) & 1
        self.jflag = (r[3] >> 10) & 3
        self.status_sel = (r[3] >> 12) & 1
        self.wrap_bottom = r[4] & 31
        self.wrap_top = (r[4] >> 5) & 31
        self.in_left = (r[4] >> 10) & 1
        self.out_left = (r[4] >> 11) & 1
        self.autopush = (r[4] >> 12) & 1
        self.autopull = (r[4] >> 13) & 1
        self.fifo16 = (r[4] >> 14) & 1
        self.diff = (r[4] >> 15) & 1
        self.push_thresh = (r[5] & 15) or 16
        self.pull_thresh = ((r[5] >> 4) & 15) or 16
        self.stuff_n = (r[5] >> 8) & 7
        self.stuff_ones = (r[5] >> 11) & 1
        self.line_tx = (r[5] >> 12) & 1
        self.line_rx = (r[5] >> 13) & 1
        self.line = self.line_tx | self.line_rx
        self.enc = (r[5] >> 14) & 3
        self.dec = r[6] & 3
        self.crc_out = (r[6] >> 2) & 1
        self.crc_in = (r[6] >> 3) & 1
        self.crc_reflect = (r[6] >> 4) & 1
        self.poly = r[7]


class SM:
    def __init__(self, num):
        self.num = num
        self.reset()

    def reset(self):
        self.en = 0
        self.pc = 0
        self.x = self.y = self.isr = self.osr = 0
        self.isr_cnt = 0
        self.osr_cnt = 16     # OSR starts empty, so the first OUT autopulls (as on RP2040)
        self.dly = 0
        self.divc = 0
        self.val = 0          # pin output values (8 bits)
        self.dir = 0          # pin directions (8 bits)
        self.tick_prev = 0    # input sample at the previous tick (WAIT EDGE)
        self.clk_prev = 0     # CLK_PIN sample at the previous clock (edge clocking)
        self.rs_prev = 0      # IN_BASE sample at the previous clock (RESYNC)
        self.crc = 0
        self.reset_codec()
        self.timeout = self.codeerr = 0
        self.irq_wait = 0
        self.txbuf = 0
        self.txv = 0
        self.txn = 0          # nibbles assembled
        self.rxbuf = 0
        self.rxv = 0
        self.ir = 0           # instruction register (prefetch of mem[pc])
        self.xp = None        # EXEC'd instruction waiting in the IR (executes next clock)

    def reset_codec(self, rx_ref=0):
        self.tq = self.tqv = 0      # TX bit queue
        self.tx_run = 0
        self.tx_last = 0
        self.tx_ph = 0              # Manchester half
        self.tx_cur = 0             # Manchester bit in flight
        self.tx_act = 0             # a line bit is in progress
        self.rq = self.rqv = 0      # RX bit queue
        self.rx_run = 0
        self.rx_last = 0
        self.rx_prev = rx_ref       # NRZI reference (previous centre sample)


class Chip:
    def __init__(self):
        from collections import Counter
        self.cov = Counter()     # feature coverage, for test reporting only
        self.mem = [None] * 32
        self.cfg = [[None] * 8, [None] * 8]
        self.glob = [None, None]
        self.reset()

    def reset(self):
        self.sms = [SM(0), SM(1)]
        self.pa = self.pb = 0
        self.pn = self.pnr = 0   # falling-edge sample of uio and its rising-edge copy
        self.now = 0             # free-running clock counter (IN TIME)
        self.w1 = self.w2 = self.w3 = 0
        self.r1 = self.r2 = self.r3 = 0
        self.irq = 0
        self.shreg = 0
        self.ncount = 0
        self.pch = 0          # 0 command packet, 1 program word
        self.cmd_ready = 0
        self.prog_ready = 0
        self.pptr = 0
        self.rd_nib = 0

    # ------------------------------------------------------------ outputs
    def status_word(self):
        a, b = self.sms
        return (b.pc << 11) | (a.pc << 6) | (b.en << 5) | (a.en << 4) | self.irq

    def outputs(self, ui_in):
        ch = (ui_in >> 5) & 3
        a, b = self.sms
        if ch in (0, 1):
            e = self.sms[ch].rxbuf
            nib = (e >> (4 * self.rd_nib)) & 15
        elif ch == 2:
            nib = (self.status_word() >> (4 * self.rd_nib)) & 15
        else:
            nib = 0
        status = (b.rxv << 3) | ((1 - b.txv) << 2) | (a.rxv << 1) | (1 - a.txv)
        uo = (status << 4) | nib
        g0 = self.glob[0]
        if g0 is None:
            if a.dir or b.dir:
                raise RuntimeError('pins driven with undefined global config')
            return uo, 0, 0, 0
        od, owner = g0 & 0xFF, g0 >> 8
        val = (b.val & owner) | (a.val & ~owner & 0xFF)
        dirs = (b.dir & owner) | (a.dir & ~owner & 0xFF)
        uout = val & ~od & 0xFF
        oe = (dirs & ~(od & val)) & 0xFF
        return uo, uout, oe, 0xFF

    # ------------------------------------------------------------ one edge
    def edge(self, ui_in, uio_in, rst_n=1, uio_late=None):
        if not rst_n:
            self.reset()
            return self.outputs(ui_in)
        uio_late = uio_in if uio_late is None else uio_late
        g1 = self.glob[1] or 0
        link01, link10 = (g1 >> 1) & 1, (g1 >> 2) & 1
        nib = ui_in & 15
        ch = (ui_in >> 5) & 3
        mem_before = list(self.mem)                  # the instruction register samples these
        span_before = (self.glob[1] or 0) & 1
        wt_ev = self.w2 != self.w3
        rt_ev = self.r2 != self.r3
        nxt = {}   # chip-level next state
        sm_next = [dict(), dict()]
        mem_write = None
        cfg_write = None
        irq_set = 0
        irq_clr = 0

        # ---- host write events
        if wt_ev:
            if ch in (0, 1):
                s = self.sms[ch]
                f16 = Cfg(self.cfg[ch]).fifo16
                if not s.txv and not (link01 if ch == 1 else link10):
                    tb = (s.txbuf & ~(15 << (4 * s.txn))) | (nib << (4 * s.txn))
                    tb &= M16 if f16 else 0xFF
                    n = s.txn + 1
                    sm_next[ch]['txbuf'] = tb
                    if n >= (4 if f16 else 2):
                        sm_next[ch]['txv'] = 1
                        sm_next[ch]['txn'] = 0
                    else:
                        sm_next[ch]['txn'] = n
            else:
                pch = ch - 2
                cnt = self.ncount if pch == self.pch else 0
                nxt['pch'] = pch
                nxt['shreg'] = ((self.shreg << 4) | nib) & 0xFFFFFF
                cnt += 1
                if pch == 0 and cnt == 6:
                    nxt['cmd_ready'] = 1
                    cnt = 0
                elif pch == 1 and cnt == 4:
                    nxt['prog_ready'] = 1
                    cnt = 0
                nxt['ncount'] = cnt
        # ---- host read events
        if rt_ev:
            if ch in (0, 1):
                s = self.sms[ch]
                if s.rxv:
                    last = (4 if Cfg(self.cfg[ch]).fifo16 else 2) - 1
                    if self.rd_nib >= last and not (link01 if ch == 0 else link10):
                        nxt['rd_nib'] = 0
                        sm_next[ch]['rxv'] = 0
                    else:
                        nxt['rd_nib'] = (self.rd_nib + 1) & 3
            elif ch == 2:
                nxt['rd_nib'] = (self.rd_nib + 1) & 3

        # ---- deferred command / program word
        exec_req = [None, None]
        if self.cmd_ready:
            nxt['cmd_ready'] = 0
            addr = (self.shreg >> 16) & 0xFF
            data = self.shreg & M16
            tgt, reg = addr >> 4, addr & 15
            if tgt in (0, 1) and reg < 8:
                cfg_write = ('cfg', tgt, reg, data)
            elif tgt == 2 and reg < 2:
                cfg_write = ('glob', reg, data)
            elif tgt == 3:
                if reg == 0:
                    for i in (0, 1):
                        en = (data >> i) & 1
                        sm_next[i]['en'] = en
                        if en and not self.sms[i].en:
                            sm_next[i]['divc'] = 0
                elif reg == 1:
                    i = (data >> 8) & 1
                    sm_next[i].update(restart=data & 31)
                elif reg in (2, 3):
                    i = reg - 2
                    if not self.sms[i].en:
                        exec_req[i] = data      # into the IR now, executed on the next clock
                elif reg == 4:
                    nxt['pptr'] = data & 31
                elif reg == 5:
                    irq_clr |= data & 15
        if self.prog_ready:
            nxt['prog_ready'] = 0
            mem_write = (self.pptr, self.shreg & M16)
            nxt['pptr'] = (self.pptr + 1) & 31

        span = (self.glob[1] or 0) & 1
        # ---- state machines
        flags_now = self.irq
        for i, s in enumerate(self.sms):
            nx = sm_next[i]
            nx['xp'] = exec_req[i]
            if 'restart' in nx:
                continue      # applied below (restart wins over execution this clock)
            if s.en and not (i == 1 and span):
                self._run_sm(s, Cfg(self.cfg[i]), nx, flags_now, None)
            elif s.xp is not None:
                self._run_sm(s, Cfg(self.cfg[i]), nx, flags_now, s.xp)
            irq_set |= nx.pop('_irq_set', 0)
            irq_clr |= nx.pop('_irq_clr', 0)
        # ---- SM-to-SM links: a waiting RX entry moves into the other SM's empty
        # TX buffer (the SM itself never pushes into a full RX buffer or pulls
        # from an empty TX buffer in the same clock, so these never collide)
        for src, dst, on in ((0, 1, link01), (1, 0, link10)):
            a, b = self.sms[src], self.sms[dst]
            if on and a.rxv and not b.txv:
                self.cov['link'] += 1
                sm_next[src]['rxv'] = 0
                sm_next[dst].update(txbuf=a.rxbuf, txv=1, txn=0)
        # per-clock input history for edge clocking / resync (both SMs, always)
        for i, s in enumerate(self.sms):
            c = Cfg(self.cfg[i])
            ins = self.pa if c.in_fast else self.pb
            sm_next[i].setdefault('clk_prev', (ins >> c.clk_pin) & 1)
            sm_next[i].setdefault('rs_prev', (ins >> c.in_base) & 1)

        # ---- commit
        for i, s in enumerate(self.sms):
            nx = sm_next[i]
            if 'restart' in nx:
                pc = nx.pop('restart')
                s.pc = pc
                s.isr = s.osr = s.isr_cnt = 0
                s.osr_cnt = 16
                s.dly = 0
                s.divc = 0
                c = Cfg(self.cfg[i])
                s.reset_codec(((self.pa if c.in_fast else self.pb) >> c.in_base) & 1)
                s.timeout = s.codeerr = s.irq_wait = 0
            for k, v in nx.items():
                setattr(s, k, v)
        self.irq = (self.irq | irq_set) & ~irq_clr & 15
        for s in self.sms:                           # prefetch the instruction at the new PC
            s.ir = mem_before[self._addr(s.num, s.pc, span_before)] if s.xp is None else s.xp
        for k, v in nxt.items():
            setattr(self, k, v)
        # Latches capture during the clock-high phase after this edge, i.e. the
        # shift register's value *after* the edge (differs only if an illegal
        # host delivers another nibble on the very next clock).
        latch_data = self.shreg & M16
        if cfg_write:
            if cfg_write[0] == 'cfg':
                self.cfg[cfg_write[1]][cfg_write[2]] = latch_data
            else:
                self.glob[cfg_write[1]] = latch_data
        if mem_write:
            self.mem[mem_write[0]] = latch_data
        self.w1, self.w2, self.w3 = (ui_in >> 4) & 1, self.w1, self.w2
        self.r1, self.r2, self.r3 = (ui_in >> 7) & 1, self.r1, self.r2
        self.pa, self.pb = uio_in & 0xFF, self.pa
        self.pnr, self.pn = self.pn, uio_late & 0xFF
        self.now = (self.now + 1) & M16
        return self.outputs(ui_in)

    # ------------------------------------------------------------ SM step
    @staticmethod
    def _addr(num, pc, span):
        if num == 1:
            return 16 + (pc & 15)
        return (16 if (span and pc & 16) else 0) + (pc & 15)

    def fetch(self, s):
        if s.ir is None:
            raise RuntimeError(f'SM{s.num} fetched an undefined word')
        return s.ir

    def _run_sm(self, s, c, nx, flags, forced):
        if not c.defined:
            raise RuntimeError(f'SM{s.num} running with undefined configuration')
        ins_all = self.pa if c.in_fast else self.pb
        if forced is not None:
            before = dict(nx)
            if self._issue(s, c, nx, flags, forced, ins_all, True):
                nx.clear()            # a forced instruction that would stall has no effect
                nx.update(before)
            return
        # ---- tick generation
        if c.line:
            tick = True               # line mode: instructions every clock, divider = line timer
        elif c.clksrc == 0:
            tick = False
            if c.resync and ((ins_all >> c.in_base) & 1) != s.rs_prev:
                self.cov['resync'] += 1
                nx['divc'] = c.div >> 1
            elif s.divc == 0:
                tick = True
                nx['divc'] = c.div
            else:
                nx['divc'] = s.divc - 1
        else:
            cur = (ins_all >> c.clk_pin) & 1
            rise = cur and not s.clk_prev
            fall = (not cur) and s.clk_prev
            tick = (c.clksrc == 1 and rise) or (c.clksrc == 2 and fall) or (c.clksrc == 3 and (rise or fall))
        if tick:
            nx['tick_prev'] = ins_all
            if s.dly:
                nx['dly'] = s.dly - 1
            else:
                self._issue(s, c, nx, flags, self.fetch(s), ins_all, False)
        if c.line:
            self._line(s, c, nx, ins_all)

    def _line(self, s, c, nx, ins_all):
        """Line unit: one serializer/deserializer step per clock (spec section 5)."""
        cv = self.cov
        sbit = (ins_all >> c.in_base) & 1
        edge = sbit != s.rs_prev
        expire = s.divc == 0
        rx_evt = tx_evt = False
        man_bit = None
        if c.line_rx and c.dec == 3:                 # Manchester: edge timing with blanking
            if edge and expire:
                man_bit = sbit
                nx['divc'] = c.div
                tx_evt = True
            elif s.divc:
                nx['divc'] = s.divc - 1
        elif c.line_rx and c.resync and edge:
            cv['resync'] += 1
            nx['divc'] = c.div >> 1
        elif expire:
            nx['divc'] = c.div
            rx_evt = tx_evt = True
        else:
            nx['divc'] = s.divc - 1
        if not c.line_rx:
            rx_evt = False
        # ---- RX
        q_bit = None
        if man_bit is not None:
            cv['rx_dec3'] += 1
            q_bit = man_bit
        elif rx_evt:
            cv['rx_dec%d' % c.dec] += 1
            if c.dec == 1:
                bit = 1 if sbit == s.rx_prev else 0
            elif c.dec == 2:
                bit = 1 if sbit != s.rx_prev else 0
            else:
                bit = sbit
            nx['rx_prev'] = sbit
            if c.stuff_n and s.rx_run >= c.stuff_n and (not c.stuff_ones or s.rx_last == 1):
                cv['rx_destuff'] += 1
                if bit != (0 if c.stuff_ones else 1 - s.rx_last):
                    nx['codeerr'] = 1
                nx['rx_last'] = bit
                nx['rx_run'] = 1
            else:
                nx['rx_run'] = min(7, s.rx_run + 1) if (bit == s.rx_last and s.rx_run) else 1
                nx['rx_last'] = bit
                q_bit = bit
        if q_bit is not None:
            if s.rqv and nx.get('rqv', 1):            # still full (not taken this clock): overrun
                cv['rx_overrun'] += 1
                nx['codeerr'] = 1
            nx['rq'] = q_bit
            nx['rqv'] = 1
        # ---- TX
        if c.line_tx and tx_evt:
            lvl = None
            if c.enc == 3 and s.tx_ph:
                lvl = s.tx_cur
                nx['tx_ph'] = 0
            else:
                stuff = c.stuff_n and s.tx_run >= c.stuff_n and (not c.stuff_ones or s.tx_last == 1)
                bit = None
                if stuff:
                    cv['tx_stuff'] += 1
                    bit = 0 if c.stuff_ones else 1 - s.tx_last
                    nx['tx_last'] = bit
                    nx['tx_run'] = 1
                elif s.tqv:
                    bit = s.tq
                    nx['tqv'] = 0
                    nx['tx_run'] = min(7, s.tx_run + 1) if (bit == s.tx_last and s.tx_run) else 1
                    nx['tx_last'] = bit
                else:
                    nx['tx_act'] = 0
                    nx['tx_run'] = 0
                if bit is not None:
                    cv['tx_enc%d' % c.enc] += 1
                    nx['tx_act'] = 1
                    cur = (s.val >> c.out_base) & 1
                    if c.enc == 3:
                        lvl = 1 - bit
                        nx['tx_cur'] = bit
                        nx['tx_ph'] = 1
                    elif c.enc == 1:
                        lvl = cur if bit else 1 - cur
                    elif c.enc == 2:
                        lvl = 1 - cur if bit else cur
                    else:
                        lvl = bit
            if lvl is not None:
                v = nx.get('val', s.val)
                b0 = c.out_base
                v = (v & ~(1 << b0)) | (lvl << b0)
                if c.diff:
                    b1 = (b0 + 1) & 7
                    v = (v & ~(1 << b1)) | ((1 - lvl) << b1)
                nx['val'] = v

    def _issue(self, s, c, nx, flags, ins, pins, forced):
        """Issue one instruction. Writes next-state into nx (non-blocking)."""
        cv = self.cov
        cv['op%d' % (ins >> 13)] += 1
        if forced:
            cv['exec'] += 1
        if c.clksrc:
            cv['edge_clock'] += 1
        g = lambda k: nx.get(k, getattr(s, k))           # value as updated so far
        op = ins >> 13
        field = (ins >> 8) & 31
        delay_bits = 5 - c.side_count
        delay = field & ((1 << delay_bits) - 1)
        side_bits = c.side_count - c.side_en
        side_val = None
        if c.side_count:
            if c.side_en:
                if field & 16:
                    side_val = (field >> delay_bits) & ((1 << side_bits) - 1)
            else:
                side_val = (field >> delay_bits) & ((1 << side_bits) - 1)
        state = {'stall': False, 'jumped': False}
        pin_val, pin_dir = s.val, s.dir

        def setpins(base, count, data, dirs=False):
            nonlocal pin_val, pin_dir
            for k in range(count):
                b = (base + k) & 7
                v = (data >> k) & 1
                if dirs:
                    pin_dir = (pin_dir & ~(1 << b)) | (v << b)
                else:
                    pin_val = (pin_val & ~(1 << b)) | (v << b)

        def rot_in():
            return ((pins >> c.in_base) | (pins << (8 - c.in_base))) & 0xFF

        def jump(a):
            nx['pc'] = a & 31
            state['jumped'] = True

        def stall():
            state['stall'] = True

        x, y = s.x, s.y
        if op == 0:                                            # JMP
            cond = (ins >> 5) & 7
            addr = ins & 31
            take = False
            if cond == 0:
                take = True
            elif cond == 1:
                take = x == 0
            elif cond == 2:
                take = x != 0
                nx['x'] = (x - 1) & M16
            elif cond == 3:
                take = y == 0
            elif cond == 4:
                take = y != 0
                nx['y'] = (y - 1) & M16
            elif cond == 5:
                take = x != y
            elif cond == 6:
                take = bool((pins >> c.jmp_pin) & 1)
            else:
                if c.jflag == 0:
                    take = s.osr_cnt < c.pull_thresh
                elif c.jflag == 1:
                    take = s.crc == 0
                elif c.jflag == 2:
                    take = bool(s.timeout)
                    nx['timeout'] = 0
                else:
                    take = bool(s.codeerr)
                    nx['codeerr'] = 0
            if take:
                jump(addr)
        elif op == 1:                                          # WAIT
            pol = (ins >> 7) & 1
            src = (ins >> 5) & 3
            idx = ins & 31
            if src == 3:
                met = (not s.tx_act and not s.tqv) if pol else bool(s.tx_act)
                if not met:
                    stall()
            elif src == 2:
                f = ((idx & 3) + (s.num if idx & 16 else 0)) & 3
                met = ((flags >> f) & 1) == pol
                if met and pol:
                    nx['_irq_clr'] = nx.get('_irq_clr', 0) | (1 << f)
                if not met:
                    stall()
            else:
                pin = (idx & 7) if src != 1 else (c.in_base + (idx & 7)) & 7
                lvl = (pins >> pin) & 1
                met = lvl == pol
                if idx & 8:
                    met = met and ((s.tick_prev >> pin) & 1) != pol
                if not met:
                    if idx & 16:
                        if x == 0:
                            self.cov['wait_timeout'] += 1
                            nx['timeout'] = 1
                        else:
                            nx['x'] = (x - 1) & M16
                            stall()
                    else:
                        stall()
        elif op == 2:                                          # IN
            self._op_in(s, c, nx, ins, pins, rot_in, state)
        elif op == 3:                                          # OUT
            self._op_out(s, c, nx, ins, state, setpins, jump)
            pin_val, pin_dir = state.get('pv', pin_val), state.get('pd', pin_dir)
        elif op == 4:                                          # PUSH / PULL
            ifc = (ins >> 6) & 1
            block = (ins >> 5) & 1
            if not (ins >> 7) & 1:
                if not (ifc and s.isr_cnt < c.push_thresh):
                    if s.rxv:
                        if block:
                            stall()
                        else:
                            nx['isr'] = 0
                            nx['isr_cnt'] = 0
                    else:
                        self._push(s, c, nx, s.isr)
            else:
                if not (ifc and s.osr_cnt < c.pull_thresh):
                    if not s.txv:
                        if block:
                            stall()
                        else:
                            nx['osr'] = x
                            nx['osr_cnt'] = 0
                    else:
                        self._pull(s, c, nx)
        elif op == 5:                                          # MOV
            dst = (ins >> 5) & 7
            mop = (ins >> 3) & 3
            src = ins & 7
            if src == 5:
                full = bool(s.rxv) if c.status_sel else (not s.txv)
                v = M16 if full else 0
            else:
                v = [rot_in(), x, y, 0, s.crc, 0, s.isr, s.osr][src]
            if mop == 1:
                v = (~v) & M16
            elif mop == 2:
                v = bitrev16(v)
            if dst == 0:
                setpins(c.out_base, c.out_count, v)
            elif dst == 1:
                nx['x'] = v
            elif dst == 2:
                nx['y'] = v
            elif dst == 3:
                nx['crc'] = v
            elif dst == 5:
                jump(v)
            elif dst == 6:
                nx['isr'] = v
                nx['isr_cnt'] = 0
            elif dst == 7:
                nx['osr'] = v
                nx['osr_cnt'] = 0
        elif op == 6:                                          # IRQ
            clear = (ins >> 6) & 1
            wait = (ins >> 5) & 1
            f = ((ins & 3) + (s.num if ins & 16 else 0)) & 3
            if clear:
                nx['_irq_clr'] = nx.get('_irq_clr', 0) | (1 << f)
            elif not wait:
                nx['_irq_set'] = nx.get('_irq_set', 0) | (1 << f)
            elif not s.irq_wait:
                # set and wait: raise the flag, then stall until it is cleared again
                nx['_irq_set'] = nx.get('_irq_set', 0) | (1 << f)
                nx['irq_wait'] = 1
                stall()
            elif (flags >> f) & 1:
                stall()
            else:
                nx['irq_wait'] = 0
        else:                                                  # SET
            dst = (ins >> 5) & 7
            data = ins & 31
            if dst == 0:
                setpins(c.set_base, c.set_count, data)
            elif dst == 1:
                nx['x'] = data
            elif dst == 2:
                nx['y'] = data
            elif dst == 4:
                setpins(c.set_base, c.set_count, data, dirs=True)

        if state['stall'] and forced:
            return True
        # codec pin writes come back through state
        if 'pv' in state:
            pin_val, pin_dir = state['pv'], state.get('pd', pin_dir)
        # side-set (applied even when stalled, wins over the instruction)
        if side_val is not None:
            for k in range(side_bits):
                b = (c.side_base + k) & 7
                v = (side_val >> k) & 1
                if c.side_pindir:
                    pin_dir = (pin_dir & ~(1 << b)) | (v << b)
                else:
                    pin_val = (pin_val & ~(1 << b)) | (v << b)
        nx['val'] = pin_val
        nx['dir'] = pin_dir
        if state['stall']:
            return False
        if not forced:
            nx['dly'] = delay
            if not state['jumped']:
                nx['pc'] = c.wrap_bottom if s.pc == c.wrap_top else (s.pc + 1) & 31

    # ------------------------------------------------------------ helpers
    def _push(self, s, c, nx, isr):
        if c.fifo16:
            e = isr
        else:
            e = (isr >> 8) & 0xFF if not c.in_left else isr & 0xFF
        nx['rxbuf'] = e
        nx['rxv'] = 1
        nx['isr'] = 0
        nx['isr_cnt'] = 0

    def _pull(self, s, c, nx):
        e = s.txbuf
        if c.fifo16:
            v = e
        else:
            v = (e & 0xFF) if not c.out_left else (e & 0xFF) << 8
        nx['osr'] = v
        nx['osr_cnt'] = 0
        nx['txv'] = 0
        nx['txn'] = 0

    def _op_in(self, s, c, nx, ins, pins, rot_in, state):
        src = (ins >> 5) & 7
        cnt = ins & 31
        if cnt == 0 or cnt > 16:
            cnt = 16
        mask = (1 << cnt) - 1
        # autopush would be needed after this shift but RX buffer is full: stall first
        if c.autopush and min(16, s.isr_cnt + cnt) >= c.push_thresh and s.rxv:
            state['stall'] = True
            return
        if src == 0 and cnt == 1 and c.line_rx:
            if not s.rqv:
                state['stall'] = True
                return
            data = s.rq
            nx['rqv'] = 0
            if c.crc_in:
                nx['crc'] = self._crc(s.crc, data, c)
        elif src == 0 and c.in_ddr:
            self.cov['in_ddr'] += 1
            early, late = (self.pnr, self.pa) if c.in_fast else (self.pb, self.pnr)
            lo, hi = (late, early) if c.in_left else (early, late)
            rot = lambda v: ((v >> c.in_base) | (v << (8 - c.in_base))) & 0xFF
            lo, hi = rot(lo), rot(hi)
            data = sum((((lo >> k) & 1) << (2 * k)) | (((hi >> k) & 1) << (2 * k + 1)) for k in range(8)) & mask
        else:
            if src == 5:
                self.cov['in_time'] += 1
            data = [rot_in(), s.x, s.y, 0, s.crc, self.now, s.isr, s.osr][src] & mask
        if c.in_left:
            isr = ((s.isr << cnt) | data) & M16
        else:
            isr = ((s.isr >> cnt) | (data << (16 - cnt))) & M16
        n = min(16, s.isr_cnt + cnt)
        nx['isr'] = isr
        nx['isr_cnt'] = n
        if c.autopush and n >= c.push_thresh:
            self._push(s, c, nx, isr)

    def _op_out(self, s, c, nx, ins, state, setpins, jump):
        dst = (ins >> 5) & 7
        cnt = ins & 31
        if cnt == 0 or cnt > 16:
            cnt = 16
        osr, ocnt = s.osr, s.osr_cnt
        if c.autopull and ocnt >= c.pull_thresh:
            if not s.txv:
                state['stall'] = True
                return
            e = s.txbuf
            osr = e if c.fifo16 else ((e & 0xFF) << 8 if c.out_left else e & 0xFF)
            ocnt = 0
            nx['txv'] = 0
            nx['txn'] = 0
            nx['osr'] = osr
            nx['osr_cnt'] = 0
        mask = (1 << cnt) - 1
        if c.out_left:
            data = (osr >> (16 - cnt)) & mask
            rest = (osr << cnt) & M16
        else:
            data = osr & mask
            rest = (osr >> cnt) & M16
        pv, pd = s.val, s.dir
        if dst == 0 and cnt == 1 and c.line_tx:
            if s.tqv:
                state['stall'] = True
                return
            nx['tq'] = data
            nx['tqv'] = 1
            if c.crc_out:
                nx['crc'] = self._crc(s.crc, data, c)
        elif dst == 0:
            for k in range(c.out_count):
                b = (c.out_base + k) & 7
                pv = (pv & ~(1 << b)) | (((data >> k) & 1) << b)
            state['pv'], state['pd'] = pv, pd
        elif dst == 1:
            nx['x'] = data
        elif dst == 2:
            nx['y'] = data
        elif dst == 4:
            for k in range(c.out_count):
                b = (c.out_base + k) & 7
                pd = (pd & ~(1 << b)) | (((data >> k) & 1) << b)
            state['pv'], state['pd'] = pv, pd
        elif dst == 5:
            jump(data)
        elif dst == 6:
            nx['isr'] = data
            nx['isr_cnt'] = cnt
        elif dst == 7:
            nx['crc'] = data
        nx['osr'] = rest
        nx['osr_cnt'] = min(16, ocnt + cnt)

    def _crc(self, crc, bit, c):
        self.cov['crc_reflect' if c.crc_reflect else 'crc'] += 1
        return self._crc_raw(crc, bit, c)

    @staticmethod
    def _crc_raw(crc, bit, c):
        if c.crc_reflect:
            fb = (crc & 1) ^ bit
            return ((crc >> 1) ^ (c.poly if fb else 0)) & M16
        fb = ((crc >> 15) & 1) ^ bit
        return (((crc << 1) & M16) ^ (c.poly if fb else 0)) & M16
