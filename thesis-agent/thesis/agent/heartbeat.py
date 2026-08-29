from __future__ import annotations

import json

from thesis.config import load_settings
from thesis.cycle import run_cycle


def main() -> None:
    settings = load_settings()
    result = run_cycle(settings)
    print(
        json.dumps(
            {
                "at": result.at,
                "decision": result.decision,
                "tool_path": result.tool_path,
                "thesis_id": result.thesis.id,
                "conviction": result.thesis.conviction,
                "underlying": result.thesis.underlying,
                "order_id": result.thesis.order_id,
                "order_status": result.thesis.order_status,
                "monitoring": (
                    result.thesis.monitoring.model_dump(mode="json")
                    if result.thesis.monitoring
                    else None
                ),
                "performance": (
                    result.performance.model_dump(mode="json")
                    if result.performance
                    else None
                ),
                "gates": result.gates,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
