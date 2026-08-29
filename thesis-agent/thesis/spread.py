from __future__ import annotations

from datetime import date

from alpaca.data.requests import OptionLatestQuoteRequest
from alpaca.trading.enums import ContractType

from thesis.alpaca.client import PaperClient
from thesis.models import Side, SpreadLeg, Structure
from thesis.risk import MAX_DTE, MIN_DTE, RiskError, size_qty


class SpreadError(ValueError):
    pass


def _dte(exp: date) -> int:
    return (exp - date.today()).days


def _width(spot: float) -> float:
    raw = max(5.0, round(spot * 0.012))
    return float(int(raw))


def build_debit_vertical(
    client: PaperClient,
    *,
    underlying: str,
    side: Side,
    equity: float,
    conviction: float,
) -> Structure:
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
    today = date.today()
    exps = sorted({c.expiration_date for c in contracts})
    exp = min(exps, key=lambda e: abs(_dte(e) - 25))
    cands = [c for c in contracts if c.expiration_date == exp]
    spot = client.last_price(underlying)
    long_c = min(cands, key=lambda c: abs(float(c.strike_price) - spot))
    width = _width(spot)
    if side is Side.BULLISH:
        higher = [c for c in cands if float(c.strike_price) >= float(long_c.strike_price) + width]
        if not higher:
            raise SpreadError("no short call strike")
        short_c = min(higher, key=lambda c: float(c.strike_price))
    else:
        lower = [c for c in cands if float(c.strike_price) <= float(long_c.strike_price) - width]
        if not lower:
            raise SpreadError("no short put strike")
        short_c = max(lower, key=lambda c: float(c.strike_price))

    quotes = client.options_data.get_option_latest_quote(
        OptionLatestQuoteRequest(symbol_or_symbols=[long_c.symbol, short_c.symbol])
    )
    long_q = quotes[long_c.symbol]
    short_q = quotes[short_c.symbol]
    long_ask = float(long_q.ask_price or 0)
    short_bid = float(short_q.bid_price or 0)
    if long_ask <= 0 or short_bid <= 0:
        raise SpreadError("missing quotes")
    natural = round(long_ask - short_bid, 2)
    if natural <= 0.15:
        raise SpreadError(f"natural debit too small: {natural}")
    width_pts = abs(float(short_c.strike_price) - float(long_c.strike_price))
    if natural >= width_pts * 0.55:
        raise SpreadError(f"debit {natural} too rich vs width {width_pts}")
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
        legs=[
            SpreadLeg(symbol=long_c.symbol, side="buy", position_intent="buy_to_open"),
            SpreadLeg(symbol=short_c.symbol, side="sell", position_intent="sell_to_open"),
        ],
    )
