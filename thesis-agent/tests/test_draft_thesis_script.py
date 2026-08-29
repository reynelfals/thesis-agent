from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from thesis.models import Side, Thesis


def _load_script():
    path = Path(__file__).parents[1] / "scripts" / "draft_thesis.py"
    spec = importlib.util.spec_from_file_location("draft_thesis_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_draft_command_uses_guarded_cycle_with_execution_disabled(
    monkeypatch,
    capsys,
) -> None:
    module = _load_script()
    settings = SimpleNamespace(grok_model="grok-test")
    thesis = Thesis(
        underlying="SPY",
        side=Side.BULLISH,
        regime="uptrend",
        setup="MCP-researched setup",
        invalidation="Close below support",
        horizon="14-45 DTE",
        expected_move_pct=2,
        iv_note="normal",
        conviction=0.7,
    )
    calls = []

    monkeypatch.setattr(module, "load_settings", lambda: settings)

    def run_cycle(value, *, execute):
        calls.append((value, execute))
        return SimpleNamespace(
            thesis=thesis,
            decision="blocked",
            tool_path="mcp",
        )

    monkeypatch.setattr(module, "run_cycle", run_cycle)

    module.main()
    payload = json.loads(capsys.readouterr().out)

    assert calls == [(settings, False)]
    assert payload["underlying"] == "SPY"
    assert payload["decision"] == "blocked"
    assert payload["tool_path"] == "mcp"