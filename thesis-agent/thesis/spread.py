from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Any

from alpaca.data.requests import OptionLatestQuoteRequest
from alpaca.trading.enums import ContractType

from thesis.alpaca.client import PaperClient
from thesis.models import Side, SpreadLeg, Structure
from thesis.observe import average_dollar_volume
from thesis.risk import MAX_DTE, MIN_DTE, RiskError, size_qty


class SpreadError(ValueError):
    pass


def _dte(exp: date) -> int:
    return (exp - date.today()).days


def _width(spot: float) -> float:
    raw = max(5.0, round(spot * 0.012))
    return float(int(raw))


def natural_debit(long_ask: float, short_bid: float, width_pts: float) -> float:
    """Apply the quote sanity gates shared by scouting and final construction."""
    if long_ask <= 0 or short_bid <= 0:
        raise SpreadError("missing quotes")
    natural = round(long_ask - short_bid, 2)
    if natural <= 0.15:
        raise SpreadError(f"natural debit too small: {natural}")
    if natural >= width_pts * 0.55:
        raise SpreadError(f"debit {natural} too rich vs width {width_pts}")
    return natural


def option_quote_evidence(symbol: str, quote: Any) -> dict[str, float | str]:
    """Return bounded, sanitized two-sided quote evidence for one option leg."""
    try:
        bid = float(getattr(quote, "bid_price", None))
        ask = float(getattr(quote, "ask_price", None))
    except (TypeError, ValueError) as exc:
        raise SpreadError(f"missing two-sided quote for {symbol}") from exc
    if (
        not math.isfinite(bid)
        or not math.isfinite(ask)
        or bid <= 0
        or ask <= 0
    ):
        raise SpreadError(f"missing two-sided quote for {symbol}")
    if ask < bid:
        raise SpreadError(f"crossed quote for {symbol}")
    bid_decimal = Decimal(str(bid))
    ask_decimal = Decimal(str(ask))
    midpoint = (bid_decimal + ask_decimal) / Decimal("2")
    bid_ask_pct = float(
        (ask_decimal - bid_decimal) / midpoint * Decimal("100")
    )
    if not math.isfinite(bid_ask_pct) or not (0 <= bid_ask_pct <= 200):
        raise SpreadError(f"invalid bid/ask percentage for {symbol}")
    return {
        "symbol": symbol,
        "bid_price": round(bid, 4),
        "ask_price": round(ask, 4),
        "bid_ask_pct": bid_ask_pct,
    }


def check_option_liquidity(
    evidence: list[dict[str, float | str]],
    max_bid_ask_pct: float,
) -> None:
    """Reject any representative leg whose two-sided quote is too wide."""
    if (
        not math.isfinite(max_bid_ask_pct)
        or max_bid_ask_pct < 0
        or max_bid_ask_pct > 200
    ):
        raise SpreadError("invalid option liquidity threshold")
    for leg in evidence:
        symbol = str(leg["symbol"])
        bid_ask_pct = float(leg["bid_ask_pct"])
        if bid_ask_pct > max_bid_ask_pct:
            raise SpreadError(
                f"bid/ask spread too wide for {symbol}: "
                f"{bid_ask_pct:.2f}% > {max_bid_ask_pct:.2f}%"
            )


def check_stock_liquidity(
    avg_dollar_volume_20d: float,
    min_avg_dollar_volume: float,
) -> None:
    """Apply the stock dollar-volume threshold used by scout and builder."""
    if (
        not math.isfinite(avg_dollar_volume_20d)
        or avg_dollar_volume_20d < 0
        or not math.isfinite(min_avg_dollar_volume)
        or min_avg_dollar_volume < 0
    ):
        raise SpreadError("invalid stock liquidity evidence")
    if avg_dollar_volume_20d < min_avg_dollar_volume:
        raise SpreadError(
            "average dollar volume "
            f"${avg_dollar_volume_20d:.0f} below "
            f"${min_avg_dollar_volume:.0f} minimum"
        )


def select_vertical_pair(contracts, spot: float, side: Side):
    """Select the expiration and near-ATM width pair used by scout and builder."""
    if not contracts:
        raise SpreadError("empty option chain")
    exps = sorted({c.expiration_date for c in contracts})
    exp = min(exps, key=lambda value: (abs(_dte(value) - 25), value))
    cands = [c for c in contracts if c.expiration_date == exp]
    long_c = min(
        cands,
        key=lambda c: (
            abs(float(c.strike_price) - spot),
            float(c.strike_price),
            c.symbol,
        ),
    )
    width = _width(spot)
    if side is Side.BULLISH:
        shorts = [
            c
            for c in cands
            if float(c.strike_price) >= float(long_c.strike_price) + width
        ]
        if not shorts:
            raise SpreadError("no short call strike")
        short_c = min(shorts, key=lambda c: (float(c.strike_price), c.symbol))
    else:
        shorts = [
            c
            for c in cands
            if float(c.strike_price) <= float(long_c.strike_price) - width
        ]
        if not shorts:
            raise SpreadError("no short put strike")
        short_c = max(shorts, key=lambda c: (float(c.strike_price), c.symbol))
    return exp, long_c, short_c


def build_debit_vertical(
    client: PaperClient,
    *,
    underlying: str,
    side: Side,
    equity: float,
    conviction: float,
) -> Structure:
    try:
        daily = client.daily_observations(underlying, days=20)
        avg_dollar_volume_20d = average_dollar_volume(daily)
    except Exception as exc:
        raise SpreadError("stock liquidity data unavailable") from exc
    check_stock_liquidity(
        avg_dollar_volume_20d,
        client.settings.min_avg_dollar_volume,
    )
    option_type = ContractType.CALL if side is Side.BULLISH else ContractType.PUT
    chain = client.option_chain(
        underlying,
        option_type=option_type,
        min_dte=MIN_DTE,
        max_dte=MAX_DTE,
    )
    contracts = [c for c in (chain.option_contracts or []) if c.tradable]
    if not contracts:
        raise SpreadError(f"empty {option_type} chain for {underlying}")
    spot = client.last_price(underlying)
    exp, long_c, short_c = select_vertical_pair(contracts, spot, side)

    quotes = client.options_data.get_option_latest_quote(
        OptionLatestQuoteRequest(symbol_or_symbols=[long_c.symbol, short_c.symbol])
    )
    long_evidence = option_quote_evidence(
        long_c.symbol, quotes.get(long_c.symbol)
    )
    short_evidence = option_quote_evidence(
        short_c.symbol, quotes.get(short_c.symbol)
    )
    check_option_liquidity(
        [long_evidence, short_evidence],
        client.settings.max_option_bid_ask_pct,
    )
    long_ask = float(long_evidence["ask_price"])
    short_bid = float(short_evidence["bid_price"])
    width_pts = abs(float(short_c.strike_price) - float(long_c.strike_price))
    natural = natural_debit(long_ask, short_bid, width_pts)
    limit = round(natural + 0.10, 2)
    qty = size_qty(equity, limit, conviction)
    if qty < 1:
        raise RiskError("size_qty=0 (budget)")
    debit_usd = limit * 100 * qty
    return Structure(
        underlying=underlying,
        long_symbol=long_c.symbol,
        short_symbol=short_c.symbol,
        expiration=exp.isoformat(),
        long_strike=float(long_c.strike_price),
        short_strike=float(short_c.strike_price),
        dte=_dte(exp),
        debit_limit=limit,
        qty=qty,
        max_loss_usd=debit_usd,
        avg_dollar_volume_20d=round(avg_dollar_volume_20d, 2),
        min_avg_dollar_volume=client.settings.min_avg_dollar_volume,
        max_option_bid_ask_pct=client.settings.max_option_bid_ask_pct,
        long_bid_ask_pct=float(long_evidence["bid_ask_pct"]),
        short_bid_ask_pct=float(short_evidence["bid_ask_pct"]),
        legs=[
            SpreadLeg(symbol=long_c.symbol, side="buy", position_intent="buy_to_open"),
            SpreadLeg(symbol=short_c.symbol, side="sell", position_intent="sell_to_open"),
        ],
    )
