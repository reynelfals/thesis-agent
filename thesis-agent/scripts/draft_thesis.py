"""Draft a grok-4.6 thesis from paper market data. Does not place orders."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thesis.alpaca.client import PaperClient
from thesis.config import load_settings
from thesis.llm import draft_thesis
from thesis.observe import universe
from thesis.store import ThesisStore


def main() -> None:
    settings = load_settings()
    client = PaperClient(settings)
    snaps = universe(client)
    thesis = draft_thesis(settings, snaps)
    ThesisStore(settings.db_path).upsert(thesis)
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
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
