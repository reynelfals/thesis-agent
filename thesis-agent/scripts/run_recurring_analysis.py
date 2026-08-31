"""Run the durable, execution-disabled market-hours analysis worker."""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path
from threading import Event
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thesis.recurring import run_worker


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True), flush=True)


def main() -> int:
    stop_requested = Event()

    def request_stop(_signum: int, _frame: Any) -> None:
        stop_requested.set()

    handled_signals = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    previous_handlers = {
        signum: signal.signal(signum, request_stop) for signum in handled_signals
    }
    try:
        result = run_worker(
            emit=_emit,
            sleep=stop_requested.wait,
            should_stop=stop_requested.is_set,
        )
    except Exception:
        result = {
            "event": "recurring_market_analysis",
            "state": "not_armed",
            "outcome": "preflight_failed",
            "order_submitted": False,
            "execute": False,
        }
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    _emit(result)
    return 1 if result["state"] in {"failed", "not_armed"} else 0


if __name__ == "__main__":
    raise SystemExit(main())