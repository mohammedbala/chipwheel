#!/bin/sh
# Wrapper for the repo's YoWASP Yosys (same binary as .tools/flow-venv/bin/yowasp-yosys).
#
# YoWASP preopens every directory under "/" for its WASI sandbox. On this Mac,
# opening /home (an autofs mount) blocks forever, so plain yowasp-yosys hangs at
# 0% CPU before printing anything. This wrapper hides /home (and /net) from that
# scan and otherwise behaves exactly like yowasp-yosys, so relative paths work.
# Alternative: YOWASP_MOUNT=/Users=/Users:/private=/private with absolute paths.
ROOT=$(cd "$(dirname "$0")/.." && pwd)
export YOWASP_CACHE_DIR="${YOWASP_CACHE_DIR:-$ROOT/.tools/yowasp-cache}"
exec "$ROOT/.tools/flow-venv/bin/python" -c '
import os, sys
_listdir = os.listdir
def _safe_listdir(p="."):
    names = _listdir(p)
    if os.path.abspath(p) == "/":
        names = [n for n in names if n not in ("home", "net")]
    return names
os.listdir = _safe_listdir
sys.argv[0] = "yowasp-yosys"
from yowasp_yosys import _run_yosys_argv
sys.exit(_run_yosys_argv())
' "$@"
