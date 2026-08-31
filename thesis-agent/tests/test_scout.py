from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from alpaca.trading.enums import ContractType

from thesis.config import ConfigError, Settings
from thesis.models import Side
from thesis.observe import MarketSnapshot
from thesis.risk import (
    ALLOWLIST,
    BASELINE_UNIVERSE,
    EXPANDED_UNIVERSE,
    SECTOR_UNIVERSE,
)
from thesis.scout import compare_stock_candidates, rank_snapshots, scout_market


def _snap(symbol: str, strength: int) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        spot=100,
        sma5=99 - strength,
        sma20=98 - strength,
        ret_5d_pct=float(strength),
        realized_vol_20d=0.25,
        regime="uptrend/normal_vol",
        avg_dollar_volume_20d=1_000_000_000,
    )


class _Options:
    def __init__(self, quote_mode="healthy"):
        self.calls = []
        self.quote_mode = quote_mode

    def get_option_latest_quote(self, request):
        self.calls.append(list(request.symbol_or_symbols))
        if self.quote_mode == "missing":
            bid, ask = None, 2.0
        elif self.quote_mode == "wide":
            bid, ask = 0.5, 2.0
        elif self.quote_mode == "at_limit":
            bid, ask = 1.9, 2.1
        elif self.quote_mode == "just_over_limit":
            bid, ask = 1.899998, 2.100002
        else:
            bid, ask = 1.8, 2.0
        return {
            symbol: SimpleNamespace(
                ask_price=ask if symbol.endswith("L") else 1.1,
                bid_price=bid if symbol.endswith("L") else 1.0,
            )
            for symbol in request.symbol_or_symbols
        }


class _Client:
    def __init__(
        self,
        failures=(),
        quote_mode="healthy",
        max_option_bid_ask_pct=25,
    ):
        self.failures = set(failures)
        self.calls = []
        self.options_data = _Options(quote_mode)
        self.settings = SimpleNamespace(
            min_avg_dollar_volume=50_000_000,
            max_option_bid_ask_pct=max_option_bid_ask_pct,
        )

    def option_chain(self, symbol, *, option_type, min_dte, max_dte):
        self.calls.append((symbol, option_type, min_dte, max_dte))
        if symbol in self.failures:
            raise RuntimeError("Authorization Bearer should-never-be-persisted")
        exp = date.today() + timedelta(days=25)
        suffix = "C" if option_type is ContractType.CALL else "P"
        contracts = [
            SimpleNamespace(
                tradable=True,
                expiration_date=exp,
                strike_price=str(strike),
                symbol=f"{symbol}{suffix}{leg}",
            )
            for strike, leg in ((100, "L"), (105, "S"), (95, "S"))
        ]
        return SimpleNamespace(option_contracts=contracts)


def test_stock_ranking_is_deterministic_with_symbol_tie_breaking() -> None:
    rows = rank_snapshots([_snap("ZZZ", 1), _snap("AAA", 1)])

    assert [row["symbol"] for row in rows] == ["AAA", "ZZZ"]
    assert rows[0]["factors"] == rows[1]["factors"]
    assert all(isinstance(value, float) for value in rows[0]["factors"].values())


def test_scout_probes_only_five_and_shortlists_only_three() -> None:
    snaps = [_snap(f"S{i}", 30 - i) for i in range(29)]
    client = _Client()

    result = scout_market(client, snaps)

    assert len(client.calls) == 10
    assert len(client.options_data.calls) == 10
    assert sum(row["probed"] for row in result.leaderboard) == 5
    assert len(result.shortlist) == 3
    assert result.stage_counts == {
        "stock_ranked": 29,
        "options_probe_budget": 5,
        "options_probe_candidates": 5,
        "options_probed": 5,
        "shortlist_budget": 3,
        "shortlist_size": 3,
    }
    assert result.duration_ms >= 0
    assert result.stock_rank_duration_ms >= 0
    assert result.options_probe_duration_ms >= 0
    assert {call[1] for call in client.calls} == {
        ContractType.CALL,
        ContractType.PUT,
    }
    assert all(
        call[2:] == (14, 45)
        for call in client.calls
    )
    shortlisted = [
        row for row in result.leaderboard if row["status"] == "feasible"
    ]
    assert all(
        0 <= float(leg["bid_ask_pct"]) <= 25
        for row in shortlisted
        for legs in row["option_liquidity"].values()
        for leg in legs
    )
    assert all(
        isinstance(leg["bid_price"], float)
        and isinstance(leg["ask_price"], float)
        and isinstance(leg["bid_ask_pct"], float)
        for row in shortlisted
        for legs in row["option_liquidity"].values()
        for leg in legs
    )


def test_expanded_allowlist_is_sector_balanced_and_keeps_baseline_prefix() -> None:
    sectors = {name for name, _ in SECTOR_UNIVERSE}

    assert EXPANDED_UNIVERSE == ALLOWLIST
    assert EXPANDED_UNIVERSE[: len(BASELINE_UNIVERSE)] == BASELINE_UNIVERSE
    assert len(EXPANDED_UNIVERSE) == 29
    assert sectors == {
        "Broad market",
        "Communication Services",
        "Consumer Discretionary",
        "Consumer Staples",
        "Energy",
        "Financials",
        "Health Care",
        "Industrials",
        "Information Technology",
        "Materials",
        "Real Estate",
        "Utilities",
    }
    assert all(symbol in ALLOWLIST for _, symbols in SECTOR_UNIVERSE for symbol in symbols)


def test_scout_universe_setting_accepts_only_documented_profiles(
    monkeypatch,
) -> None:
    monkeypatch.setenv("THESIS_ACCOUNT_PROFILE", "judge")
    monkeypatch.setenv("APCA_API_KEY_ID", "paper-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "paper-secret")
    monkeypatch.delenv("THESIS_SCOUT_UNIVERSE", raising=False)

    assert Settings().scout_universe == "expanded"

    monkeypatch.setenv("THESIS_SCOUT_UNIVERSE", "expanded")

    assert Settings().scout_universe == "expanded"

    monkeypatch.setenv("THESIS_SCOUT_UNIVERSE", "everything")
    with pytest.raises(ConfigError, match="baseline, expanded"):
        Settings()


def test_candidate_quality_compares_expanded_top_five_to_baseline() -> None:
    snaps = [_snap(symbol, 1) for symbol in BASELINE_UNIVERSE]
    snaps.extend([_snap("XLE", 10), _snap("XLF", 9)])

    comparison = compare_stock_candidates(rank_snapshots(snaps))

    assert comparison["baseline_ranked_count"] == 10
    assert comparison["active_ranked_count"] == 12
    assert comparison["active_top_symbol"] == "XLE"
    assert comparison["top_five_avg_stock_score_delta"] > 0
    assert comparison["new_top_five_symbols"] == ["XLE", "XLF"]


def test_scout_failure_reason_is_fixed_and_sanitized() -> None:
    snaps = [_snap(f"S{i}", 10 - i) for i in range(10)]
    client = _Client(failures={"S0"})

    result = scout_market(client, snaps)
    failed = next(row for row in result.leaderboard if row["symbol"] == "S0")

    assert failed["status"] == "infeasible"
    assert failed["reason"] == "options_data_unavailable"
    assert "Bearer" not in str(result.leaderboard)


def test_scout_rejects_missing_two_sided_option_quotes() -> None:
    client = _Client(quote_mode="missing")

    result = scout_market(client, [_snap("SPY", 5)])

    assert result.shortlist == []
    assert result.leaderboard[0]["status"] == "infeasible"
    assert result.leaderboard[0]["reason"] == "option_liquidity_failed"
    assert result.leaderboard[0]["option_liquidity"] == {
        "bullish": [],
        "bearish": [],
    }


def test_scout_rejects_abnormally_wide_option_quotes() -> None:
    client = _Client(quote_mode="wide")

    result = scout_market(client, [_snap("SPY", 5)])

    row = result.leaderboard[0]
    assert result.shortlist == []
    assert row["reason"] == "option_liquidity_failed"
    assert all(
        25 < float(leg["bid_ask_pct"]) <= 200
        for legs in row["option_liquidity"].values()
        for leg in legs
        if str(leg["symbol"]).endswith("L")
    )


def test_scout_enforces_configured_option_width_without_rounding() -> None:
    at_limit = scout_market(
        _Client(quote_mode="at_limit", max_option_bid_ask_pct=10),
        [_snap("SPY", 5)],
    )
    just_over = scout_market(
        _Client(quote_mode="just_over_limit", max_option_bid_ask_pct=10),
        [_snap("SPY", 5)],
    )

    assert [snap.symbol for snap in at_limit.shortlist] == ["SPY"]
    assert just_over.shortlist == []
    assert just_over.leaderboard[0]["reason"] == "option_liquidity_failed"


def test_scout_rejects_low_stock_dollar_volume_before_option_probe() -> None:
    snap = _snap("SPY", 5)
    snap.avg_dollar_volume_20d = 10_000_000
    client = _Client()

    result = scout_market(client, [snap])

    assert result.shortlist == []
    assert client.calls == []
    assert client.options_data.calls == []
    assert result.leaderboard[0]["probed"] is False
    assert result.leaderboard[0]["reason"] == "insufficient_stock_liquidity"