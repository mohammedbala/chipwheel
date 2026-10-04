#!/usr/bin/env python3
"""End-to-end acceptance checks against an already completed smoke campaign."""
import argparse
import collections
import fcntl
import json
from pathlib import Path
import tempfile
import threading
from campaign import CAMPAIGNS, safe_dir, read, totals, worker_job, atomic

def validate(folder):
    m=read(folder/'manifest.json');before=totals(folder)
    assert m['kind']=='acceptance'
    assert before['checked']==sum(m['stage_budgets'].values())
    assert before['failed']==0
    assert all(sum(before['by_stage'][stage].values())==n for stage,n in m['stage_budgets'].items())
    lock=(CAMPAIGNS/'worker.lock').open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    pause=folder/'pause';pause.unlink(missing_ok=True)
    full=worker_job(folder,'A','batch','calibration',0,100)
    timer=threading.Timer(.3,pause.touch);timer.start()
    try:head=worker_job(folder,'A','batch','calibration',0,100)
    finally:timer.cancel();pause.unlink(missing_ok=True)
    assert head['paused'] and 0<head['checked']<100
    tail=worker_job(folder,'A','batch','calibration',head['checked'],100-head['checked'])
    for key in ['checked','passed','failed','cycles','transactions']:
        assert full[key]==head[key]+tail[key],key
    combined=collections.Counter(head['coverage']);combined.update(tail['coverage'])
    assert dict(combined)==full['coverage']
    infra=False
    with tempfile.TemporaryDirectory() as d:
        source=Path(d)/'broken.v';source.write_text('intentionally invalid RTL\n')
        try:worker_job(folder,'A','batch','calibration',0,1,source_override=source)
        except RuntimeError:infra=True
    assert infra
    assert totals(folder)['checked']==before['checked']
    # Generate one genuine diagnostic waveform; it also must not touch the ledger.
    replay=worker_job(folder,'A','batch','calibration',99,1,waves=True)
    assert replay['checked']==1 and replay['failed']==0
    fst=folder/'work/tb.fst';assert fst.exists() and fst.stat().st_size>0
    assert totals(folder)['checked']==before['checked']
    result=dict(status='passed',campaign=m['id'],checked=before['checked'],
                uninterrupted_cases=full['checked'],pause_prefix=head['checked'],resume_suffix=tail['checked'],
                same_cycles=True,same_coverage=True,infrastructure_failure_not_counted=True,
                replay_not_counted=True,replay_waveform_bytes=fst.stat().st_size)
    atomic(folder/'acceptance-validation.json',result);print(json.dumps(result,indent=2));lock.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('id');validate(safe_dir(p.parse_args().id))
