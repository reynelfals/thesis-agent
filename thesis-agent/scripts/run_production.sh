#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR/.."

export PATH="/home/runner/go/bin:$PATH"
export PYTHONPATH=.
export PYTHONUNBUFFERED=1

worker_pid=""
web_pid=""

shutdown() {
    status=$?
    trap - EXIT HUP INT TERM
    set +e
    for pid in "$worker_pid" "$web_pid"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null
        fi
    done
    for pid in "$worker_pid" "$web_pid"; do
        if [ -n "$pid" ]; then
            wait "$pid" 2>/dev/null
        fi
    done
    exit "$status"
}

trap shutdown EXIT HUP INT TERM

THESIS_ACCOUNT_PROFILE=judge \
THESIS_DB=data/judge-thesis.sqlite \
THESIS_ALLOW_EXECUTE=0 \
python scripts/run_recurring_analysis.py &
worker_pid=$!

THESIS_ACCOUNT_PROFILE=judge \
THESIS_DB=data/judge-thesis.sqlite \
THESIS_ALLOW_EXECUTE=0 \
uvicorn thesis.web.app:app \
    --host 0.0.0.0 \
    --port "${PORT:-5000}" &
web_pid=$!

while kill -0 "$worker_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; do
    sleep 1
done

status=0
if ! kill -0 "$worker_pid" 2>/dev/null; then
    wait "$worker_pid" || status=$?
else
    wait "$web_pid" || status=$?
fi
exit "$status"
