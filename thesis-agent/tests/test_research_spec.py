from __future__ import annotations

import hashlib
import json
from pathlib import Path

from thesis.research.spec import (
    StrategySpecError,
    load_strategy_spec,
    strategy_spec_digest,
    verify_runtime_fingerprints,
)
import pytest


def test_frozen_v1_integrity_is_preserved_while_runtime_drift_is_expected() -> None:
    spec = load_strategy_spec()

    assert spec["strategy_id"] == "thesis-recorded-grok-defined-risk-v1"
    assert len(spec["universe"]) == 29
    drift = verify_runtime_fingerprints(spec)
    assert drift
    assert any(error.startswith("thesis/cycle.py: expected") for error in drift)
    assert any(error.startswith("thesis/llm.py: expected") for error in drift)
    assert len(strategy_spec_digest(spec)) == 64
    assert hashlib.sha256(
        Path("research/strategy-spec-v1.json").read_bytes()
    ).hexdigest() == "06cdf0bdc3315062fae949d16982e7bb2fef385a483480e0649efceab38ed8a4"


def test_current_demo_v2_has_distinct_integrity_and_runtime_fingerprints() -> None:
    path = Path("research/current-demo-strategy-v2.json")
    spec = json.loads(path.read_text(encoding="utf-8"))

    assert spec["schema_version"] == "thesis-current-demo-strategy-v2"
    assert spec["status"]["not_part_of_v1_evidence"] is True
    assert spec["status"]["positive_edge_claim"] is False
    assert verify_runtime_fingerprints(spec) == []
    assert strategy_spec_digest(spec) == (
        "cb5ed93f18c501d04c603d3a0965703aac795663c207dfabef661300e6d65e33"
    )
    with pytest.raises(StrategySpecError):
        load_strategy_spec(path)


def test_runtime_fingerprint_detects_drift(tmp_path: Path) -> None:
    spec = {
        "runtime_source_fingerprints": {
            "thesis/scout.py": "0" * 64,
        }
    }
    source = tmp_path / "thesis" / "scout.py"
    source.parent.mkdir(parents=True)
    source.write_text("changed\n", encoding="utf-8")

    drift = verify_runtime_fingerprints(spec, project_root=tmp_path)

    assert len(drift) == 1
    assert drift[0].startswith("thesis/scout.py: expected")


def test_strategy_loader_rejects_unregistered_spec_tampering(tmp_path: Path) -> None:
    spec_path = tmp_path / "strategy.json"
    text = Path("research/strategy-spec-v1.json").read_text(encoding="utf-8")
    spec_path.write_text(
        text.replace('"minimum_filled_trades": 100', '"minimum_filled_trades": 1'),
        encoding="utf-8",
    )

    with pytest.raises(StrategySpecError, match="pre-registered SHA-256"):
        load_strategy_spec(spec_path)