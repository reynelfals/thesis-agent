from __future__ import annotations

import math
from dataclasses import dataclass

from thesis.alpaca.client import PaperClient
from thesis.risk import ALLOWLIST


@dataclass
class MarketSnapshot:
    symbol: str
    spot: float
    sma5: float
    sma20: float
    ret_5d_pct: float
    realized_vol_20d: float
    regime: str


def _sma(xs: list[float], n: int) -> float:
    window = xs[-n:]
    return sum(window) / len(window)


def _regime(spot: float, sma5: float, sma20: float, vol: float) -> str:
    if sma5 > sma20 and spot > sma20:
        trend = "uptrend"
    elif sma5 < sma20 and spot < sma20:
        trend = "downtrend"
    else:
        trend = "range"
    vol_tag = "high_vol" if vol > 0.22 else "normal_vol"
    return f"{trend}/{vol_tag}"


def snapshot(client: PaperClient, symbol: str) -> MarketSnapshot:
    if symbol not in ALLOWLIST:
        raise ValueError(f"{symbol} not allowlisted")
    closes = client.daily_closes(symbol, days=40)
    if len(closes) < 21:
        raise RuntimeError(f"not enough bars for {symbol}: {len(closes)}")
    spot = client.last_price(symbol)
    sma5 = _sma(closes, 5)
    sma20 = _sma(closes, 20)
    ret_5d = (closes[-1] / closes[-6] - 1.0) * 100 if len(closes) >= 6 else 0.0
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(-20, 0)]
    var = sum((r - sum(rets) / 20) ** 2 for r in rets) / 19
    vol = math.sqrt(var * 252)
    return MarketSnapshot(
        symbol=symbol,
        spot=spot,
        sma5=sma5,
        sma20=sma20,
        ret_5d_pct=ret_5d,
        realized_vol_20d=vol,
        regime=_regime(spot, sma5, sma20, vol),
    )


def universe(client: PaperClient, symbols: tuple[str, ...] = ("SPY", "QQQ", "IWM")) -> list[MarketSnapshot]:
    return [snapshot(client, s) for s in symbols]
