from __future__ import annotations

import math
import random
from datetime import datetime
from statistics import mean, stdev
from typing import Any


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("equity timestamps must include a timezone")
    return parsed


def _safe_round(value: float | None, digits: int = 8) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def equity_metrics(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate daily-equity statistics without filling missing observations."""
    normalized: list[tuple[datetime, float]] = []
    for point in points:
        at = _time(str(point["at"]))
        equity = float(point["equity"])
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError("equity values must be finite and positive")
        normalized.append((at, equity))
    normalized.sort(key=lambda item: item[0])
    if len(normalized) < 2:
        return {
            "observations": len(normalized),
            "calendar_days": 0,
            "total_return": None,
            "cagr": None,
            "annualized_volatility": None,
            "sharpe": None,
            "sortino": None,
            "maximum_drawdown": None,
        }
    if len({at for at, _ in normalized}) != len(normalized):
        raise ValueError("equity timestamps must be unique")
    returns = [
        current / previous - 1
        for (_, previous), (_, current) in zip(normalized, normalized[1:])
    ]
    calendar_days = max(1, (normalized[-1][0] - normalized[0][0]).days)
    total_return = normalized[-1][1] / normalized[0][1] - 1
    years = calendar_days / 365.2425
    cagr = (
        (normalized[-1][1] / normalized[0][1]) ** (1 / years) - 1
        if years > 0
        else None
    )
    average_return = mean(returns)
    volatility = stdev(returns) if len(returns) >= 2 else 0.0
    annualized_volatility = volatility * math.sqrt(252)
    sharpe = (
        average_return / volatility * math.sqrt(252)
        if volatility > 0
        else None
    )
    downside = [min(value, 0.0) for value in returns]
    downside_deviation = math.sqrt(mean([value * value for value in downside]))
    sortino = (
        average_return / downside_deviation * math.sqrt(252)
        if downside_deviation > 0
        else None
    )
    peak = normalized[0][1]
    maximum_drawdown = 0.0
    for _, equity in normalized:
        peak = max(peak, equity)
        maximum_drawdown = min(maximum_drawdown, equity / peak - 1)
    return {
        "observations": len(normalized),
        "calendar_days": calendar_days,
        "total_return": _safe_round(total_return),
        "cagr": _safe_round(cagr),
        "annualized_volatility": _safe_round(annualized_volatility),
        "sharpe": _safe_round(sharpe),
        "sortino": _safe_round(sortino),
        "maximum_drawdown": _safe_round(maximum_drawdown),
    }


def _moving_block_means(
    values: list[float],
    *,
    samples: int,
    seed: int,
    block_length: int,
    centered: bool,
) -> list[float]:
    if samples < 100:
        raise ValueError("bootstrap requires at least 100 samples")
    if block_length < 1:
        raise ValueError("bootstrap block length must be positive")
    generator = random.Random(seed)
    size = len(values)
    source_mean = mean(values)
    source = (
        [value - source_mean for value in values]
        if centered
        else list(values)
    )
    width = min(block_length, size)
    estimates: list[float] = []
    for _ in range(samples):
        draw: list[float] = []
        while len(draw) < size:
            start = generator.randrange(size)
            draw.extend(
                source[(start + offset) % size] for offset in range(width)
            )
        estimates.append(mean(draw[:size]))
    return estimates


def moving_block_mean_confidence_interval(
    values: list[float],
    *,
    samples: int,
    seed: int,
    block_length: int,
    confidence: float = 0.95,
) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    estimates = sorted(
        _moving_block_means(
            values,
            samples=samples,
            seed=seed,
            block_length=block_length,
            centered=False,
        )
    )
    tail = (1 - confidence) / 2
    lower_index = max(0, min(samples - 1, int(tail * samples)))
    upper_index = max(
        0,
        min(samples - 1, int((1 - tail) * samples) - 1),
    )
    return (
        _safe_round(estimates[lower_index]),
        _safe_round(estimates[upper_index]),
    )


def one_sided_moving_block_p_value(
    values: list[float],
    *,
    samples: int,
    seed: int,
    block_length: int,
) -> float | None:
    """Empirical-null test for positive mean that preserves local dependence."""
    if not values:
        return None
    observed = mean(values)
    if observed <= 0:
        return 1.0
    null_means = _moving_block_means(
        values,
        samples=samples,
        seed=seed,
        block_length=block_length,
        centered=True,
    )
    exceedances = sum(value >= observed for value in null_means)
    return _safe_round((exceedances + 1) / (samples + 1))


def trade_metrics(
    trades: list[dict[str, Any]],
    *,
    bootstrap_samples: int,
    seed: int,
    declared_variants: int,
    block_length: int,
) -> dict[str, Any]:
    completed = [
        trade
        for trade in trades
        if trade.get("status") == "completed"
        and isinstance(trade.get("net_pnl"), (int, float))
    ]
    pnl = [float(trade["net_pnl"]) for trade in completed]
    returns = [float(trade["return_on_risk"]) for trade in completed]
    lower, upper = moving_block_mean_confidence_interval(
        pnl,
        samples=bootstrap_samples,
        seed=seed,
        block_length=block_length,
    )
    raw_p = one_sided_moving_block_p_value(
        pnl,
        samples=bootstrap_samples,
        seed=seed + 1,
        block_length=block_length,
    )
    adjusted_p = (
        min(1.0, raw_p * max(1, declared_variants))
        if raw_p is not None
        else None
    )
    return {
        "trade_count": len(completed),
        "wins": sum(value > 0 for value in pnl),
        "losses": sum(value < 0 for value in pnl),
        "breakeven": sum(value == 0 for value in pnl),
        "hit_rate": _safe_round(
            sum(value > 0 for value in pnl) / len(pnl) if pnl else None
        ),
        "expectancy_dollars": _safe_round(mean(pnl) if pnl else None),
        "median_return_on_risk": _safe_round(
            sorted(returns)[len(returns) // 2] if returns else None
        ),
        "total_net_pnl": _safe_round(sum(pnl), 2) if pnl else None,
        "total_turnover": _safe_round(
            sum(float(trade.get("turnover") or 0) for trade in completed),
            2,
        ),
        "expectancy_95pct_confidence_interval": [lower, upper],
        "one_sided_moving_block_p_value": raw_p,
        "bootstrap_block_length": block_length,
        "declared_variant_count": max(1, declared_variants),
        "adjusted_p_value": _safe_round(adjusted_p),
        "multiple_testing_method": "bonferroni_declared_variant_count",
    }


def metrics_by_regime(
    trades: list[dict[str, Any]],
    *,
    bootstrap_samples: int,
    seed: int,
    declared_variants: int,
    block_length: int,
) -> dict[str, Any]:
    def labels(trade: dict[str, Any]) -> list[str]:
        values = trade.get("regimes")
        if isinstance(values, list):
            return [str(value) for value in values if value]
        return []

    regimes = sorted(
        {
            regime
            for trade in trades
            if trade.get("status") == "completed"
            for regime in labels(trade)
        }
    )
    return {
        regime: trade_metrics(
            [trade for trade in trades if regime in labels(trade)],
            bootstrap_samples=bootstrap_samples,
            seed=seed + index + 10,
            declared_variants=declared_variants,
            block_length=block_length,
        )
        for index, regime in enumerate(regimes)
    }