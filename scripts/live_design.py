"""Read-only project telemetry for the bench. Never runs a verification job."""
import hashlib
import json
import re
from pathlib import Path

MODEL_RTL = 'fa5ee7e7005e764dd658916f07065339fe05d089ff615dd552a46cdd6cb9f15c'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

def snapshot(root):
    def read(path):
        try:
            return json.loads((root / path).read_text())
        except (OSError, ValueError):
            return {}
    rtl = root / 'src/project.v'
    text = rtl.read_text() if rtl.exists() else ''
    clean = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)
    sha = digest(rtl)
    config_sha = digest(root / 'src/config.json')
    cfg = read('src/config.json')
    memory = re.search(r'reg\s*\[(\d+):0\]\s*program_mem\s*\[0:(\d+)\]', clean)
    def width(name):
        m = re.search(r'reg\s*\[(\d+):0\]\s*[^;]*\b'+name+r'\b', clean)
        return int(m[1])+1 if m else None
    architecture = dict(words=int(memory[2])+1 if memory else None,
                        word_bits=int(memory[1])+1 if memory else None,
                        pc_bits=width('pc'), wait_bits=width('wait_left'),
                        data_bits=width('data_shift'), loop_bits=width('loop_count'))
    stages = []
    records = []
    for p in (root / 'results').glob('*.json'):
        d = read('results/'+p.name)
        if 'checked_transactions' in d and 'rtl_sha256' in d and not any(x in p.name for x in ('msb-first','short-wait')):
            records.append((p.stat().st_mtime_ns, p.name, d))
    def stage(id, title, record, current, detail):
        file, d = record if record else (None, {})
        state = ('passed' if d.get('status')=='passed' else 'blocked' if id=='physical' else 'failed') if d else 'pending'
        stages.append(dict(id=id, title=title, status=state, current=bool(current) if d else False,
                           file=file, detail=detail, hash=d.get('rtl_sha256')))
    def latest(mapped=False, mode='short'):
        candidates = [r for r in records if bool(r[2].get('cell_model_sha256'))==mapped and r[2].get('mode')==mode]
        if not candidates:
            return None
        r = max(candidates)
        return r[1], r[2]
    for id, title, mode in [('rtl','RTL correctness','short'),('campaign','Random campaign','campaign')]:
        rec = latest(mode=mode)
        d = rec[1] if rec else {}
        stage(id,title,rec,d.get('rtl_sha256')==sha,
              f"{d.get('checked_transactions',0):,} checked transactions · {d.get('failures',0)} failures" if d else 'No run recorded')
    synth = read('results/synth-flow.json')
    synth_current = synth.get('rtl_sha256')==sha and synth.get('config_sha256')==config_sha
    stage('mapping','Cell mapping',('synth-flow.json',synth) if synth else None,synth_current,
          f"{synth.get('mapped_cells',0):,} cells · {synth.get('area_um2',0) or 0:,.3f} µm²" if synth.get('status')=='passed' else synth.get('note','No mapping recorded'))
    rec = latest(mapped=True)
    stage('mapped','Mapped behavior',rec,synth_current and bool(rec) and rec[1].get('rtl_sha256')==digest(root / 'results/mapped-netlist.v'),
          f"{rec[1].get('checked_transactions',0):,} checked · functional only" if rec else 'No mapped regression recorded')
    physical = read('results/layout-flow.json')
    stage('physical','Physical build',('layout-flow.json',physical) if physical else None,
          physical.get('rtl_sha256')==sha and physical.get('config_sha256')==config_sha,
          physical.get('blocker') or physical.get('note','No physical build recorded'))
    tracked = [rtl, root/'src/config.json', root/'info.yaml', root/'docs/spec.md', root/'programs/encode.py',root/'simulator/engine.js']
    tracked += [root/'results'/s['file'] for s in stages if s['file']]
    tracked += [root/'results/mutations.json', root/'results/mapped-stat.json', root/'results/mapped-netlist.v']
    files = {str(p.relative_to(root)): digest(p) for p in tracked}
    info = (root/'info.yaml').read_text() if (root/'info.yaml').exists() else ''
    tile = re.search(r'^\s+tiles:\s*"?([\dx]+)',info,re.M)
    module = re.search(r'\bmodule\s+(\w+)',clean)
    pin = re.search(r'assign\s+uo_out\s*=\s*([^;]+)',clean)
    opcodes = re.findall(r'^\s*(\d+)\s*:',clean,re.M)
    data = dict(rtl_sha256=sha, config_sha256=config_sha, files=files,
                module=module[1] if module else 'Unknown module', architecture=architecture,
                clock_period_ns=cfg.get('CLOCK_PERIOD'), tiles=tile[1] if tile else None,
                output_assignment=pin[1].strip() if pin else 'Unknown', opcode_count=len(set(opcodes)),
                model_matches=sha==MODEL_RTL, stages=stages,
                routing_status=physical.get('routing_status','unverified'), timing_slack_ns=physical.get('timing_slack_ns'))
    data['revision'] = hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    return data
