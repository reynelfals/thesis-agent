from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import thesis.cycle as cycle_module
from thesis.llm import AgentDraft
from thesis.models import Side, SpreadLeg, Structure, Thesis
from thesis.observe import MarketSnapshot
from thesis.store import ThesisStore
from thesis.tools.mcp import McpError, McpToolResult


class Settings:
    def __init__(self, db_path, *, allow_execute=True):
        self.db_path = db_path
        self.allow_execute = allow_execute
        self.base_url = "https://paper-api.alpaca.markets"
        self.scout_universe = "baseline"

    def assert_paper(self) -> None:
        return None


class FakeClient:
    def __init__(self, settings) -> None:
        self.settings = settings

    def account(self):
        return SimpleNamespace(
            equity="100000",
            last_equity="100000",
            cash="100000",
            buying_power="200000",
            options_buying_power="100000",
            accrued_fees="0",
        )

    def clock(self):
        return SimpleNamespace(is_open=True)

    def positions(self):
        return []

    def portfolio_history(self):
        return SimpleNamespace(
            base_value="100000",
            cashflow={},
            timestamp=[],
            equity=[],
            profit_loss=[],
        )

    def fill_activities(self):
        return []


class FakeMcp:
    instances = []
    account_data = {
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
        "options_trading_level": 3,
    }
    clock_data = {"is_open": True}
    fail_submit = False
    fail_refresh = False

    def __init__(self, settings) -> None:
        self.settings = settings
        self.traces = [
            {
                "at": "2026-08-29T12:00:00+00:00",
                "tool": "Alpaca MCP",
                "step": "server start",
                "ok": True,
                "status": "Official paper MCP server started",
            }
        ]
        self.submissions = []
        self.system_calls = []
        FakeMcp.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def call_system_tool(self, name, args):
        self.system_calls.append((name, args))
        if name == "get_account_info":
            data = dict(FakeMcp.account_data)
        elif name == "get_clock":
            data = dict(FakeMcp.clock_data)
        elif name == "get_order_by_id":
            if FakeMcp.fail_refresh:
                raise McpError(
                    "Authorization Bearer broker-secret account_number=123456789"
                )
            data = {
                "id": args["order_id"],
                "status": "accepted",
                "qty": "1",
                "filled_qty": "0",
                "legs": [],
            }
        else:
            raise McpError("unexpected system tool")
        self.traces.append(
            {
                "at": "2026-08-29T12:00:00+00:00",
                "tool": "Alpaca MCP",
                "step": name,
                "ok": True,
                "status": "Alpaca MCP response received",
            }
        )
        return McpToolResult(name, True, data, "Alpaca MCP response received")

    async def place_option_order(self, payload):
        self.submissions.append(payload)
        self.traces.append(
            {
                "at": "2026-08-29T12:00:00+00:00",
                "tool": "Alpaca MCP",
                "step": "place_option_order",
                "ok": not FakeMcp.fail_submit,
                "status": (
                    "Alpaca MCP call failed"
                    if FakeMcp.fail_submit
                    else "Alpaca MCP response received"
                ),
            }
        )
        if FakeMcp.fail_submit:
            raise McpError("ambiguous paper submission")
        return McpToolResult(
            "place_option_order",
            True,
            {
                "id": "paper-order-123",
                "status": "accepted",
                "qty": "1",
                "filled_qty": "0",
                "legs": [],
            },
            "Alpaca MCP response received",
        )


def _thesis() -> Thesis:
    return Thesis(
        underlying="SPY",
        side=Side.BULLISH,
        regime="uptrend",
        setup="Breakout",
        invalidation="Close below SMA20",
        horizon="21-45 DTE",
        expected_move_pct=2,
        iv_note="acceptable",
        conviction=0.8,
    )


def _structure() -> Structure:
    return Structure(
        underlying="SPY",
        long_symbol="SPY260918C00600000",
        short_symbol="SPY260918C00605000",
        expiration="2026-09-18",
        long_strike=600,
        short_strike=605,
        dte=21,
        debit_limit=2,
        qty=1,
        max_loss_usd=200,
        legs=[
            SpreadLeg(
                symbol="SPY260918C00600000",
                side="buy",
                position_intent="buy_to_open",
            ),
            SpreadLeg(
                symbol="SPY260918C00605000",
                side="sell",
                position_intent="sell_to_open",
            ),
        ],
    )


@pytest.fixture
def cycle_fakes(monkeypatch):
    FakeMcp.instances = []
    FakeMcp.account_data = {
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
        "options_trading_level": 3,
    }
    FakeMcp.clock_data = {"is_open": True}
    FakeMcp.fail_submit = False
    FakeMcp.fail_refresh = False
    monkeypatch.setattr(cycle_module, "PaperClient", FakeClient)
    monkeypatch.setattr(cycle_module, "AlpacaMcpSession", FakeMcp)
    monkeypatch.setattr(
        cycle_module,
        "observe_universe",
        lambda client: SimpleNamespace(
            snapshots=[
                MarketSnapshot(
                    symbol="SPY",
                    spot=600,
                    sma5=595,
                    sma20=590,
                    ret_5d_pct=1.2,
                    realized_vol_20d=0.15,
                    regime="uptrend/normal_vol",
                )
            ],
            failures=[],
        ),
    )

    async def draft(*args, **kwargs):
        return AgentDraft(
            thesis=_thesis(),
            traces=[
                {
                    "at": "2026-08-29T12:00:00+00:00",
                    "tool": "Grok MCP agent",
                    "step": "request_defined_risk_spread",
                    "ok": True,
                    "status": "Requested SPY bullish defined-risk spread",
                }
            ],
            research_calls=1,
        )

    monkeypatch.setattr(cycle_module, "draft_thesis", draft)
    monkeypatch.setattr(
        cycle_module,
        "scout_market",
        lambda client, snaps: SimpleNamespace(
            shortlist=snaps,
            shortlist_sides={snaps[0].symbol: ["bullish"]},
            leaderboard=[
                {
                    "symbol": snaps[0].symbol,
                    "stock_rank": 1,
                    "stock_score": 0.8,
                    "factors": {},
                    "regime": snaps[0].regime,
                    "probed": True,
                    "call_count": 10,
                    "put_count": 10,
                    "feasible_sides": ["bullish"],
                    "options_score": 0.05,
                    "total_score": 0.85,
                    "status": "feasible",
                    "reason": "valid_natural_debit",
                }
            ],
        ),
    )
    monkeypatch.setattr(
        cycle_module,
        "build_debit_vertical",
        lambda *args, **kwargs: _structure(),
    )
    monkeypatch.setattr(cycle_module, "check_open", lambda *args, **kwargs: None)


def test_disabled_execution_records_guarded_mcp_intent(
    tmp_path, cycle_fakes
) -> None:
    result = cycle_module.run_cycle(
        Settings(tmp_path / "audit.sqlite"),
        execute=False,
    )

    assert result.decision == "blocked"
    assert result.tool_path == "mcp"
    assert FakeMcp.instances[0].submissions == []
    intent = next(
        trace for trace in result.traces if trace["step"] == "guarded order intent"
    )
    assert "Alpaca MCP place_option_order" in intent["status"]
    assert "SPY260918C00600000" in intent["status"]
    assert '"type":"limit"' in intent["status"]
    assert '"position_intent":"buy_to_open"' in intent["status"]
    assert "not submitted" in intent["status"]
    assert any(g["name"] == "mcp_account" and g["ok"] for g in result.gates)
    assert any(g["name"] == "mcp_clock" and g["ok"] for g in result.gates)


def test_execute_argument_cannot_override_disabled_setting(
    tmp_path, cycle_fakes
) -> None:
    result = cycle_module.run_cycle(
        Settings(tmp_path / "audit.sqlite", allow_execute=False),
        execute=True,
    )

    assert result.decision == "blocked"
    assert FakeMcp.instances[0].submissions == []
    assert any(
        gate["name"] == "execute_enabled" and not gate["ok"]
        for gate in result.gates
    )


def test_successful_order_is_attributed_to_exactly_one_mcp_dispatch(
    tmp_path, cycle_fakes
) -> None:
    result = cycle_module.run_cycle(
        Settings(tmp_path / "audit.sqlite"),
        execute=True,
    )

    mcp = FakeMcp.instances[0]
    assert result.decision == "submitted"
    assert result.thesis.order_id == "paper-order-123"
    assert result.tool_path == "mcp"
    assert len(mcp.submissions) == 1
    payload = mcp.submissions[0]
    assert payload["order_class"] == "mleg"
    assert payload["type"] == "limit"
    assert payload["time_in_force"] == "day"
    assert payload["client_order_id"].startswith(
        f"thesis-{result.thesis.id}-"
    )
    assert any(
        gate["name"] == "mcp_order_submission" and gate["ok"]
        for gate in result.gates
    )
    assert not hasattr(FakeClient, "place_debit_vertical")


def test_ambiguous_mcp_submission_is_terminal_and_not_retried(
    tmp_path, cycle_fakes
) -> None:
    FakeMcp.fail_submit = True

    result = cycle_module.run_cycle(
        Settings(tmp_path / "audit.sqlite"),
        execute=True,
    )

    assert result.decision == "blocked"
    assert result.thesis.order_id is None
    assert len(FakeMcp.instances[0].submissions) == 1
    assert any(
        gate["name"] == "mcp_order_submission" and not gate["ok"]
        for gate in result.gates
    )
    assert "retry" in result.thesis.notes


@pytest.mark.parametrize(
    ("account_data", "clock_data"),
    [
        ({}, {"is_open": True}),
        (
            {"status": "INACTIVE", "options_trading_level": 3},
            {"is_open": True},
        ),
        (
            {
                "status": "ACTIVE",
                "trading_blocked": True,
                "options_trading_level": 3,
            },
            {"is_open": True},
        ),
        (
            {"status": "ACTIVE", "options_trading_level": 2},
            {"is_open": True},
        ),
        (
            {"status": "ACTIVE", "options_trading_level": 3},
            {},
        ),
        (
            {"status": "ACTIVE", "options_trading_level": 3},
            {"is_open": "true"},
        ),
        (
            {"status": "ACTIVE", "options_trading_level": 3},
            {"is_open": False},
        ),
    ],
)
def test_invalid_or_disagreeing_mcp_state_fails_closed(
    tmp_path,
    cycle_fakes,
    account_data,
    clock_data,
) -> None:
    FakeMcp.account_data = account_data
    FakeMcp.clock_data = clock_data

    result = cycle_module.run_cycle(
        Settings(tmp_path / "audit.sqlite"),
        execute=True,
    )

    assert result.decision == "blocked"
    assert result.thesis.order_id is None
    assert FakeMcp.instances[0].submissions == []
    assert any(
        gate["name"] in {"mcp_account", "mcp_clock"} and not gate["ok"]
        for gate in result.gates
    )


def test_mcp_initialization_failure_is_sanitized_and_persisted(
    monkeypatch, tmp_path
) -> None:
    secret = "Authorization Bearer mcp-secret account_number=123456789"

    class BrokenMcp:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            raise McpError(secret)

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(cycle_module, "AlpacaMcpSession", BrokenMcp)
    db_path = tmp_path / "audit.sqlite"

    result = cycle_module.run_cycle(Settings(db_path), execute=True)
    serialized = json.dumps(
        {
            "traces": result.traces,
            "stored": ThesisStore(db_path).last_cycle(),
        }
    )

    assert result.decision == "blocked"
    assert "mcp-secret" not in serialized
    assert "123456789" not in serialized
    assert "failed closed" in serialized


def test_order_refresh_failure_uses_submission_without_leaking_error(
    tmp_path, cycle_fakes
) -> None:
    FakeMcp.fail_refresh = True
    db_path = tmp_path / "audit.sqlite"

    result = cycle_module.run_cycle(Settings(db_path), execute=True)
    serialized = json.dumps(
        {
            "traces": result.traces,
            "stored": ThesisStore(db_path).last_cycle(),
        }
    )

    assert result.decision == "submitted"
    assert result.thesis.order_status == "accepted"
    assert "broker-secret" not in serialized
    assert "123456789" not in serialized
    assert "status refresh unavailable" in serialized


def test_no_feasible_scout_skips_grok_spread_and_order(
    monkeypatch, tmp_path, cycle_fakes
) -> None:
    monkeypatch.setattr(
        cycle_module,
        "scout_market",
        lambda client, snaps: SimpleNamespace(
            leaderboard=[
                {
                    "symbol": "SPY",
                    "regime": "uptrend/normal_vol",
                    "status": "infeasible",
                    "reason": "no_valid_vertical",
                    "feasible_sides": [],
                    "probed": True,
                }
            ],
            shortlist=[],
            shortlist_sides={},
        ),
    )

    async def fail_draft(*args, **kwargs):
        pytest.fail("Grok must not be called")

    monkeypatch.setattr(cycle_module, "draft_thesis", fail_draft)
    monkeypatch.setattr(
        cycle_module,
        "build_debit_vertical",
        lambda *args, **kwargs: pytest.fail("spread must not be built"),
    )

    result = cycle_module.run_cycle(
        Settings(tmp_path / "audit.sqlite"),
        execute=True,
    )

    assert result.decision == "no_trade"
    assert result.thesis.structure is None
    assert FakeMcp.instances[0].submissions == []


def test_llm_exception_is_sanitized_and_stops_before_spread(
    monkeypatch, tmp_path, cycle_fakes
) -> None:
    secret = "Authorization Bearer grok-provider-secret"

    async def fail_draft(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(cycle_module, "draft_thesis", fail_draft)
    monkeypatch.setattr(
        cycle_module,
        "build_debit_vertical",
        lambda *args, **kwargs: pytest.fail("spread must not be built"),
    )
    db_path = tmp_path / "audit.sqlite"

    result = cycle_module.run_cycle(Settings(db_path), execute=True)
    serialized = json.dumps(
        {
            "thesis": result.thesis.model_dump(mode="json"),
            "cycle": ThesisStore(db_path).last_cycle(),
        }
    )

    assert result.decision == "no_trade"
    assert secret not in serialized
    assert "provider output unavailable" in serialized.lower()


def test_expanded_scout_metrics_are_preserved(
    monkeypatch, tmp_path, cycle_fakes
) -> None:
    from thesis.risk import EXPANDED_UNIVERSE

    observed_symbols = []
    snaps = [
        MarketSnapshot(
            symbol=symbol,
            spot=600,
            sma5=595,
            sma20=590,
            ret_5d_pct=1.2,
            realized_vol_20d=0.15,
            regime="uptrend/normal_vol",
        )
        for symbol in EXPANDED_UNIVERSE
    ]

    def observe_expanded(client, symbols):
        observed_symbols.extend(symbols)
        return SimpleNamespace(
            snapshots=snaps,
            failures=[],
            duration_ms=29.0,
            symbol_durations_ms={symbol: 1.0 for symbol in symbols},
        )

    def scout_expanded(client, values):
        expanded_first = [
            *EXPANDED_UNIVERSE[len(cycle_module.BASELINE_UNIVERSE) :],
            *cycle_module.BASELINE_UNIVERSE,
        ]
        rows = [
            {
                "symbol": symbol,
                "stock_rank": rank,
                "stock_score": round(1.0 - rank / 100, 4),
                "regime": "uptrend/normal_vol",
                "probed": rank <= 5,
                "status": "feasible" if rank <= 3 else "not_probed",
                "feasible_sides": ["bullish"] if rank <= 3 else [],
            }
            for rank, symbol in enumerate(expanded_first, 1)
        ]
        by_symbol = {snap.symbol: snap for snap in values}
        shortlist = [by_symbol[symbol] for symbol in expanded_first[:3]]
        return SimpleNamespace(
            leaderboard=rows,
            shortlist=shortlist,
            shortlist_sides={
                snap.symbol: ["bullish"] for snap in shortlist
            },
            stage_counts={"stock_ranked": 29, "options_probed": 5},
            stock_rank_duration_ms=1.0,
            options_probe_duration_ms=5.0,
        )

    monkeypatch.setattr(cycle_module, "observe_universe", observe_expanded)
    monkeypatch.setattr(cycle_module, "scout_market", scout_expanded)
    times = iter((0.0, 0.029, 0.035))
    monkeypatch.setattr(cycle_module, "perf_counter", lambda: next(times))
    db_path = tmp_path / "audit.sqlite"
    ThesisStore(db_path).save_cycle(
        {
            "id": "baseline-reference",
            "at": "2026-08-29T12:00:00+00:00",
            "thesis": {"id": "baseline-thesis"},
            "traces": [
                {
                    "step": "rank and options feasibility",
                    "universe_profile": "baseline",
                    "duration_ms": 12.5,
                }
            ],
        }
    )
    settings = Settings(db_path, allow_execute=False)
    settings.scout_universe = "expanded"

    result = cycle_module.run_cycle(settings, execute=False)

    scout = next(
        trace
        for trace in result.traces
        if trace["step"] == "rank and options feasibility"
    )
    comparison = next(
        trace
        for trace in result.traces
        if trace["step"] == "ten-symbol baseline comparison"
    )
    assert len(observed_symbols) == 29
    assert scout["stock_observation_target"] == 29
    assert scout["options_probe_budget"] == 5
    assert scout["shortlist_budget"] == 3
    assert comparison["comparison_ready"] is True
    assert comparison["default_profile"] == "expanded"
    assert comparison["baseline_reference_duration_ms"] == 12.5
    assert comparison["full_scout_latency_delta_ms"] == 22.5
    assert comparison["top_five_avg_stock_score_delta"] > 0