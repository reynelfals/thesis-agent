from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from alpaca.trading.enums import ContractType

from thesis.alpaca.client import DailyStockObservation
from thesis.models import Side
from thesis.spread import SpreadError, build_debit_vertical


class _Options:
    def __init__(self, quote_mode: str):
        self.quote_mode = quote_mode

    def get_option_latest_quote(self, request):
        if self.quote_mode == "missing":
            long_bid, long_ask = None, 2.0
        elif self.quote_mode == "wide":
            long_bid, long_ask = 0.5, 2.0
        elif self.quote_mode == "at_limit":
            long_bid, long_ask = 1.9, 2.1
        elif self.quote_mode == "just_over_limit":
            long_bid, long_ask = 1.899998, 2.100002
        else:
            long_bid, long_ask = 1.8, 2.0
        return {
            symbol: SimpleNamespace(
                bid_price=long_bid if symbol.endswith("L") else 1.0,
                ask_price=long_ask if symbol.endswith("L") else 1.1,
            )
            for symbol in request.symbol_or_symbols
        }


class _Client:
    def __init__(
        self,
        quote_mode: str = "healthy",
        dollar_volume: float = 100_000_000,
        max_option_bid_ask_pct: float = 25,
    ):
        self.options_data = _Options(quote_mode)
        self.dollar_volume = dollar_volume
        self.settings = SimpleNamespace(
            min_avg_dollar_volume=50_000_000,
            max_option_bid_ask_pct=max_option_bid_ask_pct,
        )

    def daily_observations(self, underlying, days):
        assert underlying == "SPY"
        assert days == 20
        return [
            DailyStockObservation(
                close=100,
                volume=self.dollar_volume / 100,
            )
            for _ in range(days)
        ]

    def option_chain(self, underlying, *, option_type, min_dte, max_dte):
        assert underlying == "SPY"
        assert option_type is ContractType.CALL
        assert (min_dte, max_dte) == (14, 45)
        expiration = date.today() + timedelta(days=25)
        return SimpleNamespace(
            option_contracts=[
                SimpleNamespace(
                    tradable=True,
                    expiration_date=expiration,
                    strike_price=str(strike),
                    symbol=f"SPYC{leg}",
                )
                for strike, leg in ((100, "L"), (105, "S"))
            ]
        )

    def last_price(self, underlying):
        assert underlying == "SPY"
        return 100.0


def test_final_spread_records_healthy_leg_liquidity() -> None:
    structure = build_debit_vertical(
        _Client(),
        underlying="SPY",
        side=Side.BULLISH,
        equity=100_000,
        conviction=0.8,
    )

    assert structure.long_bid_ask_pct == pytest.approx(10.5263157895)
    assert structure.short_bid_ask_pct == pytest.approx(9.5238095238)
    assert structure.avg_dollar_volume_20d == 100_000_000
    assert structure.min_avg_dollar_volume == 50_000_000
    assert structure.max_option_bid_ask_pct == 25


@pytest.mark.parametrize("quote_mode", ["missing", "wide"])
def test_final_spread_rechecks_option_liquidity(quote_mode: str) -> None:
    with pytest.raises(SpreadError):
        build_debit_vertical(
            _Client(quote_mode),
            underlying="SPY",
            side=Side.BULLISH,
            equity=100_000,
            conviction=0.8,
        )


def test_final_spread_enforces_configured_width_without_rounding() -> None:
    at_limit = build_debit_vertical(
        _Client(
            quote_mode="at_limit",
            max_option_bid_ask_pct=10,
        ),
        underlying="SPY",
        side=Side.BULLISH,
        equity=100_000,
        conviction=0.8,
    )

    assert at_limit.long_bid_ask_pct == 10
    with pytest.raises(SpreadError, match="bid/ask spread too wide"):
        build_debit_vertical(
            _Client(
                quote_mode="just_over_limit",
                max_option_bid_ask_pct=10,
            ),
            underlying="SPY",
            side=Side.BULLISH,
            equity=100_000,
            conviction=0.8,
        )


def test_final_spread_rechecks_stock_dollar_volume() -> None:
    with pytest.raises(SpreadError, match="average dollar volume"):
        build_debit_vertical(
            _Client(dollar_volume=10_000_000),
            underlying="SPY",
            side=Side.BULLISH,
            equity=100_000,
            conviction=0.8,
        )