from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any

from alpaca.data.requests import OptionLatestQuoteRequest
from alpaca.trading.enums import ContractType

from thesis.alpaca.client import PaperClient
from thesis.models import Side, SpreadLeg, Structure
from thesis.observe import average_dollar_volume
from thesis.risk import MAX_DTE, MIN_DTE, RiskError
from thesis.spread import (
    SpreadError,
    check_option_liquidity,
    check_stock_liquidity,
    option_quote_evidence,
)

TARGET_DTE = 25
PREMIUM_EQUITY_PCT = 0.01
MAX_QUOTE_AGE_SECONDS = 300


def _dte(expiration: date) -> int:
    return (expiration - date.today()).days


def _fresh_quote(symbol: str, quote: Any) -> dict[str, float | str]:
    evidence = option_quote_evidence(symbol, quote)
    raw = getattr(quote, "timestamp", None)
    if not isinstance(raw, datetime):
        raise SpreadError(f"quote timestamp unavailable for {symbol}")
    observed = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    if not math.isfinite(age) or age < -30 or age > MAX_QUOTE_AGE_SECONDS:
        raise SpreadError(f"stale quote for {symbol}")
    evidence["quote_timestamp"] = observed.isoformat()
    return evidence


def build_single_option(
    client: PaperClient,
    *,
    underlying: str,
    side: Side,
    equity: float,
    conviction: float,
) -> Structure:
    """Build one liquid long option, moving OTM only to satisfy the 1% cap."""
    if not math.isfinite(equity) or equity <= 0:
        raise RiskError("invalid equity for premium sizing")
    try:
        daily = client.daily_observations(underlying, days=20)
        avg_dollar_volume = average_dollar_volume(daily)
    except Exception as exc:
        raise SpreadError("stock liquidity data unavailable") from exc
    check_stock_liquidity(
        avg_dollar_volume,
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
    expirations = sorted(
        {c.expiration_date for c in contracts},
        key=lambda exp: (abs(_dte(exp) - TARGET_DTE), exp),
    )
    premium_cap = equity * PREMIUM_EQUITY_PCT

    for expiration in expirations:
        candidates = [c for c in contracts if c.expiration_date == expiration]
        if side is Side.BULLISH:
            candidates.sort(
                key=lambda c: (
                    float(c.strike_price) < spot,
                    abs(float(c.strike_price) - spot),
                    float(c.strike_price),
                    c.symbol,
                )
            )
        else:
            candidates.sort(
                key=lambda c: (
                    float(c.strike_price) > spot,
                    abs(float(c.strike_price) - spot),
                    -float(c.strike_price),
                    c.symbol,
                )
            )
        for contract in candidates:
            try:
                quotes = client.options_data.get_option_latest_quote(
                    OptionLatestQuoteRequest(symbol_or_symbols=contract.symbol)
                )
                evidence = _fresh_quote(contract.symbol, quotes.get(contract.symbol))
                check_option_liquidity(
                    [evidence],
                    client.settings.max_option_bid_ask_pct,
                )
            except Exception:
                continue
            ask = float(evidence["ask_price"])
            limit = round(ask + 0.05, 2)
            if limit * 100 > premium_cap + 1e-6:
                continue
            return Structure(
                kind="single_long_option",
                underlying=underlying,
                long_symbol=contract.symbol,
                expiration=expiration.isoformat(),
                long_strike=float(contract.strike_price),
                dte=_dte(expiration),
                debit_limit=limit,
                qty=1,
                max_loss_usd=limit * 100,
                avg_dollar_volume_20d=round(avg_dollar_volume, 2),
                min_avg_dollar_volume=client.settings.min_avg_dollar_volume,
                max_option_bid_ask_pct=client.settings.max_option_bid_ask_pct,
                long_bid_ask_pct=float(evidence["bid_ask_pct"]),
                legs=[
                    SpreadLeg(
                        symbol=contract.symbol,
                        side="buy",
                        position_intent="buy_to_open",
                    )
                ],
            )
    raise RiskError(f"no liquid option fits 1% premium cap ${premium_cap:.0f}")