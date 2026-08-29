from __future__ import annotations

import json
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

import thesis.web.dashboard as dashboard_module
from thesis.models import OrderSnapshot, Side, Thesis
from thesis.store import ThesisStore
from thesis.web.app import create_app
from thesis.web.dashboard import (
    MAX_FILL_ACTIVITY_PAGES,
    Dashboard,
)


class Settings:
    base_url = "https://paper-api.alpaca.markets"
    allow_execute = False
    demo_starting_equity = 100000.0

    def assert_paper(self) -> None:
        return None


class FakeClient:
    def __init__(self) -> None:
        self.account_calls = 0
        self.order_calls: list[str] = []
        self.fill_page_limits: list[int | None] = []
        self.fail = False

    def account(self):
        self.account_calls += 1
        if self.fail:
            raise RuntimeError("secret credential and raw upstream response")
        return SimpleNamespace(
            equity="100010",
            last_equity="100000",
            cash="100010",
            buying_power="200020",
            options_buying_power="100010",
            accrued_fees="0",
            status="ACTIVE",
            options_trading_level=3,
            account_number="123456789",
        )

    def clock(self):
        return SimpleNamespace(is_open=False)

    def positions(self):
        return []

    def portfolio_history(self):
        return SimpleNamespace(
            base_value=100000,
            cashflow={},
            timestamp=[1767225600],
            equity=[100010],
            profit_loss=[10],
        )

    def fill_activities(self, *, max_pages: int | None = None):
        self.fill_page_limits.append(max_pages)
        return []

    def order(self, order_id: str):
        self.order_calls.append(order_id)
        return {"id": order_id, "status": "filled", "qty": "1", "filled_qty": "1"}


def test_first_snapshot_is_loading_and_refresh_uses_audit_calculations(tmp_path) -> None:
    client = FakeClient()
    store = ThesisStore(tmp_path / "audit.sqlite")
    dashboard = Dashboard(
        settings=Settings(),
        store=store,
        client=client,
    )

    initial = dashboard.snapshot()
    assert initial["status"] == "loading"
    assert initial["generated_at"] is None
    assert client.account_calls == 0

    dashboard.refresh()
    result = dashboard.snapshot()
    assert result["status"] == "ready"
    assert result["performance"]["total_pl"] == 10
    assert result["readiness"]["account_suffix"] == "6789"
    assert "123456789" not in str(result)
    assert client.fill_page_limits == [MAX_FILL_ACTIVITY_PAGES]
    assert store.performance_history() == []


def test_refresh_failure_preserves_success_as_sanitized_stale_data(tmp_path) -> None:
    client = FakeClient()
    dashboard = Dashboard(
        settings=Settings(),
        store=ThesisStore(tmp_path / "audit.sqlite"),
        client=client,
    )
    dashboard.refresh()
    generated_at = dashboard.snapshot()["generated_at"]

    client.fail = True
    dashboard.refresh()
    result = dashboard.snapshot()

    assert result["status"] == "stale"
    assert result["generated_at"] == generated_at
    assert result["performance"]["equity"] == 100010
    assert "secret" not in result["error"]


def test_cycle_serialization_excludes_commands_environments_and_secrets(tmp_path) -> None:
    client = FakeClient()
    store = ThesisStore(tmp_path / "audit.sqlite")
    store.save_cycle(
        {
            "id": "cycle-1",
            "at": "2026-01-01T10:00:00Z",
            "decision": "blocked",
            "tool_path": "cli",
            "thesis": {
                "id": "thesis-1",
                "underlying": "SPY",
                "notes": "account 123456789 token=top-secret",
                "invalidation": "Never expose secret-invalidation-value",
                "cli_commands": ["alpaca orders cancel --all"],
                "monitoring": {
                    "exit_status": "monitoring",
                    "exit_reason": "secret-monitoring-value",
                },
            },
            "traces": [
                {
                    "tool": "alpaca",
                    "step": "account",
                    "ok": False,
                    "status": "api_key=top-secret",
                    "argv": ["alpaca", "orders", "cancel", "--all"],
                    "stderr": "APCA_API_SECRET_KEY=top-secret",
                    "environment": {"TOKEN": "top-secret"},
                }
            ],
        }
    )
    dashboard = Dashboard(settings=Settings(), store=store, client=client)

    dashboard.refresh()
    serialized = json.dumps(dashboard.snapshot())

    assert "cancel --all" not in serialized
    assert "top-secret" not in serialized
    assert "secret-invalidation-value" not in serialized
    assert "secret-monitoring-value" not in serialized
    assert "123456789" not in serialized
    assert "[REDACTED]" in serialized


def test_http_routes_static_assets_and_cycle_contract(tmp_path) -> None:
    client = FakeClient()
    store = ThesisStore(tmp_path / "audit.sqlite")
    store.save_cycle(
        {
            "id": "cycle-contract",
            "at": "2026-01-01T10:00:00Z",
            "decision": "blocked",
            "tool_path": "cli",
            "gates": [
                {
                    "at": "2026-01-01T10:00:00Z",
                    "name": "risk",
                    "ok": False,
                    "detail": "risk limit",
                }
            ],
            "traces": [
                {
                    "at": "2026-01-01T10:00:00Z",
                    "tool": "alpaca",
                    "step": "account",
                    "ok": True,
                    "status": "paper",
                }
            ],
            "thesis": {"id": "thesis-contract", "underlying": "SPY"},
        }
    )
    dashboard = Dashboard(settings=Settings(), store=store, client=client)
    dashboard.refresh()

    with TestClient(create_app(dashboard)) as http:
        index = http.get("/")
        brief = http.get("/brief")
        favicon = http.get("/favicon.ico")
        css = http.get("/assets/styles.css")
        javascript = http.get("/assets/app.js")
        response = http.get("/api/dashboard")
        health = http.get("/api/health")

    assert index.status_code == 200
    assert "Thesis / Audit Console" in index.text
    assert brief.status_code == 200
    assert "An options agent that must show its work." in brief.text
    assert "GROK PROPOSES" in brief.text
    assert "ALPACA PROVES" in brief.text
    assert "Paper trading only" in brief.text
    assert "http://" not in brief.text
    assert "https://" not in brief.text
    assert favicon.status_code == 204
    assert css.status_code == 200
    assert "--paper" in css.text
    assert javascript.status_code == 200
    assert 'use strict' in javascript.text
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    cycle = next(item for item in payload["cycles"] if item["id"] == "cycle-contract")
    assert cycle["gates"][0] == {
        "at": "2026-01-01T10:00:00Z",
        "name": "risk",
        "status": "fail",
        "evidence": "risk limit",
    }
    assert cycle["traces"][0] == {
        "at": "2026-01-01T10:00:00Z",
        "tool": "alpaca",
        "step": "account",
        "status": "pass",
        "evidence": "paper",
    }
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json()["status"] == "ready"


def test_terminal_order_snapshot_is_not_reloaded_from_broker(tmp_path) -> None:
    client = FakeClient()
    store = ThesisStore(tmp_path / "audit.sqlite")
    store.save_order_snapshot(
        "thesis-1",
        OrderSnapshot(order_id="terminal-order", status="filled"),
    )
    store.upsert(
        Thesis(
            id="thesis-1",
            underlying="SPY",
            side=Side.BULLISH,
            regime="uptrend",
            setup="breakout",
            invalidation="close below support",
            horizon="21 days",
            expected_move_pct=2,
            iv_note="normal",
            conviction=0.7,
            order_id="terminal-order",
            decision="submitted",
        )
    )
    dashboard = Dashboard(settings=Settings(), store=store, client=client)

    dashboard.refresh()

    assert dashboard.snapshot()["status"] == "ready"
    assert client.order_calls == []
    assert store.get("thesis-1").monitoring is None


def test_live_order_refresh_cap_fails_before_partial_order_reads(
    tmp_path, monkeypatch
) -> None:
    client = FakeClient()
    store = ThesisStore(tmp_path / "audit.sqlite")
    store.upsert(
        Thesis(
            underlying="SPY",
            side=Side.BULLISH,
            regime="uptrend",
            setup="breakout",
            invalidation="close below support",
            horizon="21 days",
            expected_move_pct=2,
            iv_note="normal",
            conviction=0.7,
            order_id="missing-order",
            decision="submitted",
        )
    )
    monkeypatch.setattr(dashboard_module, "MAX_LIVE_ORDER_REFRESHES", 0)
    dashboard = Dashboard(settings=Settings(), store=store, client=client)

    dashboard.refresh()

    assert dashboard.snapshot()["status"] == "error"
    assert client.order_calls == []


def test_daemon_refresh_loop_starts_without_blocking_caller(tmp_path) -> None:
    client = FakeClient()
    dashboard = Dashboard(
        settings=Settings(),
        store=ThesisStore(tmp_path / "audit.sqlite"),
        client=client,
        refresh_interval_seconds=1,
    )
    started = time.monotonic()
    dashboard.start()
    try:
        assert time.monotonic() - started < 0.2
        assert dashboard._thread is not None and dashboard._thread.daemon
    finally:
        dashboard.stop()