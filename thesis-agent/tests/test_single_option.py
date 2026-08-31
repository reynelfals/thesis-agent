from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from alpaca.trading.enums import ContractType

from thesis.alpaca.client import DailyStockObservation
from thesis.models import Side
from thesis.risk import RiskError
from thesis.single_option import build_single_option


class _Options:
    def __init__(self, *, stale: bool = False) -> None:
        self.stale = stale

    def get_option_latest_quote(self, request):
        symbol = request.symbol_or_symbols
        now = datetime.now(timezone.utc)
        timestamp = now - timedelta(minutes=10) if self.stale else now
        prices = {
            "SPYC100": (1.90, 2.00),
            "SPYC105": (0.70, 0.80),
        }
        bid, ask = prices[symbol]
        return {
            symbol: SimpleNamespace(
                bid_price=bid,
                ask_price=ask,
                timestamp=timestamp,
            )
        }


class _Client:
    def __init__(self, *, stale: bool = False) -> None:
        self.options_data = _Options(stale=stale)
        self.settings = SimpleNamespace(
            min_avg_dollar_volume=50_000_000,
            max_option_bid_ask_pct=25,
        )

    def daily_observations(self, underlying, days):
        return [
            DailyStockObservation(close=100, volume=1_000_000)
            for _ in range(days)
        ]

    def option_chain(self, underlying, *, option_type, min_dte, max_dte):
        assert option_type is ContractType.CALL
        expiration = date.today() + timedelta(days=25)
        return SimpleNamespace(
            option_contracts=[
                SimpleNamespace(
                    tradable=True,
                    expiration_date=expiration,
                    strike_price=str(strike),
                    symbol=f"SPYC{strike}",
                )
                for strike in (100, 105)
            ]
        )

    def last_price(self, underlying):
        return 100.0


def test_single_option_moves_otm_to_fit_one_percent_cap() -> None:
    structure = build_single_option(
        _Client(),
        underlying="SPY",
        side=Side.BULLISH,
        equity=10_000,
        conviction=0.8,
    )

    assert structure.kind == "single_long_option"
    assert structure.long_symbol == "SPYC105"
    assert structure.short_symbol is None
    assert structure.dte == 25
    assert structure.qty == 1
    assert structure.max_loss_usd == 85
    assert structure.max_loss_usd <= 100
    assert len(structure.legs) == 1
    assert structure.legs[0].position_intent == "buy_to_open"


def test_single_option_rejects_stale_quotes() -> None:
    with pytest.raises(RiskError, match="no liquid option fits 1% premium cap"):
        build_single_option(
            _Client(stale=True),
            underlying="SPY",
            side=Side.BULLISH,
            equity=10_000,
            conviction=0.8,
        )