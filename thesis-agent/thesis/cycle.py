from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from thesis.alpaca.client import PaperClient
from thesis.audit import (
    monitoring_snapshot,
    order_snapshot,
    performance_snapshot,
    position_snapshots,
)
from thesis.config import Settings
from thesis.llm import draft_thesis
from thesis.models import PerformanceSnapshot, Thesis, ThesisStatus
from thesis.observe import MarketSnapshot, universe
from thesis.risk import MIN_CONVICTION, RiskError, RiskSnapshot, check_open
from thesis.spread import SpreadError, build_debit_vertical
from thesis.store import ThesisStore
from thesis.tools.cli import CliResult, alpaca_bin, run_alpaca_cli


@dataclass
class CycleResult:
    at: str
    decision: str
    thesis: Thesis
    snapshots: list[dict[str, Any]]
    gates: list[dict[str, Any]]
    traces: list[dict[str, Any]]
    tool_path: str
    performance: PerformanceSnapshot | None = None
    error: str = ""


def _snap_dicts(snaps: list[MarketSnapshot]) -> list[dict[str, Any]]:
    return [asdict(s) for s in snaps]


def _gate(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "ok": ok,
        "detail": detail,
    }


def _trace(tool: str, step: str, **detail: Any) -> dict[str, Any]:
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "step": step,
        **detail,
    }


def _mleg_intent(thesis: Thesis) -> dict[str, Any]:
    assert thesis.structure is not None
    s = thesis.structure
    return {
        "order_class": "mleg",
        "qty": str(s.qty),
        "type": "limit",
        "limit_price": str(s.debit_limit),
        "time_in_force": "day",
        "legs": [
            {
                "symbol": s.long_symbol,
                "ratio_qty": "1",
                "side": "buy",
                "position_intent": "buy_to_open",
            },
            {
                "symbol": s.short_symbol,
                "ratio_qty": "1",
                "side": "sell",
                "position_intent": "sell_to_open",
            },
        ],
    }


def _mleg_payload(thesis: Thesis) -> dict[str, Any]:
    return {
        **_mleg_intent(thesis),
        "client_order_id": f"thesis-{thesis.id}-{uuid4().hex[:8]}",
    }


def _mleg_evidence(thesis: Thesis) -> str:
    public_intent = {
        **_mleg_intent(thesis),
        "client_order_id": "<generated-at-submit>",
    }
    return (
        "DRY RUN — alpaca api POST /v2/orders "
        + json.dumps(public_intent, separators=(",", ":"), sort_keys=True)
    )


def _cli_account_readiness(result: CliResult) -> tuple[bool, str]:
    if not result.ok:
        return False, result.as_trace()["status"]
    if not isinstance(result.parsed, dict):
        return False, "CLI account response was not valid JSON"
    status = str(result.parsed.get("status") or "").upper()
    if status != "ACTIVE":
        return False, f"CLI paper account is not active ({status or 'unknown'})"
    if result.parsed.get("account_blocked") or result.parsed.get("trading_blocked"):
        return False, "CLI paper account reports trading blocked"
    try:
        options_level = int(result.parsed.get("options_trading_level"))
    except (TypeError, ValueError):
        return False, "CLI account response has no valid options trading level"
    if options_level < 3:
        return False, f"CLI paper account options level {options_level}; level 3 required"
    return True, f"paper account ACTIVE; options level {options_level}"


def _cli_clock_readiness(
    result: CliResult,
    *,
    sdk_is_open: bool,
) -> tuple[bool, bool, str]:
    if not result.ok:
        return False, False, result.as_trace()["status"]
    if not isinstance(result.parsed, dict) or not isinstance(
        result.parsed.get("is_open"),
        bool,
    ):
        return False, False, "CLI clock response was not valid JSON"
    cli_is_open = result.parsed["is_open"]
    if cli_is_open != sdk_is_open:
        return (
            False,
            False,
            f"CLI/SDK clock mismatch (CLI={cli_is_open}, SDK={sdk_is_open}); blocked",
        )
    state = "open" if cli_is_open else "closed"
    return True, cli_is_open, f"paper market {state}; SDK agrees"


def run_cycle(settings: Settings, *, execute: bool | None = None) -> CycleResult:
    settings.assert_paper()
    allow = settings.allow_execute and (execute is not False)
    client = PaperClient(settings)
    store = ThesisStore(settings.db_path)
    traces: list[dict[str, Any]] = []
    gates: list[dict[str, Any]] = []

    acct_cli = run_alpaca_cli(settings, ["account", "get", "--quiet"])
    traces.append(_trace("alpaca CLI", "account get", **acct_cli.as_trace()))
    clock_cli = run_alpaca_cli(settings, ["clock", "--quiet"])
    traces.append(_trace("alpaca CLI", "clock", **clock_cli.as_trace()))

    account = client.account()
    clock = client.clock()
    account_cli_ready, account_cli_detail = _cli_account_readiness(acct_cli)
    clock_cli_ready, market_open, clock_cli_detail = _cli_clock_readiness(
        clock_cli,
        sdk_is_open=bool(clock.is_open),
    )
    cli_ok = bool(alpaca_bin()) and account_cli_ready and clock_cli_ready
    market_open = market_open and cli_ok
    tool_path = "cli" if cli_ok else "alpaca-py"
    positions = client.positions()
    performance = None
    fills: list[dict[str, Any]] = []
    try:
        history = client.portfolio_history()
        fills = client.fill_activities()
        performance = performance_snapshot(account, positions, history, fills)
        store.save_performance(performance)
        traces.append(
            _trace(
                "Alpaca Trading API",
                "performance snapshot",
                ok=True,
                fill_count=performance.fill_count,
                equity=performance.equity,
                realized_pl=performance.realized_pl,
                unrealized_pl=performance.unrealized_pl,
            )
        )
    except Exception:
        traces.append(
            _trace(
                "Alpaca Trading API",
                "performance snapshot",
                ok=False,
                status="Performance snapshot unavailable",
            )
        )
    snaps = universe(client)
    thesis = draft_thesis(settings, snaps)
    thesis.snapshots = _snap_dicts(snaps)
    thesis.tool_path = tool_path
    thesis.cli_commands = ["alpaca account get --quiet", "alpaca clock --quiet"]

    gates.append(_gate("paper_only", True, settings.base_url))
    gates.append(
        _gate(
            "cli_account_get",
            account_cli_ready,
            account_cli_detail,
        )
    )
    gates.append(
        _gate(
            "cli_clock",
            clock_cli_ready,
            clock_cli_detail,
        )
    )
    gates.append(
        _gate(
            "conviction",
            thesis.conviction >= MIN_CONVICTION,
            f"{thesis.conviction:.2f} (min {MIN_CONVICTION})",
        )
    )
    gates.append(
        _gate(
            "market_open",
            market_open,
            f"authoritative CLI state={market_open}",
        )
    )
    gates.append(_gate("execute_enabled", allow, f"THESIS_ALLOW_EXECUTE={allow}"))

    if thesis.conviction < MIN_CONVICTION:
        thesis.status = ThesisStatus.REJECTED
        thesis.decision = "no_trade"
        thesis.monitoring = monitoring_snapshot(
            thesis, position_snapshots(positions), None, fills=fills
        )
        thesis.exit_status = thesis.monitoring.exit_status
        thesis.exit_reason = thesis.monitoring.exit_reason
        return _complete(
            store, thesis, snaps, gates, traces, tool_path, performance
        )

    try:
        structure = build_debit_vertical(
            client,
            underlying=thesis.underlying,
            side=thesis.side,
            equity=float(account.equity),
            conviction=thesis.conviction,
        )
        thesis.structure = structure
        check_open(
            RiskSnapshot(
                equity=float(account.equity),
                open_theses=store.open_count(),
                debit_at_risk=store.debit_at_risk(),
            ),
            underlying=thesis.underlying,
            dte=structure.dte,
            debit_usd=structure.max_loss_usd,
            conviction=thesis.conviction,
        )
        gates.append(
            _gate(
                "spread_and_risk",
                True,
                f"{structure.long_symbol}/{structure.short_symbol} debit≤{structure.debit_limit} "
                f"qty={structure.qty} max_loss=${structure.max_loss_usd:.0f}",
            )
        )
    except (SpreadError, RiskError) as exc:
        gates.append(_gate("spread_and_risk", False, str(exc)))
        thesis.status = ThesisStatus.REJECTED
        thesis.decision = "rejected"
        thesis.notes = str(exc)
        thesis.monitoring = monitoring_snapshot(
            thesis, position_snapshots(positions), None, fills=fills
        )
        thesis.exit_status = thesis.monitoring.exit_status
        thesis.exit_reason = thesis.monitoring.exit_reason
        return _complete(
            store, thesis, snaps, gates, traces, tool_path, performance
        )

    if not market_open or not allow:
        thesis.status = ThesisStatus.DRAFT
        thesis.decision = "blocked"
        blocked_by = []
        if not market_open:
            blocked_by.append("CLI market gate closed or unavailable")
        if not allow:
            blocked_by.append("execution disabled")
        traces.append(
            _trace(
                "alpaca CLI",
                "blocked order intent",
                ok=True,
                status=f"{_mleg_evidence(thesis)}; not submitted ({', '.join(blocked_by)})",
            )
        )
        thesis.cli_commands.append(
            "alpaca api POST /v2/orders  # dry-run mleg payload retained as trace"
        )
        thesis.monitoring = monitoring_snapshot(
            thesis, position_snapshots(positions), None, fills=fills
        )
        thesis.exit_status = thesis.monitoring.exit_status
        thesis.exit_reason = thesis.monitoring.exit_reason
        return _complete(
            store, thesis, snaps, gates, traces, tool_path, performance
        )

    payload = _mleg_payload(thesis)
    gates.append(
        _gate(
            "cli_order_path",
            cli_ok,
            (
                "Alpaca CLI account and clock checks passed"
                if cli_ok
                else "Alpaca CLI is not ready; SDK order fallback is disabled"
            ),
        )
    )
    if not cli_ok:
        thesis.status = ThesisStatus.DRAFT
        thesis.decision = "blocked"
        thesis.notes = "Alpaca CLI unavailable; refusing SDK order fallback."
        traces.append(
            _trace(
                "alpaca CLI",
                "api POST /v2/orders",
                ok=False,
                status=thesis.notes,
            )
        )
        thesis.monitoring = monitoring_snapshot(
            thesis, position_snapshots(positions), None, fills=fills
        )
        thesis.exit_status = thesis.monitoring.exit_status
        thesis.exit_reason = thesis.monitoring.exit_reason
        return _complete(
            store, thesis, snaps, gates, traces, tool_path, performance
        )

    posted = run_alpaca_cli(
        settings,
        ["api", "POST", "/v2/orders"],
        stdin=json.dumps(payload),
    )
    traces.append(
        _trace("alpaca CLI", "api POST /v2/orders", **posted.as_trace())
    )
    thesis.cli_commands.append("alpaca api POST /v2/orders  # mleg debit vertical")
    order_id = (
        posted.parsed.get("id")
        if posted.ok and isinstance(posted.parsed, dict)
        else None
    )
    if not order_id:
        detail = (
            "CLI submission failed; refusing SDK order fallback."
            if not posted.ok
            else "CLI response had no order id; refusing retry to avoid a duplicate order."
        )
        gates.append(_gate("cli_order_submission", False, detail))
        thesis.status = ThesisStatus.DRAFT
        thesis.decision = "blocked"
        thesis.notes = detail
        thesis.monitoring = monitoring_snapshot(
            thesis, position_snapshots(positions), None, fills=fills
        )
        thesis.exit_status = thesis.monitoring.exit_status
        thesis.exit_reason = thesis.monitoring.exit_reason
        return _complete(
            store, thesis, snaps, gates, traces, tool_path, performance
        )

    submitted_order = posted.parsed
    tool_path = "cli"
    gates.append(
        _gate(
            "cli_order_submission",
            True,
            f"Alpaca CLI returned paper order {str(order_id)[:12]}…",
        )
    )

    thesis.order_id = str(order_id)
    current_order = None
    try:
        current_order = order_snapshot(client.order(thesis.order_id))
        traces.append(
            _trace(
                "Alpaca Trading API",
                "order status",
                ok=True,
                order_id=thesis.order_id,
                status=current_order.status,
                filled_qty=current_order.filled_qty,
            )
        )
    except Exception:
        if submitted_order is not None:
            current_order = order_snapshot(submitted_order)
        traces.append(
            _trace(
                "Alpaca Trading API",
                "order status",
                ok=current_order is not None,
                order_id=thesis.order_id,
                status=(
                    f"{current_order.status}; status refresh unavailable"
                    if current_order
                    else "Order status refresh unavailable"
                ),
            )
        )
    if current_order is not None:
        thesis.order_status = current_order.status
        thesis.order_submitted_at = current_order.submitted_at
        thesis.order_filled_at = current_order.filled_at
        thesis.order_filled_qty = current_order.filled_qty
        thesis.order_filled_avg_price = current_order.filled_avg_price
        store.save_order_snapshot(thesis.id, current_order)
    thesis.status = ThesisStatus.OPEN
    thesis.decision = "submitted"
    thesis.tool_path = tool_path
    try:
        positions = client.positions()
    except Exception:
        pass
    thesis.monitoring = monitoring_snapshot(
        thesis,
        position_snapshots(positions),
        current_order,
        fills=fills,
    )
    thesis.exit_status = thesis.monitoring.exit_status
    thesis.exit_reason = thesis.monitoring.exit_reason
    return _complete(store, thesis, snaps, gates, traces, tool_path, performance)


def _complete(
    store: ThesisStore,
    thesis: Thesis,
    snaps: list[MarketSnapshot],
    gates: list[dict[str, Any]],
    traces: list[dict[str, Any]],
    tool_path: str,
    performance: PerformanceSnapshot | None,
) -> CycleResult:
    thesis.gates = gates
    thesis.mcp_trace = traces
    thesis.tool_path = tool_path
    store.upsert(thesis)
    store.save_cycle(
        _cycle_dict(
            thesis,
            traces,
            tool_path,
            gates,
            snaps,
            performance,
        )
    )
    return _result(
        thesis,
        snaps,
        gates,
        traces,
        tool_path,
        performance,
    )


def _cycle_dict(
    thesis: Thesis,
    traces: list[dict[str, Any]],
    tool_path: str,
    gates: list[dict[str, Any]],
    snaps: list[MarketSnapshot],
    performance: PerformanceSnapshot | None,
) -> dict[str, Any]:
    return {
        "id": uuid4().hex,
        "at": datetime.now(timezone.utc).isoformat(),
        "decision": thesis.decision,
        "tool_path": tool_path,
        "thesis": json.loads(thesis.model_dump_json()),
        "snapshots": _snap_dicts(snaps),
        "gates": gates,
        "traces": traces,
        "performance": (
            performance.model_dump(mode="json") if performance else None
        ),
    }


def _result(
    thesis: Thesis,
    snaps: list[MarketSnapshot],
    gates: list[dict[str, Any]],
    traces: list[dict[str, Any]],
    tool_path: str,
    performance: PerformanceSnapshot | None,
) -> CycleResult:
    return CycleResult(
        at=datetime.now(timezone.utc).isoformat(),
        decision=thesis.decision,
        thesis=thesis,
        snapshots=_snap_dicts(snaps),
        gates=gates,
        traces=traces,
        tool_path=tool_path,
        performance=performance,
    )
