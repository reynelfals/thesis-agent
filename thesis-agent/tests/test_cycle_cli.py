from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import thesis.cycle as cycle_module
from thesis.models import Side, SpreadLeg, Structure, Thesis
from thesis.observe import MarketSnapshot
from thesis.store import ThesisStore
from thesis.tools.cli import CliResult


class Settings:
    def __init__(self, db_path, *, allow_execute=True):
        self.db_path = db_path
        self.allow_execute = allow_execute
        self.base_url = "https://paper-api.alpaca.markets"

    def assert_paper(self) -> None:
        return None


class FakeClient:
    sdk_order_calls = 0

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

    def order(self, order_id):
        return {
            "id": order_id,
            "status": "accepted",
            "qty": "1",
            "filled_qty": "0",
            "legs": [],
        }

    def place_debit_vertical(self, **kwargs):
        FakeClient.sdk_order_calls += 1
        raise AssertionError("SDK order fallback must never be called")


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


def _cli(args, *, ok=True, parsed=None, stderr="") -> CliResult:
    return CliResult(
        argv=["/home/runner/go/bin/alpaca", *args],
        returncode=0 if ok else 1,
        stdout="{}" if ok else "",
        stderr=stderr,
        ok=ok,
        parsed=parsed or {},
    )


@pytest.fixture
def cycle_fakes(monkeypatch):
    FakeClient.sdk_order_calls = 0
    monkeypatch.setattr(cycle_module, "PaperClient", FakeClient)
    monkeypatch.setattr(cycle_module, "alpaca_bin", lambda: "/home/runner/go/bin/alpaca")
    monkeypatch.setattr(
        cycle_module,
        "universe",
        lambda client: [
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
    )
    monkeypatch.setattr(cycle_module, "draft_thesis", lambda settings, snaps: _thesis())
    monkeypatch.setattr(
        cycle_module,
        "build_debit_vertical",
        lambda *args, **kwargs: _structure(),
    )
    monkeypatch.setattr(cycle_module, "check_open", lambda *args, **kwargs: None)


def test_disabled_execution_records_exact_cli_intent(
    monkeypatch, tmp_path, cycle_fakes
) -> None:
    calls = []

    def run_cli(settings, args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "account":
            return _cli(
                args,
                parsed={
                    "account_number": "123456789",
                    "status": "ACTIVE",
                    "options_trading_level": 3,
                },
            )
        return _cli(args, parsed={"is_open": True})

    monkeypatch.setattr(cycle_module, "run_alpaca_cli", run_cli)

    result = cycle_module.run_cycle(Settings(tmp_path / "audit.sqlite"), execute=False)

    assert result.decision == "blocked"
    assert len(calls) == 2
    intent = next(trace for trace in result.traces if trace["step"] == "blocked order intent")
    assert "alpaca api POST /v2/orders" in intent["status"]
    assert "SPY260918C00600000" in intent["status"]
    assert '"type":"limit"' in intent["status"]
    assert '"time_in_force":"day"' in intent["status"]
    assert '"ratio_qty":"1"' in intent["status"]
    assert '"position_intent":"buy_to_open"' in intent["status"]
    assert '"position_intent":"sell_to_open"' in intent["status"]
    assert "not submitted" in intent["status"]
    assert FakeClient.sdk_order_calls == 0


def test_execute_argument_cannot_override_disabled_setting(
    monkeypatch,
    tmp_path,
    cycle_fakes,
) -> None:
    calls = []

    def run_cli(settings, args, **kwargs):
        calls.append(args)
        if args[0] == "account":
            return _cli(
                args,
                parsed={
                    "account_number": "123456789",
                    "status": "ACTIVE",
                    "options_trading_level": 3,
                },
            )
        return _cli(args, parsed={"is_open": True})

    monkeypatch.setattr(cycle_module, "run_alpaca_cli", run_cli)

    result = cycle_module.run_cycle(
        Settings(tmp_path / "audit.sqlite", allow_execute=False),
        execute=True,
    )

    assert result.decision == "blocked"
    assert len(calls) == 2
    assert any(
        gate["name"] == "execute_enabled" and not gate["ok"]
        for gate in result.gates
    )
    assert FakeClient.sdk_order_calls == 0


def test_cli_submission_failure_never_falls_back_to_sdk(
    monkeypatch, tmp_path, cycle_fakes
) -> None:
    def run_cli(settings, args, **kwargs):
        if args[0] == "account":
            return _cli(
                args,
                parsed={
                    "account_number": "123456789",
                    "status": "ACTIVE",
                    "options_trading_level": 3,
                },
            )
        if args[0] == "clock":
            return _cli(args, parsed={"is_open": True})
        return _cli(args, ok=False, stderr="paper order rejected")

    monkeypatch.setattr(cycle_module, "run_alpaca_cli", run_cli)

    result = cycle_module.run_cycle(Settings(tmp_path / "audit.sqlite"), execute=True)

    assert result.decision == "blocked"
    assert result.thesis.order_id is None
    assert result.tool_path == "cli"
    assert FakeClient.sdk_order_calls == 0
    assert any(
        gate["name"] == "cli_order_submission" and not gate["ok"]
        for gate in result.gates
    )


def test_successful_order_is_attributed_to_cli(
    monkeypatch, tmp_path, cycle_fakes
) -> None:
    def run_cli(settings, args, **kwargs):
        if args[0] == "account":
            return _cli(
                args,
                parsed={
                    "account_number": "123456789",
                    "status": "ACTIVE",
                    "options_trading_level": 3,
                },
            )
        if args[0] == "clock":
            return _cli(args, parsed={"is_open": True})
        return _cli(
            args,
            parsed={
                "id": "paper-order-123",
                "status": "accepted",
                "qty": "1",
                "filled_qty": "0",
                "legs": [],
            },
        )

    monkeypatch.setattr(cycle_module, "run_alpaca_cli", run_cli)

    result = cycle_module.run_cycle(Settings(tmp_path / "audit.sqlite"), execute=True)

    assert result.decision == "submitted"
    assert result.thesis.order_id == "paper-order-123"
    assert result.tool_path == "cli"
    assert FakeClient.sdk_order_calls == 0
    assert any(
        gate["name"] == "cli_order_submission" and gate["ok"]
        for gate in result.gates
    )


@pytest.mark.parametrize(
    ("account_parsed", "clock_parsed"),
    [
        ({}, {"is_open": True}),
        (
            {
                "account_number": "123456789",
                "status": "INACTIVE",
                "options_trading_level": 3,
            },
            {"is_open": True},
        ),
        (
            {
                "account_number": "123456789",
                "status": "ACTIVE",
                "trading_blocked": True,
                "options_trading_level": 3,
            },
            {"is_open": True},
        ),
        (
            {
                "account_number": "123456789",
                "status": "ACTIVE",
                "options_trading_level": 2,
            },
            {"is_open": True},
        ),
        (
            {
                "account_number": "123456789",
                "status": "ACTIVE",
                "options_trading_level": 3,
            },
            {},
        ),
        (
            {
                "account_number": "123456789",
                "status": "ACTIVE",
                "options_trading_level": 3,
            },
            {"is_open": "true"},
        ),
        (
            {
                "account_number": "123456789",
                "status": "ACTIVE",
                "options_trading_level": 3,
            },
            {"is_open": False},
        ),
    ],
)
def test_invalid_or_disagreeing_cli_state_fails_closed(
    monkeypatch,
    tmp_path,
    cycle_fakes,
    account_parsed,
    clock_parsed,
) -> None:
    calls = []

    def run_cli(settings, args, **kwargs):
        calls.append(args)
        if args[0] == "account":
            return _cli(args, parsed=account_parsed)
        return _cli(args, parsed=clock_parsed)

    monkeypatch.setattr(cycle_module, "run_alpaca_cli", run_cli)

    result = cycle_module.run_cycle(Settings(tmp_path / "audit.sqlite"), execute=True)

    assert result.decision == "blocked"
    assert result.thesis.order_id is None
    assert len(calls) == 2
    assert FakeClient.sdk_order_calls == 0
    assert any(
        gate["name"] in {"cli_account_get", "cli_clock"} and not gate["ok"]
        for gate in result.gates
    )


def test_broker_exception_text_is_not_persisted(
    monkeypatch,
    tmp_path,
    cycle_fakes,
) -> None:
    secret = "Authorization Bearer broker-secret account_number=123456789"

    def fail_history(self):
        raise RuntimeError(secret)

    def run_cli(settings, args, **kwargs):
        if args[0] == "account":
            return _cli(
                args,
                parsed={
                    "account_number": "123456789",
                    "status": "ACTIVE",
                    "options_trading_level": 3,
                },
            )
        return _cli(args, parsed={"is_open": True})

    monkeypatch.setattr(FakeClient, "portfolio_history", fail_history)
    monkeypatch.setattr(cycle_module, "run_alpaca_cli", run_cli)
    db_path = tmp_path / "audit.sqlite"

    result = cycle_module.run_cycle(Settings(db_path), execute=False)
    stored = ThesisStore(db_path).last_cycle()
    serialized = json.dumps({"result": result.traces, "stored": stored})

    assert "broker-secret" not in serialized
    assert "123456789" not in serialized
    assert "Performance snapshot unavailable" in serialized


def test_order_refresh_exception_text_is_not_persisted(
    monkeypatch,
    tmp_path,
    cycle_fakes,
) -> None:
    secret = "Authorization Bearer broker-secret account_number=123456789"

    def fail_order(self, order_id):
        raise RuntimeError(secret)

    def run_cli(settings, args, **kwargs):
        if args[0] == "account":
            return _cli(
                args,
                parsed={
                    "account_number": "123456789",
                    "status": "ACTIVE",
                    "options_trading_level": 3,
                },
            )
        if args[0] == "clock":
            return _cli(args, parsed={"is_open": True})
        return _cli(
            args,
            parsed={
                "id": "paper-order-123",
                "status": "accepted",
                "qty": "1",
                "filled_qty": "0",
                "legs": [],
            },
        )

    monkeypatch.setattr(FakeClient, "order", fail_order)
    monkeypatch.setattr(cycle_module, "run_alpaca_cli", run_cli)
    db_path = tmp_path / "audit.sqlite"

    result = cycle_module.run_cycle(Settings(db_path), execute=True)
    stored = ThesisStore(db_path).last_cycle()
    serialized = json.dumps({"result": result.traces, "stored": stored})

    assert "broker-secret" not in serialized
    assert "123456789" not in serialized
    assert "status refresh unavailable" in serialized