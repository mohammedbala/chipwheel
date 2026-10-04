#!/usr/bin/env python3
"""Single-worker, content-addressed, resumable RTL experiment campaigns."""
import argparse
import collections
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

# Hardware helpers must share the controller's lock, including CLI execution.
if __name__ == '__main__':sys.modules['campaign']=sys.modules[__name__]

from campaign_designs import CANDIDATES, generate, mutation

ROOT=Path(os.environ.get('CW_PROJECT_ROOT',Path(__file__).resolve().parents[1]))
CAMPAIGNS=ROOT/'results/campaigns'
STAGES={'calibration':100000,'screening':400000,'stress':2000000,'holdout':7500000}
FREE_FLOOR=5*1024**3
ARTIFACT_LIMIT=1024**3
_LOCK_FD=None

def lock_fds():return (_LOCK_FD,) if _LOCK_FD is not None else ()
INPUTS=['src/project.v','src/config.json','info.yaml','docs/spec.md','test/tb.v','test/test.py',
        'test/checker.py','test/campaign_cases.py','test/campaign_worker.py','programs/encode.py',
        'scripts/campaign.py','scripts/campaign_designs.py','scripts/campaign_hardware.py',
        '.tools/pdk-lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib','docs/campaign.md']

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path,default=None):
    try:return json.loads(Path(path).read_text())
    except FileNotFoundError:return {} if default is None else default
def atomic(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w') as f:
        json.dump(obj,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(temp,path)
def environment():
    env=os.environ.copy()
    env['PATH']=str(ROOT/'.tools/icarus-verilog/13.0/bin')+os.pathsep+env['PATH']
    return env
def tools_signature():
    iv=subprocess.check_output([str(ROOT/'.tools/icarus-verilog/13.0/bin/iverilog'),'-V'],stderr=subprocess.DEVNULL,text=True).splitlines()[0]
    cv=subprocess.check_output([str(ROOT/'.venv/bin/python'),'-c','import cocotb,platform;print(cocotb.__version__+" / "+platform.python_version())'],text=True).strip()
    flow=subprocess.check_output([str(ROOT/'.tools/flow-venv/bin/python'),'-c','import importlib.metadata as m;print(m.version("yowasp-yosys"),m.version("librelane"))'],text=True).strip()
    return {'iverilog':iv,'cocotb_python':cv,'yosys_librelane':flow}
def safe_dir(id):
    if not id or not all(c.isalnum() or c=='-' for c in id):raise ValueError('Invalid campaign ID')
    path=CAMPAIGNS/id
    if not (path/'manifest.json').exists():raise ValueError('Campaign not found')
    return path
def init(smoke=False,seed=20261003):
    inputs={p:sha(ROOT/p) for p in INPUTS}
    if read(ROOT/'src/config.json').get('CLOCK_PERIOD')!=100:raise ValueError('This campaign requires a frozen 100 ns clock target')
    config={'schema':1,'seed':seed,'stage_budgets':dict.fromkeys(STAGES,400) if smoke else STAGES,
            'kind':'acceptance' if smoke else 'ten-million','input_hashes':inputs,'tools':tools_signature(),
            'clock_period_ns':100,'tiles':'6x4','batch_limit':10000,'workers':1,
            'pdk_revision':'2bbec755dc67ca3db0261c3d6163e15735d66710',
            'support_revision':'d66cf179e7bc4d296362ab7e2e3b344dc3c4f665',
            'action_revision':'3412659307918422f3f0727917cf9b499aaca588'}
    config['source_revision']=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    revision=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
    id=('acceptance-' if smoke else 'cw10m-')+revision[:16]
    folder=CAMPAIGNS/id
    if (folder/'manifest.json').exists():return folder
    folder.mkdir(parents=True,exist_ok=True)
    config.update(id=id,revision=revision,created=time.time(),candidates={})
    for file in INPUTS:
        dest=folder/'frozen'/file;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/file,dest)
    for cid,definition in CANDIDATES.items():
        dest=folder/'candidates'/cid;dest.mkdir(parents=True)
        source=generate((ROOT/'src/project.v').read_text(),definition)
        (dest/'project.v').write_text(source)
        cfg=dict(definition,id=cid,rtl_sha256=sha(dest/'project.v'),uart_words=8 if definition['fused'] else 9,
                 load_clocks=16 if definition['fused'] else 18)
        config['candidates'][cid]=cfg
        atomic(dest/'encoder.json',cfg)
        (dest/'SPEC.txt').write_text('Inherits frozen/docs/spec.md.\n'+f"Memory {cfg['words']} x 16 bits.\n"+
            ('Addresses 16..31: idle writes are ignored and set sticky error; no alias. Branch targets >=16 fault.\n' if cfg['words']==16 else '')+
            ('Opcode 7 SHIFTWAIT N: emit data[0], shift right, advance PC, stall next N enabled edges; N=0..4095. UART uses SHIFTWAIT B-2.\n' if cfg['fused'] else 'Opcode 7 remains invalid.\n'))
    atomic(folder/'manifest.json',config)
    connect(folder).close()
    atomic(folder/'state.json',{'status':'ready','id':id,'checked':0,'target':sum(config['stage_budgets'].values())})
    return folder

def connect(folder):
    con=sqlite3.connect(folder/'ledger.sqlite',timeout=10)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE IF NOT EXISTS batches(stage TEXT,candidate TEXT,start INTEGER,count INTEGER,passed INTEGER,failed INTEGER,seconds REAL,cycles INTEGER,report TEXT,PRIMARY KEY(stage,candidate,start))')
    con.commit();return con
def totals(folder):
    with connect(folder) as con:rows=con.execute('SELECT stage,candidate,start,count,passed,failed,seconds,cycles,report FROM batches ORDER BY rowid').fetchall()
    out={'checked':0,'passed':0,'failed':0,'timeouts':0,'seconds':0,'cycles':0,'build_seconds':0,'category_seconds':{},'stage_seconds':{},'by_stage':{},'by_candidate':{},'coverage':{},'failures':[]}
    for stage,cid,start,count,passed,failed,secs,cycles,raw in rows:
        report=json.loads(raw)
        for k,v in [('checked',count),('passed',passed),('failed',failed),('seconds',secs),('cycles',cycles)]:out[k]+=v
        out['timeouts']+=report.get('timeouts',0);out['build_seconds']+=report.get('build_seconds',0)
        out['stage_seconds'][stage]=out['stage_seconds'].get(stage,0)+secs
        for k,v in report.get('category_seconds',{}).items():out['category_seconds'][k]=out['category_seconds'].get(k,0)+v
        out['by_stage'].setdefault(stage,{})[cid]=out['by_stage'].get(stage,{}).get(cid,0)+count
        c=out['by_candidate'].setdefault(cid,{'checked':0,'failed':0,'coverage':{}});c['checked']+=count;c['failed']+=failed
        for key,val in report.get('coverage',{}).items():
            c['coverage'][key]=c['coverage'].get(key,0)+val;out['coverage'][key]=out['coverage'].get(key,0)+val
        if report.get('failure'):out['failures'].append(dict(report['failure'],candidate=cid,evidence=f'batches/{stage}-{cid}-{start}.json'))
    return out
def commit(folder,stage,cid,start,report):
    if not report.get('final'):raise ValueError('Incomplete reports cannot count')
    count=report['checked']
    if count<=0 or report['passed']+report['failed']!=count or report['failed'] not in (0,1):raise ValueError('Invalid report counts')
    with connect(folder) as con:
        existing=con.execute('SELECT report FROM batches WHERE stage=? AND candidate=? AND start=?',(stage,cid,start)).fetchone()
        if existing:
            if json.loads(existing[0])!=report:raise ValueError('Conflicting duplicate batch')
            return
        current=con.execute('SELECT COALESCE(SUM(count),0) FROM batches WHERE stage=? AND candidate=?',(stage,cid)).fetchone()[0]
        if current!=start:raise ValueError('Non-contiguous batch')
        con.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?,?,?)',(stage,cid,start,count,report['passed'],report['failed'],report['seconds'],report['cycles'],json.dumps(report,sort_keys=True)))
    atomic(folder/'batches'/f'{stage}-{cid}-{start}.json',report)
def allocation(pool,budget,counts):
    available=budget-sum(n for c,n in counts.items() if c not in pool)
    # Water-filling retains previously spent checks when finalists change.
    quotas={c:counts.get(c,0) for c in pool}
    remaining=available-sum(quotas.values())
    if remaining<0:raise ValueError('Stage budget exceeded')
    while remaining:
        minimum=min(quotas.values());lowest=sorted(c for c in pool if quotas[c]==minimum)
        upper=min((v for v in quotas.values() if v>minimum),default=minimum+remaining)
        step=min(upper-minimum,remaining//len(lowest))
        if step:
            for c in lowest:quotas[c]+=step
            remaining-=step*len(lowest)
        else:
            for c in lowest[:remaining]:quotas[c]+=1
            remaining=0
    return quotas
def verify_frozen(folder,manifest):
    for file,digest in manifest['input_hashes'].items():
        if sha(folder/'frozen'/file)!=digest:raise ValueError('Frozen input changed: '+file)
    for cid,cfg in manifest['candidates'].items():
        if sha(folder/'candidates'/cid/'project.v')!=cfg['rtl_sha256']:raise ValueError('Candidate changed: '+cid)
    if tools_signature()!=manifest['tools']:raise ValueError('Tool versions changed; create a new campaign')
def repair_reports(folder):
    with connect(folder) as con:rows=con.execute('SELECT stage,candidate,start,report FROM batches').fetchall()
    for stage,cid,start,raw in rows:
        path=folder/'batches'/f'{stage}-{cid}-{start}.json'
        if not path.exists():atomic(path,json.loads(raw))
def resource_reason(folder):
    if shutil.disk_usage(ROOT).free<FREE_FLOOR:return 'Free disk is below the 5 GiB reserve'
    size=sum(p.stat().st_size for p in folder.rglob('*') if p.is_file() and not p.is_symlink())
    if size>=ARTIFACT_LIMIT:return 'Campaign artifacts reached the 1 GiB limit'
    return None
def write_state(folder,status,**kwargs):
    m=read(folder/'manifest.json');t=totals(folder);target=sum(m['stage_budgets'].values())
    rates=t['checked']/t['seconds'] if t['seconds'] else None
    stage_rates={s:sum(t['by_stage'][s].values())/seconds for s,seconds in t['stage_seconds'].items() if seconds>0}
    costs={k:seconds/t['coverage'][k] for k,seconds in t['category_seconds'].items() if t['coverage'].get(k)}
    eta=0;complete_costs=all(k in costs for k in ('uart','pulse','control','boundary'))
    for stage,budget in m['stage_budgets'].items():
        remaining=budget-sum(t['by_stage'].get(stage,{}).values())
        weights=[.2,.2,.4,.2] if stage=='stress' else [.6,.25,.1,.05]
        cost=sum(costs[k]*weight for k,weight in zip(('uart','pulse','control','boundary'),weights)) if complete_costs else (1/rates if rates else None)
        if cost is None:eta=None;break
        eta+=remaining*cost
    atomic(folder/'state.json',dict(id=m['id'],revision=m['revision'],status=status,target=target,updated=time.time(),
            eta_seconds=eta,checks_per_second=rates,stage_rates=stage_rates,estimate_basis='remaining stage mix' if complete_costs else 'early measured rate',**t,**kwargs))
def worker_job(folder,cid,mode,stage='',start=0,count=0,fault=None,waves=False,source_override=None,models=None):
    m=read(folder/'manifest.json');cfg=m['candidates'][cid]
    # Reuse just one transient work directory; archival reports remain compact.
    work=folder/'work';work.mkdir(exist_ok=True)
    for name in ('report.json','progress.json','results.xml'):(work/name).unlink(missing_ok=True)
    source=folder/'candidates'/cid/'project.v'
    if source_override:source=Path(source_override)
    if fault:
        source=work/'fault.v';source.write_text(mutation((folder/'candidates'/cid/'project.v').read_text(),fault))
    job=dict(mode=mode,candidate=cfg,stage=stage,start=start,count=count,seed=m['seed'],
             source=str(source),models=[str(p) for p in models or []],frozen=str(folder/'frozen'),build=str(folder/'build'/hashlib.sha256((sha(source)+''.join(sha(p) for p in models or [])).encode()).hexdigest()[:16]),
             report=str(work/'report.json'),progress=str(work/'progress.json'),pause_file=str(folder/'pause'),
             waves=waves)
    atomic(work/'job.json',job)
    command=[str(ROOT/'.venv/bin/python'),str(folder/'frozen/scripts/campaign.py'),'worker',str(work/'job.json')]
    with (work/'worker.log').open('w') as log:
        proc=subprocess.Popen(command,env=environment(),stdout=log,stderr=subprocess.STDOUT,start_new_session=True,pass_fds=lock_fds())
        started=time.monotonic()
        while proc.poll() is None:
            if shutil.disk_usage(ROOT).free<FREE_FLOOR:(folder/'pause').touch()
            if time.monotonic()-started>3600:
                os.killpg(proc.pid,signal.SIGKILL);proc.wait();raise RuntimeError('Batch exceeded 1-hour wall timeout; no checks committed')
            time.sleep(.5)
    report=read(work/'report.json')
    xml=work/'results.xml'
    if proc.returncode or not xml.exists() or ET.parse(xml).findall('.//failure') or ET.parse(xml).findall('.//error') or not report.get('final'):
        raise RuntimeError('Simulator infrastructure failure; no batch checks committed. See work/worker.log')
    return report
def worker(path):
    from cocotb_tools.runner import get_runner
    job=read(path);frozen=Path(job['frozen']);work=Path(path).parent
    os.environ.update(environment())
    sys.path[:0]=[str(frozen/'test'),str(frozen/'scripts'),str(frozen)]
    runner=get_runner('icarus');started=time.monotonic()
    runner.build(sources=[Path(p) for p in job.get('models',[])]+[Path(job['source']),frozen/'test/tb.v'],hdl_toplevel='tb',build_dir=job['build'],always=False,
                 build_args=['-g2012','-DFUNCTIONAL','-DSIM'],timescale=('1ns','1ps'))
    build_seconds=time.monotonic()-started
    runner.test(hdl_toplevel='tb',test_module='campaign_worker',test_dir=work,
                extra_env={'CW_JOB':str(path),'PYTHONPATH':os.pathsep.join([str(frozen/'test'),str(frozen/'scripts'),str(frozen)])},
                waves=job['waves'],plusargs=['+waves'] if job['waves'] else [],results_xml=str(work/'results.xml'))
    r=read(job['report']);r.update(build_seconds=build_seconds,peak_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                                 rtl_sha256=sha(job['source']))
    atomic(job['report'],r)

def physical_ok(result):
    return result.get('status')=='passed' and result.get('routing')=='passed' and result.get('precheck')=='passed' and result.get('gate_regression')=='passed' and all(type(result.get(k)) in (int,float) and math.isfinite(result[k]) and result[k]>=0 for k in ('setup_slack_ns','hold_slack_ns')) and type(result.get('area_um2')) in (int,float) and math.isfinite(result['area_um2']) and result['area_um2']>0
def measurement_current(folder,cid,result,kind):
    m=read(folder/'manifest.json')
    if result.get('rtl_sha256')!=m['candidates'][cid]['rtl_sha256'] or result.get('config_sha256')!=m['input_hashes']['src/config.json']:return False
    if kind=='mapping':
        netlist=folder/'candidates'/cid/'mapped-netlist.v'
        return result.get('library_sha256')==m['input_hashes']['.tools/pdk-lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib'] and netlist.exists() and result.get('netlist_sha256')==sha(netlist)
    return result.get('pdk_revision')==m['pdk_revision']
def qualified(folder,cid,cfg):
    q=read(folder/'candidates'/cid/'qualification.json')
    return q.get('status')=='passed' and q.get('rtl_sha256')==cfg['rtl_sha256']
def rank(folder,pool):
    m=read(folder/'manifest.json');hw={c:read(folder/'candidates'/c/'hardware.json') for c in pool}
    valid=[c for c in pool if physical_ok(hw[c].get('physical',{})) and measurement_current(folder,c,hw[c]['physical'],'physical')]
    physical=len(valid)>=2 or len(valid)==len(pool)
    eligible=valid if physical else [c for c in pool if hw[c].get('mapping',{}).get('status')=='passed' and (hw[c]['mapping'].get('area_um2') or 0)>0 and measurement_current(folder,c,hw[c]['mapping'],'mapping')]
    def key(c):
        cfg=m['candidates'][c];metric=hw[c]['physical' if physical else 'mapping']
        return (metric['area_um2'],-cfg['words'],cfg['uart_words'],cfg['load_clocks'],c)
    return sorted(eligible,key=key),'physical' if physical else 'provisional mapped-area results'

def run(folder,max_batches=None):
    global _LOCK_FD
    from campaign_hardware import measure
    lock=(CAMPAIGNS/'worker.lock').open('a+')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise RuntimeError('Another campaign worker is running')
    lock.seek(0);lock.truncate();lock.write(str(os.getpid()));lock.flush()
    _LOCK_FD=lock.fileno()
    try:os.nice(10)
    except OSError:pass
    m=read(folder/'manifest.json');verify_frozen(folder,m);repair_reports(folder)
    (folder/'pause').unlink(missing_ok=True)
    atomic(CAMPAIGNS/'active.json',{'id':m['id']})
    signal.signal(signal.SIGTERM,lambda *_:(folder/'pause').touch())
    signal.signal(signal.SIGINT,lambda *_:(folder/'pause').touch())
    batches=0
    try:
        for cid in m['candidates']:
            q=folder/'candidates'/cid/'qualification.json'
            if not q.exists() or read(q).get('rtl_sha256')!=m['candidates'][cid]['rtl_sha256']:
                reason=resource_reason(folder)
                if reason:write_state(folder,'paused',reason=reason);return
                write_state(folder,'qualifying',current_candidate=cid,phase='directed regression')
                r=worker_job(folder,cid,'qualify');mutants=[]
                if r['paused']:write_state(folder,'paused',reason='Qualification paused; it will restart before bulk checks');return
                if not r['failed']:
                    for fault in ['short-wait','msb-first']:
                        if (folder/'pause').exists():
                            write_state(folder,'paused',reason='Qualification paused before mutation; it will restart on resume');return
                        write_state(folder,'qualifying',current_candidate=cid,phase='mutation '+fault)
                        mutant=worker_job(folder,cid,'mutation',fault=fault)
                        mutants.append(dict(fault=fault,detected=bool(mutant['failed']),report=mutant))
                passed=not r['failed'] and len(mutants)==2 and all(x['detected'] for x in mutants)
                atomic(q,dict(status='passed' if passed else 'failed',rtl_sha256=m['candidates'][cid]['rtl_sha256'],report=r,mutations=mutants))
            if (folder/'pause').exists():write_state(folder,'paused',reason='Pause requested');return
        for stage,budget in m['stage_budgets'].items():
            while True:
                t=totals(folder);counts=t['by_stage'].get(stage,{})
                if sum(counts.values())>=budget:break
                reason=resource_reason(folder)
                if reason or (folder/'pause').exists():write_state(folder,'paused',reason=reason or 'Pause requested',stage=stage);return
                pool=[c for c,cfg in m['candidates'].items() if qualified(folder,c,cfg) and not t['by_candidate'].get(c,{}).get('failed')]
                if not pool:write_state(folder,'stopped',reason='No correct candidates remain');return
                ranking=[];basis='unranked'
                if stage in ('stress','holdout'):
                    for cid in pool:
                        if (folder/'pause').exists():write_state(folder,'paused',reason='Pause requested between hardware jobs');return
                        hardware_file=folder/'candidates'/cid/'hardware.json'
                        if not hardware_file.exists() or read(hardware_file).get('physical',{}).get('status')=='interrupted':
                            write_state(folder,'measuring',current_candidate=cid,stage=stage)
                            atomic(folder/'candidates'/cid/'hardware.json',measure(ROOT,folder,cid))
                    ranking,basis=rank(folder,pool);pool=ranking[:2]
                    if not pool:write_state(folder,'paused',reason='No valid mapped area; hardware measurement needs attention');return
                if (folder/'pause').exists():write_state(folder,'paused',reason='Pause requested before next batch');return
                quotas=allocation(pool,budget,counts)
                available=[c for c in pool if counts.get(c,0)<quotas[c]]
                cid=min(available,key=lambda c:(counts.get(c,0),c));start=counts.get(cid,0)
                count=min(m['batch_limit'],quotas[cid]-start)
                write_state(folder,'running',stage=stage,current_candidate=cid,current_start=start,current_count=count,ranking=ranking,ranking_basis=basis)
                r=worker_job(folder,cid,'batch',stage,start,count)
                if not 0<r['checked']<=count or r['rtl_sha256']!=m['candidates'][cid]['rtl_sha256']:
                    raise RuntimeError('Batch result does not match its frozen job')
                r.update(campaign_revision=m['revision'],candidate=cid,stage=stage,start=start)
                commit(folder,stage,cid,start,r);batches+=1
                if r['paused'] or (max_batches is not None and batches>=max_batches):
                    write_state(folder,'paused',reason='Pause requested' if r['paused'] else 'Requested batch limit reached',stage=stage);return
        t=totals(folder);pool=[c for c,cfg in m['candidates'].items() if qualified(folder,c,cfg) and not t['by_candidate'].get(c,{}).get('failed')]
        ranking,basis=rank(folder,pool)
        write_state(folder,'completed' if basis=='physical' else 'behavioral-complete',ranking=ranking,ranking_basis=basis,
                    winner=ranking[0] if ranking and basis=='physical' else None,provisional_leader=ranking[0] if ranking else None)
    except InterruptedError as exc:
        write_state(folder,'paused',reason=str(exc))
    except Exception as exc:
        write_state(folder,'infrastructure-error',reason=str(exc));raise
    finally:fcntl.flock(lock,fcntl.LOCK_UN);lock.close();_LOCK_FD=None

def public_state(folder):
    m=read(folder/'manifest.json');state=read(folder/'state.json')
    state['kind']=m['kind'];state['stages']=m['stage_budgets'];state['candidates']={}
    for cid,cfg in m['candidates'].items():
        state['candidates'][cid]=dict(cfg,qualification=read(folder/'candidates'/cid/'qualification.json').get('status','pending'),
                                    hardware=read(folder/'candidates'/cid/'hardware.json'))
    if state.get('status') in ('running','qualifying','measuring'):
        state['in_flight']=read(folder/'work/progress.json')
        with (CAMPAIGNS/'worker.lock').open('a+') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                state.update(status='interrupted',reason='Worker is no longer running; resume from the committed ledger')
            except BlockingIOError:pass
    state['source_changed']=any(not (ROOT/f).exists() or sha(ROOT/f)!=h for f,h in m['input_hashes'].items())
    # The ledger survives a crash between a DB commit and the public JSON refresh.
    if state.get('status')=='interrupted':state.update(totals(folder))
    return state
def launch(folder):
    # Worker lock is authoritative even across dashboard/server restarts.
    with (CAMPAIGNS/'worker.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('A campaign is already running')
    with (folder/'controller.log').open('a') as log:
        p=subprocess.Popen([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/campaign.py'),'run',folder.name],
                           cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    return p.pid
def main():
    global _LOCK_FD
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('init');q.add_argument('--smoke',action='store_true');q.add_argument('--seed',type=int,default=20261003)
    for cmd in ['run','start','pause','status','hardware']:
        q=sub.add_parser(cmd);q.add_argument('id');
        if cmd=='run':q.add_argument('--max-batches',type=int)
    q=sub.add_parser('worker');q.add_argument('job',type=Path)
    q=sub.add_parser('replay');q.add_argument('id');q.add_argument('candidate',choices=list(CANDIDATES));q.add_argument('stage',choices=list(STAGES));q.add_argument('case',type=int);q.add_argument('--waves',action='store_true')
    a=p.parse_args()
    if a.command=='init':print(init(a.smoke,a.seed).name);return
    if a.command=='worker':worker(a.job);return
    folder=safe_dir(a.id)
    if a.command in ('run','hardware','replay') and Path(__file__).resolve()!=folder/'frozen/scripts/campaign.py':
        verify_frozen(folder,read(folder/'manifest.json'))
        env=os.environ.copy();env['CW_PROJECT_ROOT']=str(ROOT)
        os.execve(sys.executable,[sys.executable,str(folder/'frozen/scripts/campaign.py')]+sys.argv[1:],env)
    if a.command=='run':run(folder,a.max_batches)
    elif a.command=='start':print(launch(folder))
    elif a.command=='pause':(folder/'pause').touch();print('Pause requested; completes the current scenario.')
    elif a.command=='status':print(json.dumps(public_state(folder),indent=2))
    elif a.command=='hardware':
        from campaign_hardware import measure
        lock=(CAMPAIGNS/'worker.lock').open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        _LOCK_FD=lock.fileno()
        for cid in read(folder/'manifest.json')['candidates']:atomic(folder/'candidates'/cid/'hardware.json',measure(ROOT,folder,cid))
        lock.close()
    elif a.command=='replay':
        if a.case<0:raise ValueError('Case must be nonnegative')
        lock=(CAMPAIGNS/'worker.lock').open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        _LOCK_FD=lock.fileno()
        r=worker_job(folder,a.candidate,'batch',a.stage,a.case,1,waves=a.waves)
        dest=folder/'replays'/f'{a.stage}-{a.candidate}-{a.case}';dest.mkdir(parents=True,exist_ok=True)
        atomic(dest/'report.json',r)
        if (folder/'work/tb.fst').exists():shutil.move(str(folder/'work/tb.fst'),str(dest/'tb.fst'))
        print(dest);lock.close()

if __name__=='__main__':main()
