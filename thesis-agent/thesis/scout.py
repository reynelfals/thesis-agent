from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from alpaca.data.requests import OptionLatestQuoteRequest
from alpaca.trading.enums import ContractType

from thesis.alpaca.client import PaperClient
from thesis.models import Side
from thesis.observe import MarketSnapshot
from thesis.risk import BASELINE_UNIVERSE, MAX_DTE, MIN_DTE
from thesis.spread import (
    SpreadError,
    check_option_liquidity,
    check_stock_liquidity,
    natural_debit,
    option_quote_evidence,
    select_vertical_pair,
)

PROBE_LIMIT = 5
SHORTLIST_LIMIT = 3


@dataclass(frozen=True)
class ScoutResult:
    leaderboard: list[dict[str, Any]]
    shortlist: list[MarketSnapshot]
    shortlist_sides: dict[str, list[str]]
    stage_counts: dict[str, int] = field(default_factory=dict)
    duration_ms: float = 0.0
    stock_rank_duration_ms: float = 0.0
    options_probe_duration_ms: float = 0.0


@dataclass(frozen=True)
class _SideProbe:
    feasible: bool
    legs: list[dict[str, float | str]]
    liquidity_failed: bool = False


def _factor_scores(snap: MarketSnapshot) -> dict[str, float]:
    spot = max(abs(snap.spot), 0.01)
    distance = min(abs(snap.spot - snap.sma20) / spot / 0.05, 1.0)
    aligned = (
        snap.spot > snap.sma5 > snap.sma20
        or snap.spot < snap.sma5 < snap.sma20
    )
    partly_aligned = (snap.spot - snap.sma20) * (snap.sma5 - snap.sma20) > 0
    alignment = 1.0 if aligned else (0.5 if partly_aligned else 0.0)
    trend = (distance + alignment) / 2
    momentum = min(abs(snap.ret_5d_pct) / 5.0, 1.0)
    # Directional debit spreads are best suited to meaningful, but not extreme, vol.
    vol_fit = max(0.0, 1.0 - abs(snap.realized_vol_20d - 0.25) / 0.25)
    return {
        "trend_distance": round(distance, 4),
        "trend_alignment": round(alignment, 4),
        "trend": round(trend, 4),
        "momentum_5d": round(momentum, 4),
        "volatility_fit": round(vol_fit, 4),
    }


def rank_snapshots(snaps: list[MarketSnapshot]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for snap in snaps:
        factors = _factor_scores(snap)
        score = (
            factors["trend"] * 0.45
            + factors["momentum_5d"] * 0.35
            + factors["volatility_fit"] * 0.20
        )
        rows.append(
            {
                "symbol": snap.symbol,
                "stock_score": round(score, 4),
                "factors": factors,
                "regime": snap.regime,
                "avg_dollar_volume_20d": round(
                    snap.avg_dollar_volume_20d, 2
                ),
                "probed": False,
                "call_count": 0,
                "put_count": 0,
                "feasible_sides": [],
                "options_score": 0.0,
                "total_score": round(score, 4),
                "status": "not_probed",
                "reason": "outside_top_five",
                "option_liquidity": {},
                "min_avg_dollar_volume": None,
                "max_option_bid_ask_pct": None,
            }
        )
    rows.sort(key=lambda row: (-row["stock_score"], row["symbol"]))
    for rank, row in enumerate(rows, 1):
        row["stock_rank"] = rank
    return rows


def compare_stock_candidates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare first-stage stock ranks without spending more option-data budget."""
    ranked = [
        row
        for row in rows
        if isinstance(row.get("stock_rank"), int)
        and isinstance(row.get("stock_score"), (int, float))
    ]
    ranked.sort(key=lambda row: row["stock_rank"])
    baseline = [row for row in ranked if row.get("symbol") in BASELINE_UNIVERSE]
    active_top = ranked[:PROBE_LIMIT]
    baseline_top = baseline[:PROBE_LIMIT]

    def average_score(values: list[dict[str, Any]]) -> float:
        if not values:
            return 0.0
        return round(
            sum(float(row["stock_score"]) for row in values) / len(values),
            4,
        )

    active_average = average_score(active_top)
    baseline_average = average_score(baseline_top)
    baseline_symbols = {row["symbol"] for row in baseline_top}
    active_symbols = {row["symbol"] for row in active_top}
    return {
        "baseline_ranked_count": len(baseline),
        "active_ranked_count": len(ranked),
        "baseline_top_symbol": baseline[0]["symbol"] if baseline else None,
        "baseline_top_stock_score": (
            float(baseline[0]["stock_score"]) if baseline else None
        ),
        "active_top_symbol": ranked[0]["symbol"] if ranked else None,
        "active_top_stock_score": (
            float(ranked[0]["stock_score"]) if ranked else None
        ),
        "baseline_top_five_avg_stock_score": baseline_average,
        "active_top_five_avg_stock_score": active_average,
        "top_five_avg_stock_score_delta": round(
            active_average - baseline_average,
            4,
        ),
        "top_five_overlap_count": len(active_symbols & baseline_symbols),
        "new_top_five_symbols": sorted(active_symbols - baseline_symbols),
    }


def _contracts(chain: Any) -> list[Any]:
    return [c for c in (getattr(chain, "option_contracts", None) or []) if c.tradable]


def _side_probe(
    client: PaperClient,
    contracts: list[Any],
    spot: float,
    side: Side,
) -> _SideProbe:
    _, long_c, short_c = select_vertical_pair(contracts, spot, side)
    quotes = client.options_data.get_option_latest_quote(
        OptionLatestQuoteRequest(symbol_or_symbols=[long_c.symbol, short_c.symbol])
    )
    try:
        long_evidence = option_quote_evidence(
            long_c.symbol, quotes.get(long_c.symbol)
        )
        short_evidence = option_quote_evidence(
            short_c.symbol, quotes.get(short_c.symbol)
        )
    except SpreadError:
        return _SideProbe(feasible=False, legs=[], liquidity_failed=True)
    evidence = [long_evidence, short_evidence]
    try:
        check_option_liquidity(
            evidence,
            client.settings.max_option_bid_ask_pct,
        )
    except SpreadError:
        return _SideProbe(
            feasible=False,
            legs=evidence,
            liquidity_failed=True,
        )
    width = abs(float(short_c.strike_price) - float(long_c.strike_price))
    try:
        natural_debit(
            float(long_evidence["ask_price"]),
            float(short_evidence["bid_price"]),
            width,
        )
    except SpreadError:
        return _SideProbe(feasible=False, legs=evidence)
    return _SideProbe(feasible=True, legs=evidence)


def scout_market(client: PaperClient, snaps: list[MarketSnapshot]) -> ScoutResult:
    """Rank every stock, then spend option-data budget only on the top five."""
    started = perf_counter()
    rows = rank_snapshots(snaps)
    ranked_at = perf_counter()
    by_symbol = {snap.symbol: snap for snap in snaps}
    for row in rows:
        row["min_avg_dollar_volume"] = client.settings.min_avg_dollar_volume
        row["max_option_bid_ask_pct"] = client.settings.max_option_bid_ask_pct
    for row in rows[:PROBE_LIMIT]:
        snap = by_symbol[row["symbol"]]
        try:
            check_stock_liquidity(
                snap.avg_dollar_volume_20d,
                client.settings.min_avg_dollar_volume,
            )
        except SpreadError:
            row["status"] = "infeasible"
            row["reason"] = "insufficient_stock_liquidity"
            continue
        row["probed"] = True
        row["reason"] = "no_valid_vertical"
        chains: dict[Side, list[Any]] = {}
        had_data_error = False
        # Both directional queries are deliberately issued independently.
        for side, option_type, count_key in (
            (Side.BULLISH, ContractType.CALL, "call_count"),
            (Side.BEARISH, ContractType.PUT, "put_count"),
        ):
            try:
                chain = client.option_chain(
                    row["symbol"],
                    option_type=option_type,
                    min_dte=MIN_DTE,
                    max_dte=MAX_DTE,
                )
                chains[side] = _contracts(chain)
                row[count_key] = len(chains[side])
            except Exception:
                chains[side] = []
                had_data_error = True

        feasible: list[str] = []
        liquidity_failed = False
        for side in (Side.BULLISH, Side.BEARISH):
            try:
                probe = _side_probe(
                    client, chains[side], by_symbol[row["symbol"]].spot, side
                )
                row["option_liquidity"][side.value] = probe.legs
                liquidity_failed = liquidity_failed or probe.liquidity_failed
                if probe.feasible:
                    feasible.append(side.value)
            except SpreadError:
                pass
            except Exception:
                had_data_error = True
        row["feasible_sides"] = feasible
        row["options_score"] = round(0.05 * len(feasible), 4)
        row["total_score"] = round(
            row["stock_score"] + row["options_score"], 4
        )
        if feasible:
            row["status"] = "feasible"
            row["reason"] = "valid_natural_debit"
        else:
            row["status"] = "infeasible"
            row["reason"] = (
                "options_data_unavailable"
                if had_data_error
                else (
                    "option_liquidity_failed"
                    if liquidity_failed
                    else "no_valid_vertical"
                )
            )

    feasible_rows = [row for row in rows if row["status"] == "feasible"]
    feasible_rows.sort(
        key=lambda row: (-row["total_score"], row["stock_rank"], row["symbol"])
    )
    shortlist = [
        by_symbol[row["symbol"]] for row in feasible_rows[:SHORTLIST_LIMIT]
    ]
    shortlist_sides = {
        row["symbol"]: list(row["feasible_sides"])
        for row in feasible_rows[:SHORTLIST_LIMIT]
    }
    finished = perf_counter()
    return ScoutResult(
        leaderboard=rows,
        shortlist=shortlist,
        shortlist_sides=shortlist_sides,
        stage_counts={
            "stock_ranked": len(rows),
            "options_probe_budget": PROBE_LIMIT,
            "options_probe_candidates": min(PROBE_LIMIT, len(rows)),
            "options_probed": sum(1 for row in rows if row["probed"]),
            "shortlist_budget": SHORTLIST_LIMIT,
            "shortlist_size": len(shortlist),
        },
        duration_ms=round((finished - started) * 1000, 3),
        stock_rank_duration_ms=round((ranked_at - started) * 1000, 3),
        options_probe_duration_ms=round((finished - ranked_at) * 1000, 3),
    )