from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from thesis.alpaca.client import PaperClient
from thesis.audit import (
    monitoring_snapshot,
    order_snapshot,
    performance_snapshot,
    position_snapshots,
)
from thesis.config import DEFAULT_SCOUT_UNIVERSE, Settings
from thesis.llm import draft_thesis
from thesis.models import PerformanceSnapshot, Side, Thesis, ThesisStatus
from thesis.observe import MarketSnapshot, observe_universe
from thesis.risk import (
    BASELINE_UNIVERSE,
    MIN_CONVICTION,
    RiskError,
    RiskSnapshot,
    check_open,
    symbols_for_scout,
)
from thesis.scout import (
    PROBE_LIMIT,
    SHORTLIST_LIMIT,
    compare_stock_candidates,
    scout_market,
)
from thesis.spread import SpreadError, build_debit_vertical
from thesis.store import ThesisStore
from thesis.tools.mcp import AlpacaMcpSession, McpError, McpToolResult


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


def _milliseconds(started: float, finished: float) -> float:
    return round(max(0.0, finished - started) * 1000, 3)


def _success_pct(succeeded: int, attempted: int) -> float:
    if attempted <= 0:
        return 0.0
    return round(succeeded / attempted * 100, 2)


def _latest_baseline_scout_trace(
    store: ThesisStore,
) -> tuple[dict[str, Any], str] | None:
    for cycle in store.list_cycles(limit=100):
        for trace in cycle.get("traces") or []:
            if (
                isinstance(trace, dict)
                and trace.get("step") == "rank and options feasibility"
                and trace.get("universe_profile") == "baseline"
                and isinstance(trace.get("duration_ms"), (int, float))
                and not isinstance(trace.get("duration_ms"), bool)
            ):
                return trace, str(cycle.get("at") or "")
    return None


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
        "DRY RUN — Alpaca MCP place_option_order "
        + json.dumps(public_intent, separators=(",", ":"), sort_keys=True)
    )


def _mcp_account_readiness(result: McpToolResult) -> tuple[bool, str]:
    if not result.ok:
        return False, result.status
    if not isinstance(result.data, dict):
        return False, "MCP account response was not valid JSON"
    status = str(result.data.get("status") or "").upper()
    if status != "ACTIVE":
        return False, f"MCP paper account is not active ({status or 'unknown'})"
    if result.data.get("account_blocked") or result.data.get("trading_blocked"):
        return False, "MCP paper account reports trading blocked"
    try:
        options_level = int(result.data.get("options_trading_level"))
    except (TypeError, ValueError):
        return False, "MCP account response has no valid options trading level"
    if options_level < 3:
        return False, f"MCP paper account options level {options_level}; level 3 required"
    return True, f"paper account ACTIVE; options level {options_level}"


def _mcp_clock_readiness(
    result: McpToolResult,
    *,
    sdk_is_open: bool,
) -> tuple[bool, bool, str]:
    if not result.ok:
        return False, False, result.status
    if not isinstance(result.data, dict) or not isinstance(
        result.data.get("is_open"),
        bool,
    ):
        return False, False, "MCP clock response was not valid JSON"
    mcp_is_open = result.data["is_open"]
    if mcp_is_open != sdk_is_open:
        return (
            False,
            False,
            f"MCP/SDK clock mismatch (MCP={mcp_is_open}, SDK={sdk_is_open}); blocked",
        )
    state = "open" if mcp_is_open else "closed"
    return True, mcp_is_open, f"paper market {state}; SDK agrees"


def run_cycle(settings: Settings, *, execute: bool | None = None) -> CycleResult:
    return asyncio.run(_run_cycle(settings, execute=execute))


async def _run_cycle(
    settings: Settings,
    *,
    execute: bool | None = None,
) -> CycleResult:
    settings.assert_paper()
    try:
        async with AlpacaMcpSession(settings) as mcp:
            return await _run_cycle_with_mcp(settings, mcp, execute=execute)
    except McpError:
        store = ThesisStore(settings.db_path)
        thesis = Thesis(
            underlying="SPY",
            side=Side.BULLISH,
            regime="mcp_unavailable",
            setup="The official Alpaca MCP server was unavailable. No trade.",
            invalidation="n/a",
            horizon="14-45 DTE",
            expected_move_pct=0,
            iv_note="MCP evidence unavailable.",
            conviction=0,
            status=ThesisStatus.REJECTED,
            notes="sanitized MCP initialization failure",
            decision="blocked",
            tool_path="mcp",
        )
        gates = [
            _gate("paper_only", True, settings.base_url),
            _gate("mcp_server", False, "Official Alpaca MCP server unavailable"),
        ]
        traces = [
            _trace(
                "Alpaca MCP",
                "initialize",
                ok=False,
                status="Official Alpaca MCP server unavailable; failed closed",
            )
        ]
        return _complete(store, thesis, [], gates, traces, "mcp", None)


async def _run_cycle_with_mcp(
    settings: Settings,
    mcp: AlpacaMcpSession,
    *,
    execute: bool | None = None,
) -> CycleResult:
    settings.assert_paper()
    allow = settings.allow_execute and (execute is not False)
    client = PaperClient(settings)
    store = ThesisStore(settings.db_path)
    traces: list[dict[str, Any]] = []
    gates: list[dict[str, Any]] = []

    account_mcp = await mcp.call_system_tool("get_account_info", {})
    clock_mcp = await mcp.call_system_tool("get_clock", {})
    mcp_trace_count = len(mcp.traces)
    traces.extend(mcp.traces)

    account = client.account()
    clock = client.clock()
    account_mcp_ready, account_mcp_detail = _mcp_account_readiness(account_mcp)
    clock_mcp_ready, market_open, clock_mcp_detail = _mcp_clock_readiness(
        clock_mcp,
        sdk_is_open=bool(clock.is_open),
    )
    mcp_ok = account_mcp_ready and clock_mcp_ready
    market_open = market_open and mcp_ok
    tool_path = "mcp"
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
    scout_profile = getattr(settings, "scout_universe", "baseline")
    scout_symbols = symbols_for_scout(scout_profile)
    scout_started = perf_counter()
    observations = (
        observe_universe(client)
        if scout_profile == "baseline"
        else observe_universe(client, scout_symbols)
    )
    observed_at = perf_counter()
    snaps = observations.snapshots
    scouting = scout_market(client, snaps)
    scouted_at = perf_counter()
    scouting.leaderboard.extend(observations.failures)
    shortlist = scouting.shortlist
    stage_counts = dict(getattr(scouting, "stage_counts", {}) or {})
    ranked_count = int(stage_counts.get("stock_ranked", len(snaps)))
    probed_count = int(
        stage_counts.get(
            "options_probed",
            sum(1 for row in scouting.leaderboard if row.get("probed")),
        )
    )
    feasible_count = sum(
        1 for row in scouting.leaderboard if row.get("status") == "feasible"
    )
    observation_duration_ms = float(
        getattr(
            observations,
            "duration_ms",
            _milliseconds(scout_started, observed_at),
        )
    )
    stock_rank_duration_ms = float(
        getattr(scouting, "stock_rank_duration_ms", 0.0)
    )
    options_probe_duration_ms = float(
        getattr(
            scouting,
            "options_probe_duration_ms",
            _milliseconds(observed_at, scouted_at),
        )
    )
    total_scout_duration_ms = _milliseconds(scout_started, scouted_at)
    candidate_comparison = compare_stock_candidates(scouting.leaderboard)
    observed_symbols = {snap.symbol for snap in snaps}
    baseline_symbols = set(BASELINE_UNIVERSE) & set(scout_symbols)
    baseline_attempted_count = len(baseline_symbols)
    baseline_observed_count = len(baseline_symbols & observed_symbols)
    baseline_failed_count = baseline_attempted_count - baseline_observed_count
    selected_attempted_count = len(scout_symbols)
    selected_observed_count = len(observed_symbols & set(scout_symbols))
    selected_failed_count = selected_attempted_count - selected_observed_count
    baseline_success_pct = _success_pct(
        baseline_observed_count,
        baseline_attempted_count,
    )
    selected_success_pct = _success_pct(
        selected_observed_count,
        selected_attempted_count,
    )
    reliability_delta_pct_points = round(
        selected_success_pct - baseline_success_pct,
        2,
    )
    symbol_durations = getattr(observations, "symbol_durations_ms", {}) or {}
    baseline_duration_values = [
        symbol_durations.get(symbol) for symbol in baseline_symbols
    ]
    if baseline_duration_values and all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in baseline_duration_values
    ):
        baseline_observation_duration_ms = round(
            sum(float(value) for value in baseline_duration_values),
            3,
        )
    elif scout_profile == "baseline":
        baseline_observation_duration_ms = observation_duration_ms
    else:
        baseline_observation_duration_ms = None
    observation_latency_delta_ms = (
        round(
            observation_duration_ms - baseline_observation_duration_ms,
            3,
        )
        if baseline_observation_duration_ms is not None
        else None
    )
    if scout_profile == "baseline":
        baseline_reference_duration_ms: float | None = total_scout_duration_ms
        baseline_reference_source = "current_cycle"
        baseline_reference_at = datetime.now(timezone.utc).isoformat()
    else:
        baseline_reference = _latest_baseline_scout_trace(store)
        baseline_reference_duration_ms = (
            float(baseline_reference[0]["duration_ms"])
            if baseline_reference
            else None
        )
        baseline_reference_source = (
            "historical_cycle" if baseline_reference else "unavailable"
        )
        baseline_reference_at = baseline_reference[1] if baseline_reference else ""
    full_scout_latency_delta_ms = (
        round(total_scout_duration_ms - baseline_reference_duration_ms, 3)
        if baseline_reference_duration_ms is not None
        else None
    )
    comparison_ready = (
        scout_profile == "expanded"
        and baseline_reference_duration_ms is not None
        and baseline_attempted_count == len(BASELINE_UNIVERSE)
    )
    llm_attempted = False
    llm_ok = True
    if shortlist:
        llm_attempted = True
        try:
            draft = await draft_thesis(
                settings,
                shortlist,
                scouting.shortlist_sides,
                mcp,
            )
            thesis = draft.thesis
            traces.extend(mcp.traces[mcp_trace_count:])
            mcp_trace_count = len(mcp.traces)
            traces.extend(draft.traces)
        except Exception:
            llm_ok = False
            traces.extend(mcp.traces[mcp_trace_count:])
            mcp_trace_count = len(mcp.traces)
            leader = scouting.leaderboard[0]
            thesis = Thesis(
                underlying=leader["symbol"],
                side=Side.BULLISH,
                regime=leader["regime"],
                setup="Thesis provider output was unavailable or invalid. No trade.",
                invalidation="n/a",
                horizon="14-45 DTE",
                expected_move_pct=0,
                iv_note="Provider result unavailable.",
                conviction=0.0,
                notes="sanitized thesis provider failure",
                decision="no_trade",
            )
    else:
        leader = scouting.leaderboard[0] if scouting.leaderboard else None
        thesis = Thesis(
            underlying=leader["symbol"] if leader else "SPY",
            side=Side.BULLISH,
            regime=leader["regime"] if leader else "unknown",
            setup="No option-feasible candidate passed deterministic scouting.",
            invalidation="n/a",
            horizon="14-45 DTE",
            expected_move_pct=0,
            iv_note="No valid natural-debit vertical was available.",
            conviction=0.0,
            notes="deterministic scout no_trade",
            decision="no_trade",
        )
    thesis.snapshots = _snap_dicts(snaps)
    thesis.leaderboard = scouting.leaderboard
    thesis.tool_path = tool_path
    thesis.cli_commands = []

    gates.append(_gate("paper_only", True, settings.base_url))
    gates.append(
        _gate(
            "scout_shortlist",
            bool(shortlist),
            f"profile={scout_profile} observed={len(snaps)}/{len(scout_symbols)} "
            f"probed={probed_count}/{PROBE_LIMIT} feasible={feasible_count} "
            f"shortlist={len(shortlist)}",
        )
    )
    traces.append(
        _trace(
            "deterministic scout",
            "rank and options feasibility",
            ok=bool(shortlist),
            status=(
                f"profile={scout_profile}; duration_ms={total_scout_duration_ms:.3f}; "
                f"stage_targets=stock:{len(scout_symbols)},options:{PROBE_LIMIT},"
                f"grok:{SHORTLIST_LIMIT}; stage_actuals=observed:{len(snaps)},"
                f"failed:{selected_failed_count},ranked:{ranked_count},"
                f"probed:{probed_count},feasible:{feasible_count},"
                f"shortlist:{len(shortlist)}"
            ),
            universe_profile=scout_profile,
            universe_size=len(scout_symbols),
            duration_ms=total_scout_duration_ms,
            observation_duration_ms=observation_duration_ms,
            stock_rank_duration_ms=stock_rank_duration_ms,
            options_probe_duration_ms=options_probe_duration_ms,
            stock_observation_target=len(scout_symbols),
            observed=selected_observed_count,
            observation_failures=selected_failed_count,
            observation_success_pct=selected_success_pct,
            stock_ranked=ranked_count,
            options_probe_budget=PROBE_LIMIT,
            options_probe_candidates=min(PROBE_LIMIT, ranked_count),
            probed=probed_count,
            feasible=feasible_count,
            shortlist_budget=SHORTLIST_LIMIT,
            shortlist_size=len(shortlist),
            top_five_avg_stock_score=(
                candidate_comparison["active_top_five_avg_stock_score"]
            ),
        )
    )
    baseline_reference_text = (
        f"{baseline_reference_duration_ms:.3f}"
        if baseline_reference_duration_ms is not None
        else "unavailable"
    )
    full_latency_delta_text = (
        f"{full_scout_latency_delta_ms:.3f}"
        if full_scout_latency_delta_ms is not None
        else "unavailable"
    )
    observation_latency_delta_text = (
        f"{observation_latency_delta_ms:.3f}"
        if observation_latency_delta_ms is not None
        else "unavailable"
    )
    traces.append(
        _trace(
            "deterministic scout",
            "ten-symbol baseline comparison",
            ok=scout_profile == "baseline" or comparison_ready,
            status=(
                f"profile={scout_profile}; baseline_reference_ms="
                f"{baseline_reference_text}; selected_ms="
                f"{total_scout_duration_ms:.3f}; full_latency_delta_ms="
                f"{full_latency_delta_text}; same_cycle_observation_delta_ms="
                f"{observation_latency_delta_text}; success_pct=baseline:"
                f"{baseline_success_pct:.2f},selected:{selected_success_pct:.2f}; "
                f"reliability_delta_pct_points={reliability_delta_pct_points:.2f}; "
                f"top_five_avg_stock_score_delta="
                f"{candidate_comparison['top_five_avg_stock_score_delta']:.4f}; "
                f"top_five_overlap={candidate_comparison['top_five_overlap_count']}/"
                f"{PROBE_LIMIT}; baseline_source={baseline_reference_source}; "
                f"default_profile={DEFAULT_SCOUT_UNIVERSE}"
            ),
            universe_profile=scout_profile,
            default_profile=DEFAULT_SCOUT_UNIVERSE,
            comparison_ready=comparison_ready,
            baseline_universe_size=len(BASELINE_UNIVERSE),
            selected_universe_size=len(scout_symbols),
            baseline_attempted_count=baseline_attempted_count,
            baseline_observed_count=baseline_observed_count,
            baseline_failed_count=baseline_failed_count,
            baseline_success_pct=baseline_success_pct,
            selected_attempted_count=selected_attempted_count,
            selected_observed_count=selected_observed_count,
            selected_failed_count=selected_failed_count,
            selected_success_pct=selected_success_pct,
            reliability_delta_pct_points=reliability_delta_pct_points,
            baseline_observation_duration_ms=baseline_observation_duration_ms,
            selected_observation_duration_ms=observation_duration_ms,
            observation_latency_delta_ms=observation_latency_delta_ms,
            baseline_reference_source=baseline_reference_source,
            baseline_reference_at=baseline_reference_at,
            baseline_reference_duration_ms=baseline_reference_duration_ms,
            selected_duration_ms=total_scout_duration_ms,
            full_scout_latency_delta_ms=full_scout_latency_delta_ms,
            latency_delta_ms=full_scout_latency_delta_ms,
            **candidate_comparison,
        )
    )
    if llm_attempted:
        gates.append(
            _gate(
                "thesis_provider",
                llm_ok,
                (
                    "validated bounded MCP-agent response"
                    if llm_ok
                    else "provider or MCP-agent output unavailable"
                ),
            )
        )
        traces.append(
            _trace(
                "Grok",
                "bounded MCP tool thesis",
                ok=llm_ok,
                status=(
                    "Validated MCP-researched shortlist response"
                    if llm_ok
                    else "Provider output unavailable"
                ),
            )
        )
    gates.append(
        _gate(
            "mcp_account",
            account_mcp_ready,
            account_mcp_detail,
        )
    )
    gates.append(
        _gate(
            "mcp_clock",
            clock_mcp_ready,
            clock_mcp_detail,
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
            f"authoritative MCP state={market_open}",
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
            blocked_by.append("MCP market gate closed or unavailable")
        if not allow:
            blocked_by.append("execution disabled")
        traces.append(
            _trace(
                "Alpaca MCP",
                "guarded order intent",
                ok=True,
                status=f"{_mleg_evidence(thesis)}; not submitted ({', '.join(blocked_by)})",
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

    payload = _mleg_payload(thesis)
    gates.append(
        _gate(
            "mcp_order_path",
            mcp_ok,
            (
                "Alpaca MCP account and clock checks passed"
                if mcp_ok
                else "Alpaca MCP is not ready; all order fallbacks are disabled"
            ),
        )
    )
    if not mcp_ok:
        thesis.status = ThesisStatus.DRAFT
        thesis.decision = "blocked"
        thesis.notes = "Alpaca MCP unavailable; refusing all order fallbacks."
        traces.append(
            _trace(
                "Alpaca MCP",
                "place_option_order",
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

    try:
        posted = await mcp.place_option_order(payload)
        traces.extend(mcp.traces[mcp_trace_count:])
        mcp_trace_count = len(mcp.traces)
    except McpError:
        traces.extend(mcp.traces[mcp_trace_count:])
        mcp_trace_count = len(mcp.traces)
        detail = (
            "MCP submission failed or was ambiguous; refusing every retry and "
            "fallback to avoid a duplicate order."
        )
        gates.append(_gate("mcp_order_submission", False, detail))
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

    submitted_order = posted.data if isinstance(posted.data, dict) else None
    order_id = submitted_order.get("id") if submitted_order else None
    if not order_id:
        detail = (
            "MCP response had no order id; outcome is ambiguous and will not be retried."
        )
        gates.append(_gate("mcp_order_submission", False, detail))
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

    gates.append(
        _gate(
            "mcp_order_submission",
            True,
            f"Alpaca MCP returned paper order {str(order_id)[:12]}…",
        )
    )

    thesis.order_id = str(order_id)
    current_order = None
    try:
        refreshed = await mcp.call_system_tool(
            "get_order_by_id",
            {"order_id": thesis.order_id, "nested": True},
        )
        traces.extend(mcp.traces[mcp_trace_count:])
        mcp_trace_count = len(mcp.traces)
        if not isinstance(refreshed.data, dict):
            raise McpError("MCP order status response was invalid")
        current_order = order_snapshot(refreshed.data)
    except McpError:
        traces.extend(mcp.traces[mcp_trace_count:])
        mcp_trace_count = len(mcp.traces)
        if submitted_order is not None:
            current_order = order_snapshot(submitted_order)
        traces.append(
            _trace(
                "Alpaca MCP",
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
