"""Wait for and run the authorized Monday paper cycle exactly once."""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path
from threading import Event
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thesis.scheduled import wait_and_run


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
        result = wait_and_run(
            emit=_emit,
            sleep=stop_requested.wait,
            should_stop=stop_requested.is_set,
        )
    except Exception:
        result = {
            "event": "monday_paper_cycle",
            "state": "not_armed",
            "outcome": "preflight_failed",
        }
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    _emit(result)
    return 1 if result["state"] in {"failed", "not_armed"} else 0


if __name__ == "__main__":
    raise SystemExit(main())