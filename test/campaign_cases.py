"""Public-signal scenarios. Expected pin schedules never read RTL internals."""
import hashlib
import random
from pathlib import Path
from campaign_designs import uart
from test import Bench, control_regression
from checker import uart_expected

CATEGORIES = ['uart', 'pulse', 'control', 'boundary']
CONTROL = ['reset', 'pause', 'held_start', 'write_busy', 'write_start', 'disabled_start', 'busy_start']
BOUNDARY = ['wait', 'count', 'invalid', 'last_address', 'address', 'self_loop', 'long_uart', 'shift']

def scenario_spec(seed, stage, index):
    seed_bytes = hashlib.sha256(f'{seed}:{stage}:{index}'.encode()).digest()
    rng = random.Random(int.from_bytes(seed_bytes, 'big'))
    # Exact mix per 100 cases, common inputs across candidate architectures.
    slot = index % 100
    cuts = [20, 40, 80] if stage == 'stress' else [60, 85, 95]
    category = CATEGORIES[sum(slot >= n for n in cuts)]
    spec = {'category': category, 'byte': rng.randrange(256), 'duration': rng.choice([3,4,8,17,31,80]),
            'gap': rng.randrange(13), 'repeat': 1+int(index % 8 == 0),
            'low': rng.randrange(2,65), 'high': rng.randrange(3,65), 'count': rng.randrange(1,9),
            'control': rng.choice(CONTROL), 'boundary': rng.choice(BOUNDARY),
            'value': rng.randrange(65536), 'abort': rng.randrange(82)}
    return spec

async def public_trace(b, length, expected, error=0):
    await b.trace(length, expected, error)

async def control(b, cfg, s):
    kind = s['control']
    if kind in ('pause', 'write_busy'):
        await control_regression(b)
    elif kind == 'reset':
        await b.load(uart(cfg,8)); await b.start(s['byte'])
        expected = uart_expected(s['byte'],8)
        for i in range(s['abort']):
            assert await b.tick() == 2 | expected[i]
        assert await b.tick(rst_n=0,ena=0) == 1
        await b.tick(rst_n=1,ena=1,uio_in=0)
        await b.frame(s['byte'],8)
    elif kind == 'held_start':
        await b.load([0]); await b.start()
        for _ in range(5):
            assert await b.tick() == 1
        await b.start(); assert await b.tick() == 1
    elif kind == 'write_start':
        await b.load([0]); assert await b.tick(ui_in=0,uio_in=3) == 5
        assert await b.tick(uio_in=0) == 5
        await b.start(); assert await b.tick() == 1
    elif kind == 'disabled_start':
        await b.load([0]); assert await b.tick(ena=0,uio_in=1) == 1
        assert await b.tick(ena=1) == 1
        await b.start(); assert await b.tick() == 1
    else:
        await b.load(uart(cfg,8)); await b.start(s['byte'])
        for e, pin in enumerate(uart_expected(s['byte'],8),1):
            actual = await b.tick(ui_in=s['byte']^255,uio_in=0 if e==3 else 1)
            assert actual == (2 if e<82 else 0) | pin
        assert await b.tick() == 1

async def boundary(b,cfg,s):
    kind=s['boundary']; n=cfg['words']; v=s['value']
    if kind=='wait':
        delay=[0,1,2,4095][v%4]
        await b.load([0x1000,0x2000+delay,0x1001,0]); await b.start()
        await public_trace(b,delay+4,[0]*(delay+2)+[1,1])
    elif kind=='count':
        count=[0,1,2,255][v%4]; count_cycles=max(1,count)
        await b.load([0x4000+count,0x1000,0x5002,0x1001,0]); await b.start()
        await public_trace(b,count_cycles+4,[1]+[0]*(count_cycles+1)+[1,1])
    elif kind=='invalid':
        invalid=[1,0x1002,0x3001,0x4100,0x5000+n,0x6000+n]+[op<<12 for op in range(8 if cfg['fused'] else 7,16)]
        await b.load([invalid[v%len(invalid)]]); await b.start()
        await public_trace(b,1,[1],1)
    elif kind=='last_address':
        bad=bool(v%2)
        await b.load([0x6000+n-1]+[0]*(n-2)+[0x1000 if bad else 0x6001]); await b.start()
        await public_trace(b,2 if bad else 3,[1]*(2 if bad else 3),int(bad))
    elif kind=='address':
        # Exercise both bytes of all invalid addresses, then execute the would-be alias.
        addr=16+v%16;alias=addr%16;high=(v//16)%2
        image=[0]*n
        if alias:image[0]=0x6000+alias
        await b.load(image)
        out=await b.tick(ui_in=0x10 if high else 0xff,uio_in=(addr<<3)|2|(high<<2))
        assert out==(5 if n==16 else 1)
        await b.tick(uio_in=0); await b.start()
        await public_trace(b,2 if alias else 1,[1]*(2 if alias else 1))
    elif kind=='self_loop':
        await b.load([0x6000]); await b.start()
        for _ in range(12): assert await b.tick()==3
        await b.reset()
    elif kind=='long_uart':
        duration=[255,256,4095,4096][v%4]
        await b.load(uart(cfg,duration)); await b.frame(s['byte'],duration)
    else:
        await b.load([0x3000]*9+[0]); await b.start(0x81)
        await public_trace(b,10,[1,0,0,0,0,0,0,1,0,1])

async def run_case(b,cfg,s):
    # Each case starts at a reproducible external reset; memory isn't preloaded.
    await b.reset()
    if s['category']=='uart':
        await b.load(uart(cfg,s['duration']))
        for k in range(s['repeat']):
            await b.frame(s['byte']^(255 if k else 0),s['duration'],s['gap'] if k else 0)
    elif s['category']=='pulse':
        lo,hi,count=s['low'],s['high'],s['count']
        await b.load([0x4000+count,0x1000,0x2000+lo-2,0x1001,0x2000+hi-3,0x5001,0])
        await b.start()
        # Desired low/high schedule is defined independently of instruction execution.
        expected=[1]+([0]*lo+[1]*hi)*count+[1]
        await public_trace(b,2+count*(lo+hi),expected)
    elif s['category']=='control':
        await control(b,cfg,s)
    else:
        await boundary(b,cfg,s)

class QualificationPaused(Exception):
    def __init__(self,checked):self.checked=checked

async def qualify(b,cfg,pause_file=None,onprogress=None):
    checked=0
    def checkpoint():
        if onprogress:onprogress(checked)
        if pause_file and Path(pause_file).exists():raise QualificationPaused(checked)
    # All byte values at normal durations plus extremal waits/durations.
    for duration in [3,4,8,17,31,80,255,256,4095,4096]:
        await b.reset(); await b.load(uart(cfg,duration))
        for byte in (range(256) if duration<=80 else [0,1,65,128,255]):
            await b.frame(byte,duration,byte%4); checked+=1;checkpoint()
    for kind in CONTROL:
        for abort in (range(82) if kind=='reset' else [0]):
            s=scenario_spec(1,'qualification',0);s.update(category='control',control=kind,abort=abort)
            await run_case(b,cfg,s);checked+=1;checkpoint()
    for kind in BOUNDARY:
        for value in range(32):
            s=scenario_spec(1,'qualification',0);s.update(category='boundary',boundary=kind,value=value)
            await run_case(b,cfg,s);checked+=1;checkpoint()
    for i in range(10):
        s=scenario_spec(1,'qualification',i);s['category']='pulse';await run_case(b,cfg,s);checked+=1;checkpoint()
    n=cfg['words']
    await b.reset();await b.load([0x6000+n-1]+[0]*(n-2)+[0x5000]);await b.start()
    await public_trace(b,2,[1,1],1);checked+=1;checkpoint()
    await b.reset();await b.load([0x4002,0x6000+n-1]+[0]*(n-3)+[0x5002]);await b.start()
    await public_trace(b,4,[1,1,1,1]);checked+=1;checkpoint()
    if cfg['fused']:
        for delay in [0,1,2,4095]:
            await b.reset(); await b.load([0x7000+delay,0]); await b.start(0)
            await public_trace(b,delay+2,[0]*(delay+1)+[1]); checked+=1;checkpoint()
        await b.reset(); await b.load([0x7000]*9+[0]);await b.start(0x81)
        await public_trace(b,10,[1,0,0,0,0,0,0,1,0,1]);checked+=1
    return checked
