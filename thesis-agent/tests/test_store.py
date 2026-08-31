from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

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
    assert json.loads(
        (tmp_path / "cycles" / "cycle-1.json").read_text()
    ) == first
    assert json.loads(
        (tmp_path / "cycles" / "cycle-2.json").read_text()
    ) == second


def test_cycle_archive_rejects_id_collision_without_mutating_original(
    tmp_path,
) -> None:
    store = ThesisStore(tmp_path / "audit.sqlite")
    original = {"id": "fixed", "at": "2026-01-01T00:00:00Z", "thesis": {}}
    store.save_cycle(original)
    path = tmp_path / "cycles" / "fixed.json"
    before = path.read_bytes()

    with pytest.raises(ValueError, match="immutable cycle id collision"):
        store.save_cycle({**original, "decision": "different"})

    assert path.read_bytes() == before
    assert store.last_cycle() == original


def test_legacy_last_cycle_is_archived_before_later_cycle_overwrites_pointer(
    tmp_path,
) -> None:
    legacy = {
        "id": "aug-31-completed",
        "at": "2026-08-31T14:35:00-04:00",
        "decision": "submitted",
        "thesis": {"id": "aug-31-thesis", "structure": {"kind": "debit_vertical"}},
    }
    legacy_bytes = (
        b'{\n  "id": "aug-31-completed",\n'
        b'  "at": "2026-08-31T14:35:00-04:00",\n'
        b'  "decision": "submitted",\n'
        b'  "thesis": {"id": "aug-31-thesis", "structure": {"kind": "debit_vertical"}}\n'
        b"}"
    )
    assert json.loads(legacy_bytes) == legacy
    (tmp_path / "last_cycle.json").write_bytes(legacy_bytes)

    store = ThesisStore(tmp_path / "audit.sqlite")
    archived = tmp_path / "cycles" / "aug-31-completed.json"
    assert archived.read_bytes() == legacy_bytes

    later = {
        "id": "sep-01-analysis",
        "at": "2026-09-01T14:35:00-04:00",
        "decision": "blocked",
        "thesis": {"id": "sep-01-thesis"},
    }
    store.save_cycle(later)

    assert store.last_cycle() == later
    assert archived.read_bytes() == legacy_bytes
    assert [cycle["id"] for cycle in store.list_cycles()] == [
        "sep-01-analysis",
        "aug-31-completed",
    ]


def test_opening_order_reservation_is_atomic_and_persistent(tmp_path) -> None:
    path = tmp_path / "audit.sqlite"
    ThesisStore(path)

    def reserve(index: int) -> bool:
        return ThesisStore(path).reserve_opening_order(
            ny_date="2026-09-01",
            slot="opening-1",
            thesis_id=f"thesis-{index}",
            client_order_id=f"thesis-20260901-open-{index}",
            created_at="2026-09-01T13:30:00Z",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(16)))

    assert sum(results) == 1
    persisted = ThesisStore(path).opening_order_reservation("2026-09-01")
    assert persisted is not None
    assert persisted["state"] == "reserved"


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