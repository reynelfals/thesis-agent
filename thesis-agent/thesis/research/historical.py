from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from thesis.observe import MarketSnapshot
from thesis.scout import rank_snapshots


class HistoricalDataError(ValueError):
    pass


def _time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise HistoricalDataError(f"{field} must be an RFC-3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HistoricalDataError(f"{field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HistoricalDataError(f"{field} must include a timezone")
    return parsed


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise HistoricalDataError(f"{field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise HistoricalDataError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise HistoricalDataError(f"{field} must be finite and positive")
    return parsed


def _regime(spot: float, sma5: float, sma20: float, vol: float) -> str:
    if sma5 > sma20 and spot > sma20:
        trend = "uptrend"
    elif sma5 < sma20 and spot < sma20:
        trend = "downtrend"
    else:
        trend = "range"
    return f"{trend}/{'high_vol' if vol > 0.22 else 'normal_vol'}"


def classify_validation_regimes(
    snapshot: MarketSnapshot,
    *,
    high_volatility_threshold: float = 0.22,
) -> list[str]:
    """Return the frozen, objective trend and volatility study buckets."""
    trend = "bull" if snapshot.spot >= snapshot.sma20 else "bear"
    volatility = (
        "high_vol"
        if snapshot.realized_vol_20d > high_volatility_threshold
        else "low_vol"
    )
    return [trend, volatility]


def completed_bar_snapshot(
    symbol: str,
    bars: list[dict[str, Any]],
    *,
    as_of: str,
    spot: float | None = None,
) -> MarketSnapshot:
    """Build the live factors using only bars explicitly completed before as_of."""
    cutoff = _time(as_of, "as_of")
    eligible: list[tuple[datetime, float, float]] = []
    for bar in bars:
        if not isinstance(bar, dict):
            raise HistoricalDataError("bars must be objects")
        end_at = _time(bar.get("end_at"), "bars[].end_at")
        if end_at >= cutoff:
            continue
        eligible.append(
            (
                end_at,
                _number(bar.get("close"), "bars[].close"),
                _number(bar.get("volume"), "bars[].volume"),
            )
        )
    eligible.sort(key=lambda item: item[0])
    if len(eligible) < 21:
        raise HistoricalDataError(
            f"not enough completed bars for {symbol}: {len(eligible)}"
        )
    closes = [close for _, close, _ in eligible]
    sma5 = sum(closes[-5:]) / 5
    sma20 = sum(closes[-20:]) / 20
    returns = [
        math.log(closes[index] / closes[index - 1])
        for index in range(len(closes) - 20, len(closes))
    ]
    average_return = sum(returns) / len(returns)
    variance = sum(
        (value - average_return) ** 2 for value in returns
    ) / (len(returns) - 1)
    realized_vol = math.sqrt(variance * 252)
    point_in_time_spot = closes[-1] if spot is None else _number(spot, "spot")
    return MarketSnapshot(
        symbol=symbol,
        spot=point_in_time_spot,
        sma5=sma5,
        sma20=sma20,
        ret_5d_pct=(closes[-1] / closes[-6] - 1) * 100,
        realized_vol_20d=realized_vol,
        regime=_regime(point_in_time_spot, sma5, sma20, realized_vol),
        avg_dollar_volume_20d=sum(
            close * volume for _, close, volume in eligible[-20:]
        )
        / 20,
    )


def rank_universe_as_of(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    as_of: str,
    spots_by_symbol: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    spots = spots_by_symbol or {}
    snapshots = [
        completed_bar_snapshot(
            symbol,
            bars,
            as_of=as_of,
            spot=spots.get(symbol),
        )
        for symbol, bars in sorted(bars_by_symbol.items())
    ]
    return rank_snapshots(snapshots)


def select_vertical_as_of(
    contracts: list[dict[str, Any]],
    *,
    spot: float,
    side: str,
    as_of: str,
    minimum_dte: int = 14,
    maximum_dte: int = 45,
    target_dte: int = 25,
) -> dict[str, Any]:
    """Select the live near-ATM pair using a historical clock and chain snapshot."""
    cutoff = _time(as_of, "as_of")
    if side not in {"bullish", "bearish"}:
        raise HistoricalDataError("side must be bullish or bearish")
    required_option_type = "call" if side == "bullish" else "put"
    eligible: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    for contract in contracts:
        if not isinstance(contract, dict):
            raise HistoricalDataError("contracts must be objects")
        snapshot_at = _time(
            contract.get("snapshot_at"),
            "contracts[].snapshot_at",
        )
        if (
            snapshot_at > cutoff
            or contract.get("tradable") is not True
            or contract.get("option_type") != required_option_type
        ):
            continue
        symbol = str(contract.get("symbol") or "")
        if not symbol or symbol in seen_symbols:
            raise HistoricalDataError("contract symbols must be unique and non-empty")
        seen_symbols.add(symbol)
        try:
            expiration = date.fromisoformat(str(contract.get("expiration")))
        except ValueError as exc:
            raise HistoricalDataError("contract expiration is invalid") from exc
        dte = (expiration - cutoff.date()).days
        if minimum_dte <= dte <= maximum_dte:
            eligible.append(
                {
                    **contract,
                    "_expiration": expiration,
                    "_strike": _number(
                        contract.get("strike"),
                        "contracts[].strike",
                    ),
                }
            )
    if not eligible:
        raise HistoricalDataError("no point-in-time contracts in the DTE range")
    expirations = sorted({contract["_expiration"] for contract in eligible})
    expiration = min(
        expirations,
        key=lambda value: (
            abs((value - cutoff.date()).days - target_dte),
            value,
        ),
    )
    candidates = [
        contract
        for contract in eligible
        if contract["_expiration"] == expiration
    ]
    long_contract = min(
        candidates,
        key=lambda contract: (
            abs(contract["_strike"] - spot),
            contract["_strike"],
            str(contract.get("symbol") or ""),
        ),
    )
    width = float(int(max(5.0, round(spot * 0.012))))
    if side == "bullish":
        shorts = [
            contract
            for contract in candidates
            if contract["_strike"] >= long_contract["_strike"] + width
        ]
        if not shorts:
            raise HistoricalDataError("no point-in-time short call strike")
        short_contract = min(
            shorts,
            key=lambda contract: (
                contract["_strike"],
                str(contract.get("symbol") or ""),
            ),
        )
    else:
        shorts = [
            contract
            for contract in candidates
            if contract["_strike"] <= long_contract["_strike"] - width
        ]
        if not shorts:
            raise HistoricalDataError("no point-in-time short put strike")
        short_contract = max(
            shorts,
            key=lambda contract: (
                contract["_strike"],
                str(contract.get("symbol") or ""),
            ),
        )
    return {
        "expiration": expiration.isoformat(),
        "dte": (expiration - cutoff.date()).days,
        "long_symbol": str(long_contract.get("symbol") or ""),
        "short_symbol": str(short_contract.get("symbol") or ""),
        "long_strike": long_contract["_strike"],
        "short_strike": short_contract["_strike"],
        "width": abs(short_contract["_strike"] - long_contract["_strike"]),
    }