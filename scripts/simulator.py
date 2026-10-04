#!/usr/bin/env python3
"""Serve the local experiment bench; refresh measured evidence on each request."""
import argparse
import json
import subprocess
from urllib.parse import urlparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from live_design import snapshot
import campaign

ROOT = Path(__file__).resolve().parents[1]

def evidence():
    rows = []
    for name, file in [('RTL regression', 'final-short.json'), ('Random campaign', 'campaign.json'), ('Mapped functional regression', 'final-mapped-short.json')]:
        p = ROOT / 'results' / file
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        rows.append(dict(name=name, file=file, status=d['status'], detail=f"{d['checked_transactions']:,} checked · {d['simulated_clock_cycles']:,} clocks · {d['simulation_wall_seconds']:.2f} s · {d['failures']} failures"))
    p = ROOT / 'results/mutations.json'
    if p.exists():
        d = json.loads(p.read_text())
        for f in d['faults']:
            rows.append(dict(name=f"Deliberate fault: {f['fault']}", file=f['evidence'], status='detected' if f['detected'] else 'not detected', detail='Independent RTL checker detected the injected fault.' if f['detected'] else 'Fault escaped the checker.'))
    p = ROOT / 'results/mapped-stat.json'
    if p.exists():
        d = json.loads(p.read_text())['design']
        rows.append(dict(name='CMOS5L mapping', file=p.name, status='completed', detail=f"{d['num_cells']:,} cells · {d['area']:,.3f} µm² cell area · standalone synthesis, no routing"))
    p = ROOT / 'results/layout-flow.json'
    if p.exists():
        d = json.loads(p.read_text())
        rows.append(dict(name='Official physical build', file=p.name, status='blocked' if d['status']=='failed' else d['status'], detail=d.get('blocker') or d.get('note', 'Physical timing unverified.')))
    return ('window.CHIPWHEEL_EVIDENCE = '+json.dumps(rows)+';\n').encode()

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/simulator/campaign.json':
            try:
                active=campaign.read(campaign.CAMPAIGNS/'active.json').get('id')
                data=campaign.public_state(campaign.safe_dir(active)) if active else {'status':'not-started','target':10000000}
                if active:
                    audit=campaign.read(campaign.safe_dir(active)/'ledger-audit.json')
                    if audit.get('revision')==data.get('revision'):
                        data['ledger_audit']=audit
                self.json_response(data)
            except (OSError, ValueError) as exc:self.json_response({'error':str(exc)},503)
            return
        if path in ('/simulator/evidence.js', '/simulator/live.json'):
            try:
                if path.endswith('.js'):
                    body = evidence()
                else:
                    design = snapshot(ROOT)
                    rows = [dict(name=s['title'],file=s['file'],status=s['status'] if s['current'] else 'historical · '+s['status'],detail=s['detail']) for s in design['stages'] if s['file']]
                    try:
                        mutations = json.loads((ROOT / 'results/mutations.json').read_text())
                        for fault in mutations['faults']:
                            current = mutations['correct_rtl_sha256'] == design['rtl_sha256']
                            rows.append(dict(name='Deliberate fault: '+fault['fault'], file=fault['evidence'], status=('' if current else 'historical · ')+('detected' if fault['detected'] else 'not detected'), detail='Independent RTL mutation check'))
                    except (OSError, ValueError, KeyError):
                        pass
                    body = json.dumps(dict(design=design, evidence=rows)).encode()
            except (OSError, ValueError, KeyError):
                self.send_error(503, 'Project files are being updated; retry shortly')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript; charset=utf-8' if path.endswith('.js') else 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def json_response(self,data,status=200):
        body=json.dumps(data).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

    def do_POST(self):
        if self.path!='/simulator/campaign/action':self.send_error(404);return
        host=self.headers.get('Host','')
        allowed={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        if host not in allowed or self.headers.get('X-Campaign-Control')!='1' or self.headers.get('Origin',f'http://{host}')!=f'http://{host}':
            self.json_response({'error':'Use the local experiment bench to control campaigns'},403);return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=4096:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict):raise ValueError('Expected a control request object')
            action=data.get('action')
            if action=='start':
                folder=campaign.init();pid=campaign.launch(folder);self.json_response({'id':folder.name,'pid':pid});return
            folder=campaign.safe_dir(data.get('id'))
            if action=='pause':(folder/'pause').touch()
            elif action=='resume':campaign.launch(folder)
            elif action=='replay':
                cid=data.get('candidate');stage=data.get('stage');case=data.get('case_id')
                if cid not in campaign.CANDIDATES or stage not in campaign.STAGES or type(case) is not int or case<0:raise ValueError('Invalid replay')
                if campaign.public_state(folder).get('status') in ('running','qualifying','measuring'):raise ValueError('Pause the campaign before replaying a case')
                with (folder/'replay.log').open('a') as log:
                    subprocess.Popen([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/campaign.py'),'replay',folder.name,cid,stage,str(case),'--waves'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            else:raise ValueError('Unknown action')
            self.json_response({'ok':True})
        except (OSError,ValueError,RuntimeError) as exc:self.json_response({'error':str(exc)},409)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--snapshot', action='store_true', help='Refresh evidence for opening index.html directly')
    args = parser.parse_args()
    (ROOT / 'simulator/evidence.js').write_bytes(evidence())
    if not args.snapshot:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
        print(f'Experiment bench: http://127.0.0.1:{args.port}/simulator/', flush=True)
        server.serve_forever()
