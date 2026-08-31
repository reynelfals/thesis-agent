from __future__ import annotations

from copy import deepcopy

import pytest

from thesis.research.simulation import simulate_event
from thesis.research.spec import load_strategy_spec


def _quote(
    at: str,
    *,
    long_bid: float = 1.9,
    long_ask: float = 2.0,
    short_bid: float = 1.0,
    short_ask: float = 1.1,
    size: int = 20,
) -> dict:
    return {
        "at": at,
        "source": "OPRA",
        "long_symbol": "SPY250131C00100000",
        "short_symbol": "SPY250131C00105000",
        "long_bid": long_bid,
        "long_ask": long_ask,
        "long_bid_size": size,
        "long_ask_size": size,
        "short_bid": short_bid,
        "short_ask": short_ask,
        "short_bid_size": size,
        "short_ask_size": size,
    }


def _event() -> dict:
    return {
        "event_id": "decision-1",
        "underlying": "SPY",
        "side": "bullish",
        "regimes": ["bull", "low_vol"],
        "conviction": 0.8,
        "signal_at": "2025-01-06T14:35:00Z",
        "stock_features_as_of": "2025-01-03T21:00:00Z",
        "contract_snapshot_at": "2025-01-06T14:35:05Z",
        "decision_recorded_at": "2025-01-06T14:35:10Z",
        "submitted_at": "2025-01-06T14:35:11Z",
        "expiration": "2025-01-31",
        "long_symbol": "SPY250131C00100000",
        "short_symbol": "SPY250131C00105000",
        "long_strike": 100,
        "short_strike": 105,
        "long_contract": {
            "symbol": "SPY250131C00100000",
            "strike": 100,
            "option_type": "call",
            "expiration": "2025-01-31",
            "snapshot_at": "2025-01-06T14:35:05Z",
        },
        "short_contract": {
            "symbol": "SPY250131C00105000",
            "strike": 105,
            "option_type": "call",
            "expiration": "2025-01-31",
            "snapshot_at": "2025-01-06T14:35:05Z",
        },
        "grok_model": "grok-4.6",
        "request_schema": "request_defined_risk_spread-v1",
        "decision_record_id": "recorded-before-order",
        "selection": {
            "stock_rank": 1,
            "shortlist_rank": 1,
            "side_was_feasible": True,
        },
        "quotes": [
            _quote("2025-01-06T14:35:10.500000Z"),
            _quote("2025-01-06T14:35:11.500000Z"),
            _quote(
                "2025-01-08T15:00:00Z",
                long_bid=2.80,
                long_ask=2.90,
                short_bid=1.10,
                short_ask=1.20,
            ),
        ],
        "expiration_underlying_mark": {
            "at": "2025-01-31T21:00:00Z",
            "price": 110,
            "source": "SIP",
        },
    }


def test_simulator_models_conservative_fill_fees_and_profit_exit() -> None:
    result = simulate_event(_event(), load_strategy_spec(), equity=100_000)

    assert result["status"] == "completed"
    assert result["requested_qty"] == 14
    assert result["filled_qty"] == 14
    assert result["assigned_qty"] == 0
    assert result["entry_debit"] == pytest.approx(1.04)
    assert result["average_exit_credit"] == pytest.approx(1.56)
    assert result["exit_reasons"] == ["profit_target"]
    assert result["fees"] > 0
    assert result["net_pnl"] < result["gross_pnl"]
    assert result["net_pnl"] > 0
    assert sum(flow["amount"] for flow in result["cash_flows"]) == pytest.approx(
        result["net_pnl"]
    )


def test_simulator_cancels_unfilled_order_instead_of_using_last_trade() -> None:
    event = _event()
    event["quotes"] = [
        event["quotes"][0],
        _quote(
            "2025-01-06T14:35:11.500000Z",
            long_bid=2.0,
            long_ask=2.1,
            short_bid=0.9,
            short_ask=1.0,
        ),
    ]

    result = simulate_event(event, load_strategy_spec(), equity=100_000)

    assert result["status"] == "unfilled"
    assert result["filled_qty"] == 0
    assert result["net_pnl"] is None


def test_simulator_models_partial_quantity_and_expiration_assignment() -> None:
    event = _event()
    event["quotes"] = [
        event["quotes"][0],
        _quote("2025-01-06T14:35:11.500000Z", size=2),
    ]

    result = simulate_event(event, load_strategy_spec(), equity=100_000)

    assert result["status"] == "completed"
    assert result["requested_qty"] == 14
    assert result["filled_qty"] == 2
    assert result["assigned_qty"] == 2
    assert result["closed_qty"] == 0
    assert result["average_exit_credit"] == 5
    assert result["exit_reasons"] == ["expiration_settlement"]
    assert result["reason"].startswith("partial entry")


def test_simulator_rejects_lookahead_and_unrecorded_grok_decisions() -> None:
    event = deepcopy(_event())
    event["stock_features_as_of"] = event["signal_at"]
    event["decision_record_id"] = ""

    result = simulate_event(event, load_strategy_spec(), equity=100_000)

    assert result["status"] == "invalid"
    assert "incomplete bars" in result["reason"]


def test_simulator_enforces_portfolio_risk_before_fill() -> None:
    result = simulate_event(
        _event(),
        load_strategy_spec(),
        equity=100_000,
        open_positions=3,
    )

    assert result["status"] == "rejected_risk"
    assert result["reason"] == "maximum open positions"


def test_simulator_rejects_quote_after_expiration_close() -> None:
    event = _event()
    event["quotes"].append(
        _quote("2025-01-31T22:00:00Z", long_bid=4.9, short_ask=0.1)
    )

    result = simulate_event(event, load_strategy_spec(), equity=100_000)

    assert result["status"] == "invalid"
    assert result["reason"] == "quote occurs after option expiration"


def test_simulator_binds_nbbo_to_selected_contract_symbols() -> None:
    event = _event()
    event["quotes"][1]["long_symbol"] = "DIFFERENT-CONTRACT"

    result = simulate_event(event, load_strategy_spec(), equity=100_000)

    assert result["status"] == "invalid"
    assert "long quote" in result["reason"]


def test_simulator_rejects_delayed_submission_lookahead() -> None:
    event = _event()
    event["decision_recorded_at"] = "2025-01-06T16:34:59Z"
    event["submitted_at"] = "2025-01-06T16:35:00Z"

    result = simulate_event(event, load_strategy_spec(), equity=100_000)

    assert result["status"] == "invalid"
    assert result["reason"] == "submission is outside the frozen signal deadline"