"""One simulator batch. Only clean XML + final report can enter the ledger."""
import hashlib
import json
import os
import time
import traceback
from pathlib import Path
import cocotb
from campaign_cases import Bench, run_case, scenario_spec, qualify, QualificationPaused
from campaign_designs import uart

def atomic(path, obj):
    path=Path(path); tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(obj));os.replace(tmp,path)

@cocotb.test()
async def campaign_batch(dut):
    job=json.loads(Path(os.environ['CW_JOB']).read_text())
    b=Bench(dut); start=time.monotonic(); checked=0;failure=None; coverage={}; category_seconds={}; digest=hashlib.sha256()
    cfg=job['candidate'];paused=False
    def report(final=False):
        return dict(final=final,checked=checked,passed=checked-int(failure is not None),failed=int(failure is not None),
                    failure=failure,paused=paused,cycles=b.cycles,transactions=b.checked,timeouts=b.timeouts,
                    completed_uart_transactions=b.completed,transaction_note='Transactions count UART frames; checked counts scenarios',
                    seconds=time.monotonic()-start,coverage=coverage,category_seconds=category_seconds,digest=digest.hexdigest())
    if job['mode']=='qualify':
        def qualification_progress(done):
            nonlocal checked
            checked=done
            if checked%100==0:atomic(job['progress'],dict(report(),mode='qualify'))
        try:
            checked=await qualify(b,cfg,job['pause_file'],qualification_progress)
        except QualificationPaused as exc:
            checked=exc.checked;paused=True
        except AssertionError as exc:
            failure=dict(message=str(exc) or 'Public-signal assertion failed',traceback=traceback.format_exc(),mode='qualification');checked+=1
    elif job['mode']=='mutation':
        try:
            await b.reset();await b.load(uart(cfg,8));await b.frame(65,8);checked=1
        except AssertionError as exc:
            failure=dict(message=str(exc) or 'Public-signal assertion failed',traceback=traceback.format_exc(),mode='mutation');checked=1
    else:
        for i in range(job['start'],job['start']+job['count']):
            s=scenario_spec(job['seed'],job['stage'],i)
            case_started=time.monotonic()
            try:
                await run_case(b,cfg,s)
            except AssertionError as exc:
                failure=dict(message=str(exc) or 'Public-signal assertion failed',traceback=traceback.format_exc(),case_id=i,stage=job['stage'],seed=job['seed'],scenario=s)
            checked+=1
            category_seconds[s['category']]=category_seconds.get(s['category'],0)+time.monotonic()-case_started
            keys=[s['category']]
            if s['category']=='uart':keys += ['byte:'+str(s['byte']),'duration:'+str(s['duration'])]
            if s['category']=='control':keys+=['control:'+s['control']]
            if s['category']=='boundary':keys+=['boundary:'+s['boundary']]
            for key in keys:coverage[key]=coverage.get(key,0)+1
            digest.update(json.dumps([i,s,failure is None],sort_keys=True).encode())
            if checked%100==0:atomic(job['progress'],report())
            paused=Path(job['pause_file']).exists()
            if failure or paused:break
    atomic(job['report'],report(True))
