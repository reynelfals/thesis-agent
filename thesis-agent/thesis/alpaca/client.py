from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from alpaca.data.enums import DataFeed
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    AssetStatus,
    ContractType,
)
from alpaca.trading.requests import (
    GetOrderByIdRequest,
    GetOptionContractsRequest,
    GetPortfolioHistoryRequest,
)

from thesis.config import Settings


@dataclass(frozen=True)
class DailyStockObservation:
    close: float
    volume: float

    @property
    def dollar_volume(self) -> float:
        return self.close * self.volume


class PaperClient:
    """Paper-only Alpaca access. Callers must pass Settings that already passed assert_paper()."""

    def __init__(self, settings: Settings) -> None:
        settings.assert_paper()
        self.settings = settings
        self.trading = TradingClient(
            api_key=settings.api_key,
            secret_key=settings.secret_key,
            paper=True,
            url_override=settings.base_url,
        )
        self.stocks = StockHistoricalDataClient(
            api_key=settings.api_key,
            secret_key=settings.secret_key,
        )
        self.options_data = OptionHistoricalDataClient(
            api_key=settings.api_key,
            secret_key=settings.secret_key,
        )

    def account(self):
        return self.trading.get_account()

    def clock(self):
        return self.trading.get_clock()

    def last_price(self, symbol: str) -> float:
        trades = self.stocks.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        )
        trade = trades[symbol]
        return float(trade.price)

    def daily_observations(
        self, symbol: str, days: int = 40
    ) -> list[DailyStockObservation]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days + 15)
        bars = self.stocks.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                feed=DataFeed.IEX,
            )
        )
        frame = bars[symbol]
        observations: list[DailyStockObservation] = []
        for bar in frame:
            close = float(bar.close)
            volume = float(bar.volume)
            if (
                not math.isfinite(close)
                or not math.isfinite(volume)
                or close <= 0
                or volume < 0
            ):
                raise RuntimeError(f"invalid daily bar data for {symbol}")
            observations.append(
                DailyStockObservation(close=close, volume=volume)
            )
        return observations[-days:]

    def daily_closes(self, symbol: str, days: int = 40) -> list[float]:
        return [
            observation.close
            for observation in self.daily_observations(symbol, days=days)
        ]

    def option_chain(
        self,
        underlying: str,
        *,
        option_type: ContractType,
        min_dte: int = 14,
        max_dte: int = 45,
    ):
        today = date.today()
        req = GetOptionContractsRequest(
            underlying_symbols=[underlying],
            status=AssetStatus.ACTIVE,
            expiration_date_gte=today + timedelta(days=min_dte),
            expiration_date_lte=today + timedelta(days=max_dte),
            type=option_type,
            limit=1000,
        )
        return self.trading.get_option_contracts(req)

    def positions(self):
        return list(self.trading.get_all_positions())

    def order(self, order_id: str):
        return self.trading.get_order_by_id(
            order_id,
            GetOrderByIdRequest(nested=True),
        )

    def portfolio_history(self):
        return self.trading.get_portfolio_history(
            GetPortfolioHistoryRequest(
                period="all",
                timeframe="1D",
                pnl_reset="no_reset",
            )
        )

    def fill_activities(self, *, max_pages: int | None = None) -> list[dict]:
        """Read Alpaca fill activities oldest-first for auditable realized P&L."""
        fills: list[dict] = []
        page_token = ""
        pages = 0
        while True:
            params = {
                "activity_types": "FILL",
                "direction": "asc",
                "page_size": 100,
            }
            if page_token:
                params["page_token"] = page_token
            page = self.trading.get("/account/activities", params)
            if not isinstance(page, list) or not page:
                break
            fills.extend(page)
            pages += 1
            if len(page) < 100:
                break
            if max_pages is not None and pages >= max_pages:
                raise RuntimeError(
                    "Alpaca fill history exceeded the requested page limit; "
                    "refusing to calculate realized P&L from partial data"
                )
            next_token = str(page[-1].get("id") or "")
            if not next_token or next_token == page_token:
                raise RuntimeError(
                    "Alpaca fill pagination did not advance; refusing a "
                    "partial realized P&L audit"
                )
            page_token = next_token
        return fills
