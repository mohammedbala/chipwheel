#!/bin/sh
# No administrator installations. On this Mac the pre-existing Homebrew only
# downloads a bottle, which is extracted under this project.
set -eu
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install -r test/requirements.txt
if command -v iverilog >/dev/null 2>&1; then
    iverilog -V
elif [ -x .tools/icarus-verilog/13.0/bin/iverilog ]; then
    .tools/icarus-verilog/13.0/bin/iverilog -V
elif [ "$(uname -m)" = arm64 ] && [ "$(uname -s)" = Darwin ] && [ -x /opt/homebrew/bin/brew ]; then
    mkdir -p .tools
    /opt/homebrew/bin/brew fetch icarus-verilog
    archive=$(/opt/homebrew/bin/brew --cache icarus-verilog)
    tar -xzf "$archive" -C .tools
    # The measured setup used version 13.0. A changed bottle needs review.
    test -x .tools/icarus-verilog/13.0/bin/iverilog
else
    echo 'Icarus Verilog is required. Install it with your preferred method, then rerun setup.' >&2
    exit 1
fi
