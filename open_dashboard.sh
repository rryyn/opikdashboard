#!/bin/zsh

set -euo pipefail

/Users/linwang/.venv/bin/python /Users/linwang/codex-test/generate_analysis_dashboard.py
open /Users/linwang/codex-test/analysis_dashboard.html
