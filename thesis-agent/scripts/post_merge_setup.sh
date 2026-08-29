#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

pip install \
  --disable-pip-version-check \
  --no-input \
  -r requirements.txt

python -m compileall -q thesis-agent/thesis

export PATH="/home/runner/go/bin:$PATH"
bash thesis-agent/scripts/install_alpaca_cli.sh
alpaca version