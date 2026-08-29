from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from thesis.models import (
    OrderSnapshot,
    PerformanceSnapshot,
    Side,
    Thesis,
)
from thesis.store import ThesisStore


def _performance(at: datetime) -> PerformanceSnapshot:
    return PerformanceSnapshot(
        observed_at=at,
        starting_equity=100000,
        equity=100100,
        last_equity=100050,
        cash=99900,
        buying_power=199800,
        options_buying_power=99900,
        total_pl=100,
        total_return_pct=0.1,
        realized_pl=50,
        unrealized_pl=50,
        fees=0,
        reconciliation_delta=0,
        fill_count=4,
    )


def test_store_keeps_append_only_cycles_and_deduplicates_same_record(tmp_path) -> None:
    store = ThesisStore(tmp_path / "audit.sqlite")
    first = {
        "id": "cycle-1",
        "at": "2026-01-01T10:00:00+00:00",
        "decision": "blocked",
        "thesis": {"id": "thesis-1"},
    }
    second = {
        "id": "cycle-2",
        "at": "2026-01-01T11:00:00+00:00",
        "decision": "submitted",
        "thesis": {"id": "thesis-2"},
    }

    store.save_cycle(first)
    store.save_cycle(first)
    store.save_cycle(second)

    cycles = store.list_cycles()
    assert [cycle["id"] for cycle in cycles] == ["cycle-2", "cycle-1"]
    assert store.last_cycle()["id"] == "cycle-2"


def test_store_keeps_order_status_and_performance_snapshots(tmp_path) -> None:
    store = ThesisStore(tmp_path / "audit.sqlite")
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    accepted = OrderSnapshot(
        observed_at=at,
        order_id="order-1",
        status="accepted",
        submitted_at=at,
        updated_at=at,
        qty=1,
    )
    filled = accepted.model_copy(
        update={
            "observed_at": datetime(2026, 1, 1, 10, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 1, 1, 10, 1, tzinfo=timezone.utc),
            "status": "filled",
            "filled_qty": 1,
            "filled_avg_price": 1.25,
        }
    )

    store.save_order_snapshot("thesis-1", accepted)
    store.save_order_snapshot("thesis-1", accepted)
    store.save_order_snapshot("thesis-1", filled)
    store.save_performance(_performance(at))
    store.save_performance(_performance(at))

    assert [snapshot.status for snapshot in store.order_snapshots("thesis-1")] == [
        "accepted",
        "filled",
    ]
    assert store.latest_order_snapshot("order-1").status == "filled"
    assert len(store.performance_history()) == 1


def test_store_loads_legacy_monitoring_without_attribution_status(
    tmp_path,
) -> None:
    path = tmp_path / "audit.sqlite"
    store = ThesisStore(path)
    thesis = Thesis(
        underlying="SPY",
        side=Side.BEARISH,
        regime="downtrend",
        setup="Legacy setup",
        invalidation="Close above SMA20",
        horizon="21-45 DTE",
        expected_move_pct=1,
        iv_note="unknown",
        conviction=0.5,
        decision="submitted",
    )
    payload = thesis.model_dump(mode="json")
    payload["monitoring"] = {
        "observed_at": "2026-01-01T10:00:00Z",
        "entry_status": "filled",
        "position_status": "flat",
        "open_leg_count": 0,
        "expected_leg_count": 2,
        "market_value": 0,
        "unrealized_pl": 0,
        "exit_status": "flat_unlinked",
        "exit_reason": "legacy record",
    }
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO theses (id, status, json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                thesis.id,
                thesis.status.value,
                json.dumps(payload),
                thesis.created_at.isoformat(),
            ),
        )

    loaded = store.list()[0]

    assert loaded.monitoring is not None
    assert loaded.monitoring.attribution_status == "unverified"