#!/bin/zsh

set -euo pipefail

source /Users/linwang/.venv/bin/activate
export OPIK_BASE_URL="${OPIK_BASE_URL:-http://opik-nexa-01.us-east4.qe.gcp.conviva.com:5173/api}"
python /Users/linwang/codex-test/export_opik_logs.py
