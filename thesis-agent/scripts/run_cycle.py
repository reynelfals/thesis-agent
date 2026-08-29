"""One autonomous cycle. Does not place orders unless THESIS_ALLOW_EXECUTE=1 and market is open."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thesis.config import load_settings
from thesis.cycle import run_cycle


def main() -> None:
    result = run_cycle(load_settings())
    out = {
        "at": result.at,
        "decision": result.decision,
        "tool_path": result.tool_path,
        "thesis_id": result.thesis.id,
        "underlying": result.thesis.underlying,
        "side": result.thesis.side,
        "conviction": result.thesis.conviction,
        "setup": result.thesis.setup,
        "invalidation": result.thesis.invalidation,
        "order_id": result.thesis.order_id,
        "order_status": result.thesis.order_status,
        "monitoring": (
            result.thesis.monitoring.model_dump(mode="json")
            if result.thesis.monitoring
            else None
        ),
        "structure": result.thesis.structure.model_dump() if result.thesis.structure else None,
        "performance": (
            result.performance.model_dump(mode="json") if result.performance else None
        ),
        "gates": result.gates,
        "traces": [
            {
                "tool": t.get("tool"),
                "step": t.get("step"),
                "ok": t.get("ok"),
                "returncode": t.get("returncode"),
                "status": t.get("status"),
            }
            for t in result.traces
        ],
    }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
