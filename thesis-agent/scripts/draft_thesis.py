"""Draft a guarded MCP thesis. Execution is always disabled."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thesis.config import load_settings
from thesis.cycle import run_cycle


def main() -> None:
    settings = load_settings()
    result = run_cycle(settings, execute=False)
    thesis = result.thesis
    print(
        json.dumps(
            {
                "id": thesis.id,
                "underlying": thesis.underlying,
                "side": thesis.side,
                "regime": thesis.regime,
                "conviction": thesis.conviction,
                "setup": thesis.setup,
                "invalidation": thesis.invalidation,
                "horizon": thesis.horizon,
                "iv_note": thesis.iv_note,
                "model": settings.grok_model,
                "decision": result.decision,
                "tool_path": result.tool_path,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
