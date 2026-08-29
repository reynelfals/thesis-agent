from __future__ import annotations

import thesis.observe as observe_module
from thesis.alpaca.client import DailyStockObservation
from thesis.observe import MarketSnapshot


def test_observation_failure_isolated_and_sanitized(monkeypatch) -> None:
    secret = "Authorization Bearer stock-provider-secret"
    attempted = []

    def fake_snapshot(client, symbol):
        attempted.append(symbol)
        if symbol == "QQQ":
            raise RuntimeError(secret)
        return MarketSnapshot(
            symbol=symbol,
            spot=100,
            sma5=99,
            sma20=98,
            ret_5d_pct=1,
            realized_vol_20d=0.2,
            regime="uptrend/normal_vol",
        )

    monkeypatch.setattr(observe_module, "snapshot", fake_snapshot)

    result = observe_module.observe_universe(None, ("SPY", "QQQ", "IWM"))

    assert attempted == ["SPY", "QQQ", "IWM"]
    assert [snap.symbol for snap in result.snapshots] == ["SPY", "IWM"]
    assert result.failures[0]["reason"] == "market_data_unavailable"
    assert secret not in str(result.failures)


def test_snapshot_computes_twenty_day_average_dollar_volume() -> None:
    daily = [
        DailyStockObservation(close=100 + day, volume=1_000_000 + day)
        for day in range(40)
    ]

    class Client:
        def daily_observations(self, symbol, days):
            assert symbol == "SPY"
            assert days == 40
            return daily

        def last_price(self, symbol):
            assert symbol == "SPY"
            return daily[-1].close

    snap = observe_module.snapshot(Client(), "SPY")

    expected = sum(bar.dollar_volume for bar in daily[-20:]) / 20
    assert snap.avg_dollar_volume_20d == expected