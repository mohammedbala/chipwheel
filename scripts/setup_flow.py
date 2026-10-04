#!/usr/bin/env python3
"""Set up official support tools locally; optional full pinned PDK download."""
import argparse
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SUPPORT = 'd66cf179e7bc4d296362ab7e2e3b344dc3c4f665'
ACTION = '3412659307918422f3f0727917cf9b499aaca588'
PDK = '2bbec755dc67ca3db0261c3d6163e15735d66710'


def call(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def clone(name, repository, revision):
    target = ROOT / '.tools' / name
    if not target.exists():
        call('git', 'init', str(target))
        call('git', '-C', str(target), 'fetch', '--depth', '1', repository, revision)
        call('git', '-C', str(target), 'checkout', '--detach', 'FETCH_HEAD')
    actual = subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != revision:
        raise SystemExit(f'{target} has revision {actual}; preserve it and resolve manually')


def prepare_models():
    target = ROOT / '.tools/pdk-lib'
    target.mkdir(parents=True, exist_ok=True)
    base = f'https://raw.githubusercontent.com/IHP-GmbH/IHP-Open-PDK/{PDK}/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/'
    for kind, name in [('lib', 'sg13cmos5l_stdcell_typ_1p20V_25C.lib'),
                       ('verilog', 'sg13cmos5l_stdcell.v'), ('verilog', 'sg13cmos5l_udp.v')]:
        path = target / name
        if not path.exists():
            path.write_bytes(urllib.request.urlopen(base + kind + '/' + name).read())
    # Icarus cannot parse this PDK revision's edge-sensitive ifnone paths.
    # Keep the official logic/UDP tables; omit timing and connect delayed inputs
    # directly. This is explicitly zero-delay functional verification only.
    original = (target / 'sg13cmos5l_stdcell.v').read_text()
    functional = re.sub(r'\bspecify\b.*?\bendspecify\b', '// timing omitted: functional verification only', original, flags=re.S)
    functional = re.sub(r'\bwire delayed_[^;]+;', '// use direct inputs below', functional)
    functional = re.sub(r'\bdelayed_(\w+)\b', r'\1', functional)
    functional += '\n' + (target / 'sg13cmos5l_udp.v').read_text()
    (target / 'sg13cmos5l_functional.v').write_text(functional)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--full-pdk', action='store_true')
    a = p.parse_args()
    os.chdir(ROOT)
    (ROOT / '.tools').mkdir(exist_ok=True)
    clone('tt-support-tools', 'https://github.com/TinyTapeout/tt-support-tools.git', SUPPORT)
    clone('tt-gds-action', 'https://github.com/TinyTapeout/tt-gds-action.git', ACTION)
    if not (ROOT / 'tt').exists():
        (ROOT / 'tt').symlink_to('.tools/tt-support-tools', target_is_directory=True)
    env = ROOT / '.tools/flow-venv'
    if not (env / 'bin/python').exists():
        if shutil.disk_usage(ROOT).free < 2 * 1024**3:
            raise SystemExit('Flow setup needs at least 2 GiB free for local tools/caches; no unrelated files will be removed.')
        call(sys.executable, '-m', 'pip', 'install', 'uv==0.12.22')
        uv = ROOT / '.venv/bin/uv'
        uv_env = os.environ.copy()
        uv_env.update(UV_CACHE_DIR=str(ROOT / '.tools/uv-cache'), UV_PYTHON_BIN_DIR=str(ROOT / '.tools/bin'))
        call(str(uv), 'python', 'install', '3.11', '--install-dir', str(ROOT / '.tools/python'), env=uv_env)
        python = sorted((ROOT / '.tools/python').glob('cpython-3.11.*/bin/python3'))[-1]
        call(str(uv), 'venv', '--python', str(python), str(env), env=uv_env)
        call(str(uv), 'pip', 'install', '--python', str(env / 'bin/python'), '-r',
             str(ROOT / '.tools/tt-support-tools/requirements.txt'), 'librelane==3.1.0.dev3', env=uv_env)
    prepare_models()
    if a.full_pdk:
        if shutil.disk_usage(ROOT).free < 10 * 1024**3:
            raise SystemExit('Full PDK/container flow requires disk headroom: reserve at least 10 GiB, then retry. No files were deleted.')
        envvars = os.environ.copy()
        envvars['PDK_ROOT'] = str(ROOT / '.tools/pdk')
        call('bash', str(ROOT / '.tools/tt-gds-action/install_sg13cmos5l.sh'), env=envvars)
    print('Local flow tools and pinned synthesis models ready. Full layout additionally requires Docker/Podman and the full PDK.')


if __name__ == '__main__':
    main()
