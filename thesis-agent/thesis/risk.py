from __future__ import annotations

from dataclasses import dataclass

BASELINE_UNIVERSE = (
    "SPY",
    "QQQ",
    "IWM",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "TSLA",
)

# Broad-market ETFs plus every GICS sector. Each sector has a liquid ETF and
# at least one liquid stock so expanded scouting is diversified without using
# option data during the first-stage stock ranking.
SECTOR_UNIVERSE = (
    ("Broad market", ("SPY", "QQQ", "IWM")),
    ("Communication Services", ("META", "GOOGL", "XLC")),
    ("Consumer Discretionary", ("AMZN", "TSLA", "XLY")),
    ("Consumer Staples", ("COST", "XLP")),
    ("Energy", ("XOM", "XLE")),
    ("Financials", ("JPM", "XLF")),
    ("Health Care", ("LLY", "XLV")),
    ("Industrials", ("CAT", "XLI")),
    ("Information Technology", ("AAPL", "MSFT", "NVDA", "XLK")),
    ("Materials", ("FCX", "XLB")),
    ("Real Estate", ("PLD", "XLRE")),
    ("Utilities", ("NEE", "XLU")),
)

_SECTOR_SYMBOLS = tuple(
    symbol for _, symbols in SECTOR_UNIVERSE for symbol in symbols
)
EXPANDED_UNIVERSE = BASELINE_UNIVERSE + tuple(
    symbol for symbol in _SECTOR_SYMBOLS if symbol not in BASELINE_UNIVERSE
)
ALLOWLIST = EXPANDED_UNIVERSE

MIN_DTE = 14
MAX_DTE = 45
MAX_OPEN_THESES = 3
PER_THESIS_EQUITY_PCT = 0.02
AGGREGATE_EQUITY_PCT = 0.06
MIN_CONVICTION = 0.35


class RiskError(ValueError):
    pass


def symbols_for_scout(profile: str) -> tuple[str, ...]:
    if profile == "baseline":
        return BASELINE_UNIVERSE
    if profile == "expanded":
        return EXPANDED_UNIVERSE
    raise ValueError(f"unknown scout universe profile: {profile}")


@dataclass(frozen=True)
class RiskSnapshot:
    equity: float
    open_theses: int
    debit_at_risk: float


def size_qty(equity: float, debit_per_spread: float, conviction: float) -> int:
    """Contracts such that debit paid <= 2% equity, scaled by conviction. Min 1 if affordable."""
    if debit_per_spread <= 0:
        raise RiskError("debit must be positive")
    budget = equity * PER_THESIS_EQUITY_PCT * max(conviction, MIN_CONVICTION)
    qty = int(budget // (debit_per_spread * 100))
    return max(qty, 0)


def check_open(
    snap: RiskSnapshot,
    *,
    underlying: str,
    dte: int,
    debit_usd: float,
    conviction: float,
) -> None:
    if underlying not in ALLOWLIST:
        raise RiskError(f"{underlying} not in allowlist")
    if not (MIN_DTE <= dte <= MAX_DTE):
        raise RiskError(f"DTE {dte} outside {MIN_DTE}-{MAX_DTE}")
    if conviction < MIN_CONVICTION:
        raise RiskError("conviction too low")
    if snap.open_theses >= MAX_OPEN_THESES:
        raise RiskError("max open theses")
    cap = snap.equity * PER_THESIS_EQUITY_PCT
    if debit_usd > cap + 1e-6:
        raise RiskError(f"debit ${debit_usd:.0f} exceeds 2% cap ${cap:.0f}")
    if snap.debit_at_risk + debit_usd > snap.equity * AGGREGATE_EQUITY_PCT + 1e-6:
        raise RiskError("aggregate debit cap")
