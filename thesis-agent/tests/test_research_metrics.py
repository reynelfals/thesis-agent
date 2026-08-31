from __future__ import annotations

import pytest

from thesis.research.metrics import equity_metrics, trade_metrics


def test_equity_metrics_include_risk_and_drawdown_statistics() -> None:
    result = equity_metrics(
        [
            {"at": "2025-01-02T21:00:00Z", "equity": 100_000},
            {"at": "2025-01-03T21:00:00Z", "equity": 101_000},
            {"at": "2025-01-06T21:00:00Z", "equity": 99_000},
            {"at": "2026-01-02T21:00:00Z", "equity": 110_000},
        ]
    )

    assert result["observations"] == 4
    assert result["calendar_days"] == 365
    assert result["total_return"] == pytest.approx(0.1)
    assert result["cagr"] > 0
    assert result["annualized_volatility"] > 0
    assert result["maximum_drawdown"] < 0


def test_trade_metrics_are_seeded_and_multiple_test_adjusted() -> None:
    trades = [
        {
            "status": "completed",
            "net_pnl": value,
            "return_on_risk": value / 1000,
            "turnover": 2000,
        }
        for value in (100, 80, 120, -20, 90, 110)
    ]

    first = trade_metrics(
        trades,
        bootstrap_samples=500,
        seed=123,
        declared_variants=3,
        block_length=3,
    )
    second = trade_metrics(
        trades,
        bootstrap_samples=500,
        seed=123,
        declared_variants=3,
        block_length=3,
    )

    assert first == second
    assert first["trade_count"] == 6
    assert first["hit_rate"] == pytest.approx(5 / 6)
    assert first["adjusted_p_value"] >= first["one_sided_moving_block_p_value"]
    assert first["expectancy_95pct_confidence_interval"][0] is not None