from __future__ import annotations

from datetime import datetime, timezone

from thesis.audit import (
    monitoring_snapshot,
    order_snapshot,
    performance_snapshot,
    position_snapshots,
    realized_pl_from_fills,
)
from thesis.models import Side, SpreadLeg, Structure, Thesis


def _thesis() -> Thesis:
    return Thesis(
        underlying="SPY",
        side=Side.BULLISH,
        regime="uptrend/normal_vol",
        setup="Breakout with breadth confirmation.",
        invalidation="Daily close below SMA20.",
        horizon="21-45 DTE",
        expected_move_pct=2.0,
        iv_note="IV rank acceptable.",
        conviction=0.7,
        structure=Structure(
            underlying="SPY",
            long_symbol="SPY260918C00600000",
            short_symbol="SPY260918C00605000",
            expiration="2026-09-18",
            long_strike=600,
            short_strike=605,
            dte=21,
            debit_limit=2.0,
            qty=1,
            max_loss_usd=200,
            legs=[
                SpreadLeg(
                    symbol="SPY260918C00600000",
                    side="buy",
                    position_intent="buy_to_open",
                ),
                SpreadLeg(
                    symbol="SPY260918C00605000",
                    side="sell",
                    position_intent="sell_to_open",
                ),
            ],
        ),
        order_id="order-1",
        decision="submitted",
    )


def test_realized_pl_from_fills_handles_long_and_short_option_lots() -> None:
    fills = [
        {
            "id": "1",
            "transaction_time": "2026-01-01T10:00:00Z",
            "symbol": "SPY260918C00600000",
            "side": "buy",
            "qty": "1",
            "price": "2.00",
        },
        {
            "id": "2",
            "transaction_time": "2026-01-01T10:00:01Z",
            "symbol": "SPY260918C00605000",
            "side": "sell",
            "qty": "1",
            "price": "1.00",
        },
        {
            "id": "3",
            "transaction_time": "2026-01-02T10:00:00Z",
            "symbol": "SPY260918C00600000",
            "side": "sell",
            "qty": "1",
            "price": "2.50",
        },
        {
            "id": "4",
            "transaction_time": "2026-01-02T10:00:01Z",
            "symbol": "SPY260918C00605000",
            "side": "buy",
            "qty": "1",
            "price": "0.75",
        },
    ]

    assert realized_pl_from_fills(fills) == 75.0


def test_performance_snapshot_reconciles_broker_sources() -> None:
    account = {
        "equity": "100100",
        "last_equity": "100050",
        "cash": "99800",
        "buying_power": "199600",
        "options_buying_power": "99800",
        "accrued_fees": "2",
    }
    positions = [
        {
            "symbol": "SPY260918C00600000",
            "side": "long",
            "qty": "1",
            "avg_entry_price": "2",
            "current_price": "2.4",
            "market_value": "240",
            "cost_basis": "200",
            "unrealized_pl": "40",
            "unrealized_plpc": "0.2",
        }
    ]
    history = {
        "base_value": 100000.0,
        "cashflow": {
            "2026-01-01": [1000.0, -250.0],
            "2026-01-02": [250.0],
        },
    }
    fills = [
        {
            "id": "1",
            "transaction_time": "2026-01-01T10:00:00Z",
            "symbol": "SPY260918C00600000",
            "side": "buy",
            "qty": "1",
            "price": "1.5",
        },
        {
            "id": "2",
            "transaction_time": "2026-01-02T10:00:00Z",
            "symbol": "SPY260918C00600000",
            "side": "sell",
            "qty": "1",
            "price": "2",
        },
    ]

    snapshot = performance_snapshot(account, positions, history, fills)

    assert snapshot.starting_equity == 100000
    assert snapshot.total_pl == -900
    assert snapshot.realized_pl == 48
    assert snapshot.unrealized_pl == 40
    assert snapshot.reconciliation_delta == -988
    assert snapshot.total_return_pct == -0.9


def test_monitoring_snapshot_marks_open_position_and_exit_watch() -> None:
    thesis = _thesis()
    positions = position_snapshots(
        [
            {
                "symbol": "SPY260918C00600000",
                "side": "long",
                "qty": "1",
                "avg_entry_price": "2",
                "current_price": "2.2",
                "market_value": "220",
                "cost_basis": "200",
                "unrealized_pl": "20",
                "unrealized_plpc": "0.1",
            },
            {
                "symbol": "SPY260918C00605000",
                "side": "short",
                "qty": "-1",
                "avg_entry_price": "1",
                "current_price": "0.9",
                "market_value": "-90",
                "cost_basis": "-100",
                "unrealized_pl": "10",
                "unrealized_plpc": "0.1",
            },
        ]
    )
    order = order_snapshot(
        {
            "id": "order-1",
            "status": "filled",
            "qty": "1",
            "filled_qty": "1",
            "filled_avg_price": "1",
            "submitted_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "filled_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "legs": [
                {
                    "id": "leg-long",
                    "symbol": "SPY260918C00600000",
                    "side": "buy",
                    "status": "filled",
                    "qty": "1",
                    "filled_qty": "1",
                },
                {
                    "id": "leg-short",
                    "symbol": "SPY260918C00605000",
                    "side": "sell",
                    "status": "filled",
                    "qty": "1",
                    "filled_qty": "1",
                },
            ],
        }
    )

    fills = [
        {
            "id": "fill-long",
            "transaction_time": "2026-01-01T10:00:00Z",
            "order_id": "leg-long",
            "symbol": "SPY260918C00600000",
            "side": "buy",
            "qty": "1",
            "price": "2",
        },
        {
            "id": "fill-short",
            "transaction_time": "2026-01-01T10:00:00Z",
            "order_id": "leg-short",
            "symbol": "SPY260918C00605000",
            "side": "sell",
            "qty": "1",
            "price": "1",
        },
    ]
    monitor = monitoring_snapshot(thesis, positions, order, fills=fills)

    assert monitor.position_status == "open — 2/2 order-linked legs on Alpaca"
    assert monitor.attribution_status == "linked_by_order_fills"
    assert monitor.unrealized_pl == 30
    assert monitor.market_value == 130
    assert monitor.exit_status == "monitoring"
    assert thesis.invalidation in monitor.exit_reason


def test_monitoring_snapshot_explains_pending_and_skipped_entries() -> None:
    thesis = _thesis()
    pending = order_snapshot({"id": "order-1", "status": "accepted", "qty": "1"})
    pending_monitor = monitoring_snapshot(thesis, [], pending)
    assert pending_monitor.position_status.startswith("entry pending")
    assert pending_monitor.exit_status == "not_started"

    thesis.order_id = None
    thesis.decision = "blocked"
    skipped_monitor = monitoring_snapshot(thesis, [], None)
    assert skipped_monitor.position_status == "not opened"
    assert skipped_monitor.exit_status == "not_applicable"


def test_monitoring_snapshot_refuses_same_symbol_without_fill_provenance() -> None:
    thesis = _thesis()
    positions = position_snapshots(
        [
            {
                "symbol": "SPY260918C00600000",
                "side": "long",
                "qty": "1",
                "market_value": "220",
                "unrealized_pl": "20",
            },
            {
                "symbol": "SPY260918C00605000",
                "side": "short",
                "qty": "1",
                "market_value": "-90",
                "unrealized_pl": "10",
            },
        ]
    )
    order = order_snapshot(
        {
            "id": "order-1",
            "status": "filled",
            "qty": "1",
            "filled_qty": "1",
        }
    )

    monitor = monitoring_snapshot(thesis, positions, order, fills=[])

    assert monitor.attribution_status == "ambiguous"
    assert monitor.exit_status == "unmanaged"
    assert "do not prove" in monitor.position_status