from __future__ import annotations

import math
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from thesis.alpaca.client import PaperClient
from thesis.risk import ALLOWLIST, BASELINE_UNIVERSE


@dataclass
class MarketSnapshot:
    symbol: str
    spot: float
    sma5: float
    sma20: float
    ret_5d_pct: float
    realized_vol_20d: float
    regime: str
    avg_dollar_volume_20d: float = 0.0


@dataclass
class ObservationResult:
    snapshots: list[MarketSnapshot] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0
    symbol_durations_ms: dict[str, float] = field(default_factory=dict)


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


def average_dollar_volume(daily: list[Any], days: int = 20) -> float:
    if days <= 0 or len(daily) < days:
        raise RuntimeError("not enough daily bars for dollar volume")
    values: list[float] = []
    for bar in daily[-days:]:
        try:
            dollar_volume = float(bar.close) * float(bar.volume)
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError("invalid daily bar for dollar volume") from exc
        if not math.isfinite(dollar_volume) or dollar_volume < 0:
            raise RuntimeError("invalid daily bar for dollar volume")
        values.append(dollar_volume)
    return sum(values) / days


def snapshot(client: PaperClient, symbol: str) -> MarketSnapshot:
    if symbol not in ALLOWLIST:
        raise ValueError(f"{symbol} not allowlisted")
    daily = client.daily_observations(symbol, days=40)
    closes = [bar.close for bar in daily]
    if len(closes) < 21:
        raise RuntimeError(f"not enough bars for {symbol}: {len(closes)}")
    spot = client.last_price(symbol)
    sma5 = _sma(closes, 5)
    sma20 = _sma(closes, 20)
    ret_5d = (closes[-1] / closes[-6] - 1.0) * 100 if len(closes) >= 6 else 0.0
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(-20, 0)]
    var = sum((r - sum(rets) / 20) ** 2 for r in rets) / 19
    vol = math.sqrt(var * 252)
    avg_dollar_volume = average_dollar_volume(daily)
    return MarketSnapshot(
        symbol=symbol,
        spot=spot,
        sma5=sma5,
        sma20=sma20,
        ret_5d_pct=ret_5d,
        realized_vol_20d=vol,
        regime=_regime(spot, sma5, sma20, vol),
        avg_dollar_volume_20d=avg_dollar_volume,
    )


def universe(
    client: PaperClient, symbols: tuple[str, ...] = BASELINE_UNIVERSE
) -> list[MarketSnapshot]:
    return [snapshot(client, s) for s in symbols]


def observe_universe(
    client: PaperClient, symbols: tuple[str, ...] = BASELINE_UNIVERSE
) -> ObservationResult:
    """Observe each allowlisted symbol independently and sanitize failures."""
    result = ObservationResult()
    started = perf_counter()
    for symbol in symbols:
        symbol_started = perf_counter()
        try:
            result.snapshots.append(snapshot(client, symbol))
        except Exception:
            result.failures.append(
                {
                    "symbol": symbol,
                    "stock_rank": None,
                    "stock_score": 0.0,
                    "factors": {},
                    "regime": "unavailable",
                    "probed": False,
                    "call_count": 0,
                    "put_count": 0,
                    "feasible_sides": [],
                    "options_score": 0.0,
                    "total_score": 0.0,
                    "status": "unavailable",
                    "reason": "market_data_unavailable",
                }
            )
        finally:
            result.symbol_durations_ms[symbol] = round(
                (perf_counter() - symbol_started) * 1000,
                3,
            )
    result.duration_ms = round((perf_counter() - started) * 1000, 3)
    return result
