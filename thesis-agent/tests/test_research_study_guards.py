from __future__ import annotations

import hashlib
import json
from datetime import datetime

from thesis.research.spec import load_strategy_spec, strategy_spec_digest
from thesis.research.study import (
    _canonical_value_sha256,
    _derived_benchmark_curves,
    _derived_strategy_curve,
    _grok_record_errors,
    _manifest_errors,
    canonical_dataset_digest,
    _curve_integrity_errors,
    _partition_for_event,
    _replication_criterion,
)


def _metadata() -> dict:
    return {
        "initial_equity": 100_000,
        "walk_forward_folds": [
            {
                "fold_id": "fold-1",
                "train_start": "2024-01-01T00:00:00Z",
                "train_end": "2024-06-30T23:00:00Z",
                "test_start": "2024-07-01T00:00:00Z",
                "test_end": "2024-12-31T23:00:00Z",
            }
        ],
        "untouched_holdout": {
            "sealed_at": "2024-12-31T23:00:00Z",
            "start": "2025-01-01T00:00:00Z",
            "end": "2026-02-01T00:00:00Z",
        },
    }


def test_event_cannot_label_training_period_as_walk_forward_test() -> None:
    partition, error = _partition_for_event(
        {
            "signal_at": "2024-03-04T14:35:00Z",
            "evaluation_partition": "walk_forward:fold-1",
        },
        _metadata(),
    )

    assert partition == "walk_forward:fold-1"
    assert error == "event is not inside its walk-forward test fold"


def test_sparse_equity_curves_cannot_satisfy_year_long_coverage() -> None:
    dataset = {
        "valuation_calendar": [
            "2025-01-02T21:00:00Z",
            "2026-01-02T21:00:00Z",
        ],
        "equity_curves": {
            name: [
                {"at": "2025-01-02T21:00:00Z", "equity": 100_000},
                {"at": "2026-01-02T21:00:00Z", "equity": 110_000},
            ]
            for name in (
                "THESIS",
                "SPY_BUY_HOLD",
                "ATM_25D_DEFINED_RISK_VERTICAL",
            )
        }
    }

    errors = _curve_integrity_errors(
        dataset,
        load_strategy_spec(),
        _metadata(),
    )

    assert any("2 daily observations" in error for error in errors)
    assert any("operator-supplied equity curves" in error for error in errors)


def test_positive_verdict_requires_distinct_content_addressed_replication() -> None:
    spec = load_strategy_spec()
    metadata = _metadata()
    metadata.update(
        {
            "dataset_id": "primary",
            "source_manifest": {
                "artifacts": [
                    {"role": "raw_archive", "sha256": "1" * 64},
                    {"role": "query_manifest", "sha256": "2" * 64},
                ]
            },
        }
    )

    missing = _replication_criterion(
        metadata,
        None,
        spec_digest=strategy_spec_digest(spec),
        primary_event_keys=["primary-event|2025-01-06T14:35:00Z"],
    )
    computed_replication_report = {
        "status": "replication_criteria_passed",
        "strategy_spec_sha256": strategy_spec_digest(spec),
        "dataset_id": "replication",
        "dataset_artifact_sha256": "a" * 64,
        "data_integrity_errors": [],
        "criteria": [{"name": "sample", "passed": True}],
        "verified_evidence": {
            "source_artifacts": {
                "raw_archive": "3" * 64,
                "query_manifest": "4" * 64,
            },
            "holdout_window": {
                "start": "2026-03-01T00:00:00Z",
                "end": "2027-04-01T00:00:00Z",
            },
            "holdout_event_keys": [
                "replication-event|2026-03-02T14:35:00Z"
            ],
        },
    }
    present = _replication_criterion(
        metadata,
        computed_replication_report,
        spec_digest=strategy_spec_digest(spec),
        primary_event_keys=["primary-event|2025-01-06T14:35:00Z"],
    )

    assert missing["passed"] is False
    assert present["passed"] is True

    computed_replication_report["verified_evidence"]["source_artifacts"][
        "raw_archive"
    ] = "1" * 64
    reused_archive = _replication_criterion(
        metadata,
        computed_replication_report,
        spec_digest=strategy_spec_digest(spec),
        primary_event_keys=["primary-event|2025-01-06T14:35:00Z"],
    )
    assert reused_archive["passed"] is False


def test_canonical_dataset_digest_binds_parsed_content() -> None:
    dataset = {"schema_version": "v1", "metadata": {"dataset_id": "one"}}
    first = canonical_dataset_digest(dataset)
    dataset["_artifact_sha256"] = first

    assert canonical_dataset_digest(dataset) == first
    dataset["metadata"]["dataset_id"] = "two"
    assert canonical_dataset_digest(dataset) != first


def test_source_manifest_hashes_real_files_and_rejects_mismatch(tmp_path) -> None:
    raw = tmp_path / "raw.bin"
    query = tmp_path / "query.json"
    raw.write_bytes(b"licensed raw bytes")
    query.write_text('{"query":"historical"}', encoding="utf-8")
    manifest = {
        "provider": "OPRA",
        "immutable": True,
        "artifacts": [
            {
                "role": "raw_archive",
                "path": raw.name,
                "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
            },
            {
                "role": "query_manifest",
                "path": query.name,
                "sha256": hashlib.sha256(query.read_bytes()).hexdigest(),
            },
        ],
    }

    assert _manifest_errors(manifest, tmp_path) == []
    raw.write_bytes(b"changed")
    assert any(
        "does not match" in error
        for error in _manifest_errors(manifest, tmp_path)
    )


def test_id_only_or_incomplete_grok_record_is_rejected(tmp_path) -> None:
    spec = load_strategy_spec()
    event = {
        "signal_at": "2025-01-06T14:35:00Z",
        "decision_recorded_at": "2025-01-06T14:35:10Z",
        "decision_record_id": "decision-1",
        "underlying": "SPY",
        "side": "bullish",
        "conviction": 0.8,
        "selection_log": [],
    }
    assert "required" in _grok_record_errors(event, spec, tmp_path)[0]

    record = {
        "schema_version": spec["grok_decision"]["artifact_schema"],
        "record_id": "decision-1",
        "request": {
            "recorded_at": "2025-01-06T14:35:05Z",
            "model": spec["grok_decision"]["model"],
            "request_schema": spec["grok_decision"]["request_schema"],
            "selection_log_sha256": _canonical_value_sha256([]),
        },
        "response": {
            "recorded_at": "2025-01-06T14:35:10Z",
            "underlying": "SPY",
            "direction": "bullish",
            "regime": "risk_on",
            "invalidation": "break below support",
            "horizon": "25 days",
            "expected_move_pct": 2.0,
            "iv_note": "defined risk",
            "conviction": 0.8,
        },
    }
    path = tmp_path / "decision.json"
    raw = json.dumps(record).encode()
    path.write_bytes(raw)
    event["grok_record"] = {
        "path": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }

    errors = _grok_record_errors(event, spec, tmp_path)
    assert "Grok response field setup is missing" in errors


def test_strategy_curve_is_derived_and_requires_daily_nbbo_marks() -> None:
    spec = load_strategy_spec()
    calendar = [
        datetime.fromisoformat("2025-01-01T21:00:00+00:00"),
        datetime.fromisoformat("2025-01-03T21:00:00+00:00"),
        datetime.fromisoformat("2025-01-04T21:00:00+00:00"),
    ]
    event = {
        "event_id": "one",
        "quotes": [
            {
                "at": "2025-01-03T21:00:00Z",
                "long_bid": 1.5,
                "short_ask": 0.5,
            }
        ],
    }
    trade = {
        "event_id": "one",
        "status": "completed",
        "entry_at": "2025-01-02T14:35:00Z",
        "exit_at": "2025-01-04T14:35:00Z",
        "filled_qty": 1,
        "net_pnl": 17.34,
        "cash_flows": [
            {
                "at": "2025-01-02T14:35:00Z",
                "kind": "entry",
                "qty": 1,
                "amount": -101.30,
            },
            {
                "at": "2025-01-04T14:35:00Z",
                "kind": "close",
                "qty": 1,
                "amount": 118.64,
            },
        ],
    }
    curve, errors = _derived_strategy_curve(
        {"equity_curves": {"THESIS": [{"equity": 999_999}]}},
        [event],
        [trade],
        calendar,
        spec,
        initial_equity=100_000,
    )

    assert errors == []
    assert curve[-1]["equity"] == 100_017.34
    event["quotes"] = []
    _, missing_mark_errors = _derived_strategy_curve(
        {},
        [event],
        [trade],
        calendar,
        spec,
        initial_equity=100_000,
    )
    assert any("paired NBBO mark" in error for error in missing_mark_errors)


def test_benchmark_curves_are_constructed_from_sip_prices() -> None:
    spec = load_strategy_spec()
    calendar = [
        datetime.fromisoformat("2025-01-02T21:00:00+00:00"),
        datetime.fromisoformat("2025-01-03T21:00:00+00:00"),
    ]
    curves, errors = _derived_benchmark_curves(
        {
            "benchmark_inputs": {
                "SPY_BUY_HOLD": [
                    {
                        "at": "2025-01-02T21:00:00Z",
                        "price": 100,
                        "source": "SIP",
                    },
                    {
                        "at": "2025-01-03T21:00:00Z",
                        "price": 110,
                        "source": "SIP",
                    },
                ]
            }
        },
        calendar,
        spec,
        initial_equity=100_000,
    )

    assert errors == []
    assert curves["SPY_BUY_HOLD"][-1]["equity"] == 110_000
    assert curves["CASH"][-1]["equity"] == 100_000