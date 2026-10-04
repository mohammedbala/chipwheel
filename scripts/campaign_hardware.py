"""Isolated, pinned hardware measurements; unknown physical metrics stay null."""
import csv
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import xml.etree.ElementTree as ET

def execute(command,cwd,env,log,timeout,folder):
    from campaign import lock_fds,resource_reason
    proc=subprocess.Popen(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,pass_fds=lock_fds())
    began=time.monotonic()
    while proc.poll() is None:
        reason=resource_reason(folder)
        paused=(folder/'pause').exists()
        if paused or reason or time.monotonic()-began>timeout:
            os.killpg(proc.pid,signal.SIGTERM)
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            if paused or reason:
                (folder/'pause').touch();raise InterruptedError(reason or 'Hardware job paused; it will restart on resume')
            raise subprocess.TimeoutExpired(command,timeout)
        time.sleep(.5)
    return proc.returncode

def mapping(root,folder,cid):
    from campaign import sha
    candidate=folder/'candidates'/cid
    lib=folder/'frozen/.tools/pdk-lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib'
    script=candidate/'synth.ys'
    q=lambda path:'"'+str(path)+'"'
    script.write_text('\n'.join([
        f'read_liberty -lib {q(lib)}',f'read_verilog {q(candidate/"project.v")}',
        'hierarchy -check -top tt_um_chipwheel','synth -top tt_um_chipwheel',
        f'dfflibmap -liberty {q(lib)}',f'abc -liberty {q(lib)}','clean','check -assert',
        f'tee -o {q(candidate/"mapped-stat.json")} stat -json -liberty {q(lib)}',
        f'write_verilog -noattr {q(candidate/"mapped-netlist.v")}'])+'\n')
    env=os.environ.copy();env.update(YOWASP_CACHE_DIR=str(root/'.tools/yowasp-cache'),DYLD_FALLBACK_LIBRARY_PATH='/opt/homebrew/lib')
    began=time.monotonic()
    with (candidate/'synthesis.log').open('w') as log:
        code=execute([str(root/'.tools/flow-venv/bin/yowasp-yosys'),'-s',str(script)],root,env,log,600,folder)
    result={'status':'failed','area_um2':None,'cells':None,'seconds':time.monotonic()-began,
            'rtl_sha256':sha(candidate/'project.v'),'config_sha256':sha(folder/'frozen/src/config.json'),
            'library_sha256':sha(lib),'returncode':code}
    if code==0:
        stat=json.loads((candidate/'mapped-stat.json').read_text())['design']
        area=stat.get('area')
        unmapped=[name for name in stat.get('num_cells_by_type',{}) if not name.startswith('sg13cmos5l_')]
        result['unmapped_cells']=unmapped
        if isinstance(area,(int,float)) and math.isfinite(area) and area>0 and not unmapped:
            result.update(status='passed',area_um2=area,cells=stat['num_cells'],netlist_sha256=sha(candidate/'mapped-netlist.v'))
    return result

def prerequisites(root):
    reasons=[]
    if shutil.disk_usage(root).free<10*1024**3:reasons.append('Official-flow setup needs at least 10 GiB free; the campaign retains a 5 GiB reserve')
    if not (root/'.tools/pdk/ihp-sg13cmos5l/SOURCES').exists():reasons.append('Full pinned CMOS5L PDK is not installed')
    runtime=False
    for executable in ['docker','podman']:
        if shutil.which(executable):
            try:runtime=subprocess.run([executable,'info'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10).returncode==0
            except subprocess.TimeoutExpired:pass
            if runtime:break
    if not runtime:reasons.append('No working Docker/Podman container engine')
    if not shutil.which('nix-shell'):reasons.append('Official precheck Nix environment is unavailable')
    return reasons

def physical(root,folder,cid):
    from campaign import sha
    m=json.loads((folder/'manifest.json').read_text())
    result={'status':'blocked','routing':'unverified','precheck':'unverified','gate_regression':'unverified',
            'area_um2':None,'setup_slack_ns':None,'hold_slack_ns':None,'blockers':prerequisites(root),
            'rtl_sha256':sha(folder/'candidates'/cid/'project.v'),'config_sha256':sha(folder/'frozen/src/config.json'),
            'pdk_revision':m['pdk_revision']}
    if result['blockers']:return result
    for path,key in [('.tools/pdk','pdk_revision'),('.tools/tt-support-tools','support_revision'),('.tools/tt-gds-action','action_revision')]:
        actual=subprocess.check_output(['git','-C',str(root/path),'rev-parse','HEAD'],text=True).strip()
        if actual!=m[key]:result['blockers'].append('Pinned revision mismatch: '+path)
    if result['blockers']:return result
    work=folder/'candidates'/cid/'physical';work.mkdir(exist_ok=True)
    (work/'src').mkdir(exist_ok=True)
    shutil.copyfile(folder/'candidates'/cid/'project.v',work/'src/project.v')
    shutil.copyfile(folder/'frozen/src/config.json',work/'src/config.json')
    shutil.copyfile(folder/'frozen/info.yaml',work/'info.yaml')
    if not (work/'tt').exists():(work/'tt').symlink_to(root/'.tools/tt-support-tools',target_is_directory=True)
    env=os.environ.copy();env.update(PATH=str(root/'.tools/flow-venv/bin')+os.pathsep+env['PATH'],PDK_ROOT=str(root/'.tools/pdk'))
    commands=[[str(root/'.tools/flow-venv/bin/python'),'tt/tt_tool.py','--create-user-config','--ihp'],
              [str(root/'.tools/flow-venv/bin/python'),'tt/tt_tool.py','--harden','--ihp'],
              [str(root/'.tools/flow-venv/bin/python'),'tt/tt_tool.py','--create-tt-submission','--ihp']]
    started=time.monotonic()
    try:
        with (work/'flow.log').open('w') as log:
            for cmd in commands:
                code=execute(cmd,work,env,log,10800,folder)
                if code:raise subprocess.CalledProcessError(code,cmd)
        metrics=dict(csv.reader((work/'runs/wokwi/final/metrics.csv').open()))
        def metric(key):
            try:
                v=float(metrics[key]);return v if math.isfinite(v) else None
            except (KeyError,ValueError):return None
        result.update(routing='passed',area_um2=metric('design__instance__area'),setup_slack_ns=metric('timing__setup__ws'),hold_slack_ns=metric('timing__hold__ws'))
        pre=work/'precheck'
        if not pre.exists():shutil.copytree(root/'.tools/tt-support-tools/precheck',pre)
        gds=list((work/'tt_submission').glob('*.gds*'))
        if len(gds)!=1:raise RuntimeError('Expected exactly one GDS artifact')
        # shlex quoting here is for nix-shell's documented --run shell interface.
        import shlex
        with (work/'precheck.log').open('w') as log:
            cmd=['nix-shell','--run',f'python precheck.py --gds {shlex.quote(str(gds[0]))} --tech ihp-sg13cmos5l']
            code=execute(cmd,pre,env,log,3600,folder)
            if code:raise subprocess.CalledProcessError(code,cmd)
        xml=ET.parse(pre/'reports/results.xml')
        if not xml.findall('.//testcase') or xml.findall('.//failure') or xml.findall('.//error'):raise RuntimeError('Official precheck did not pass')
        result['precheck']='passed'
        # The official post-layout models must compile and pass the same candidate suite.
        from campaign import worker_job,atomic
        netlists=list((work/'tt_submission').glob('*.v'))
        if len(netlists)!=1:raise RuntimeError('Expected exactly one post-layout netlist')
        modeldir=root/'.tools/pdk/ihp-sg13cmos5l/libs.ref'
        models=[modeldir/'sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v',modeldir/'sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v',modeldir/'sg13cmos5l_io/verilog/sg13cmos5l_io.v']
        gate=worker_job(folder,cid,'qualify',source_override=netlists[0],models=models)
        atomic(work/'gate-regression.json',gate)
        if gate.get('paused'):raise InterruptedError('Post-layout qualification paused; it must finish before physical eligibility')
        if gate['failed']:raise RuntimeError('Post-layout regression failed')
        result.update(gate_regression='passed',status='passed')
    except InterruptedError as exc:
        result.update(status='interrupted',blockers=[str(exc)])
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        result.update(status='failed',blockers=[str(exc)])
    result['seconds']=time.monotonic()-started
    return result

def measure(root,folder,cid):
    mapped=mapping(root,folder,cid)
    if (folder/'pause').exists():raise InterruptedError('Pause requested after cell mapping')
    return {'mapping':mapped,'physical':physical(root,folder,cid)}
