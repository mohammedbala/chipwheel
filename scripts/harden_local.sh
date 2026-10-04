#!/bin/sh
# Official Tiny Tapeout hardening (LibreLane, dockerized) on this Mac via Colima.
#
#   sh scripts/harden_local.sh           # needs >= 15 GiB free; refuses otherwise
#
# Steps: free-space guard -> start Colima VM (repo mounted writable) -> pull the
# pinned LibreLane image -> tt_tool --create-user-config/--harden --ihp ->
# gate-level replay of the v2 suites on the routed netlist. The PDK must already
# be in .tools/pdk (sparse checkout of IHP-Open-PDK 2bbec755, see docs/hardening.md).
# Delete the VM afterwards with `colima delete -f --data` to recover ~7 GiB.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
NEED_GIB=${NEED_GIB:-15}
IMAGE=ghcr.io/librelane/librelane:3.1.0.dev3

free_gib() { df -g "$ROOT" | awk 'NR==2 {print $4}'; }
guard() {
    f=$(free_gib)
    if [ "$f" -lt "$1" ]; then
        echo "Only ${f} GiB free; need at least $1 GiB ($2). Free space and rerun." >&2
        exit 2
    fi
}

guard "$NEED_GIB" "VM ~1 GiB + image ~5.6 GiB + runs ~1 GiB + 5 GiB reserve"
[ -f .tools/pdk/ihp-sg13cmos5l/SOURCES ] || { echo "PDK missing in .tools/pdk" >&2; exit 2; }

if ! docker info >/dev/null 2>&1; then
    colima start --arch aarch64 --vm-type vz --mount-type virtiofs --cpu 6 --memory 8 --disk 30 \
        --mount "$ROOT:w"
    rm -rf "$HOME/Library/Caches/colima"          # base-image download cache, no longer needed
fi
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE"
guard 3 "keep a small reserve before running the flow"

export PATH="$ROOT/.tools/shim:$ROOT/.tools/flow-venv/bin:$PATH"
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
export PDK_ROOT="$ROOT/.tools/pdk" PDK=ihp-sg13cmos5l
mkdir -p results/hardening
PY="$ROOT/.tools/flow-venv/bin/python"
"$PY" tt/tt_tool.py --create-user-config --ihp
start=$(date +%s)
if "$PY" tt/tt_tool.py --harden --ihp > results/hardening/harden.log 2>&1; then status=passed; else status=failed; fi
echo "harden: $status in $(( $(date +%s) - start )) s (log: results/hardening/harden.log)"
cp runs/wokwi/final/metrics.csv results/hardening/metrics.csv 2>/dev/null || true
"$PY" tt/tt_tool.py --print-stats --ihp > results/hardening/stats.md 2>&1 || true
"$PY" tt/tt_tool.py --print-warnings --ihp > results/hardening/warnings.md 2>&1 || true
[ "$status" = passed ] || exit 1

# Gate-level: replay the v2 directed suite and a random campaign on the routed netlist.
NL=$(ls runs/wokwi/final/nl/*.nl.v 2>/dev/null | head -1)
if [ -n "$NL" ]; then
    cp "$NL" test/gate_level_netlist.v
    (cd v2/test && CW2_GL="$ROOT/test/gate_level_netlist.v" "$ROOT/.venv/bin/python" -m pytest -q test_protocols.py \
        -p no:cacheprovider && "$ROOT/.venv/bin/python" fuzz.py --cases 200 --cycles 4000 --seed 77 \
        --gl "$ROOT/test/gate_level_netlist.v" --out ../results/fuzz_postlayout.json) \
        > results/hardening/gl_replay.log 2>&1 && echo "post-layout replay: passed" || echo "post-layout replay: FAILED"
fi
