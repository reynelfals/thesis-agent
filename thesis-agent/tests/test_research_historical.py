from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from thesis.research.historical import (
    classify_validation_regimes,
    completed_bar_snapshot,
    select_vertical_as_of,
)


def test_completed_snapshot_excludes_future_and_in_progress_bars() -> None:
    start = datetime(2024, 11, 1, 21, tzinfo=timezone.utc)
    bars = [
        {
            "end_at": (start + timedelta(days=index)).isoformat(),
            "close": 100 + index,
            "volume": 1_000_000,
        }
        for index in range(40)
    ]
    bars.extend(
        [
            {
                "end_at": "2025-01-06T14:36:00Z",
                "close": 9999,
                "volume": 1_000_000,
            },
            {
                "end_at": "2025-01-07T21:00:00Z",
                "close": 9999,
                "volume": 1_000_000,
            },
        ]
    )

    snapshot = completed_bar_snapshot(
        "SPY",
        bars,
        as_of="2025-01-06T14:35:00Z",
    )

    assert snapshot.spot == 139
    assert snapshot.sma5 == 137
    assert snapshot.spot != 9999


def test_contract_selection_uses_historical_clock_not_today() -> None:
    as_of = "2025-01-06T14:35:00Z"
    expiration = date(2025, 1, 31)
    contracts = [
        {
            "symbol": f"SPY-{strike}",
            "expiration": expiration.isoformat(),
            "strike": strike,
            "option_type": "call",
            "tradable": True,
            "snapshot_at": "2025-01-06T14:34:59Z",
        }
        for strike in (95, 100, 105, 110)
    ]
    contracts.append(
        {
            "symbol": "FUTURE-105",
            "expiration": expiration.isoformat(),
            "strike": 105,
            "option_type": "call",
            "tradable": True,
            "snapshot_at": "2025-01-06T14:36:00Z",
        }
    )

    pair = select_vertical_as_of(
        contracts,
        spot=101,
        side="bullish",
        as_of=as_of,
    )

    assert pair["dte"] == 25
    assert pair["long_symbol"] == "SPY-100"
    assert pair["short_symbol"] == "SPY-105"
    assert pair["width"] == 5


def test_validation_regimes_are_derived_from_predecision_features() -> None:
    start = datetime(2024, 11, 1, 21, tzinfo=timezone.utc)
    bars = [
        {
            "end_at": (start + timedelta(days=index)).isoformat(),
            "close": 100 + index * 0.1,
            "volume": 1_000_000,
        }
        for index in range(40)
    ]
    snapshot = completed_bar_snapshot(
        "SPY",
        bars,
        as_of="2025-01-06T14:35:00Z",
        spot=110,
    )

    assert classify_validation_regimes(
        snapshot,
        high_volatility_threshold=1.0,
    ) == ["bull", "low_vol"]