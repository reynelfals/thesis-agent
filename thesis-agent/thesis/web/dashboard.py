"""Thread-safe, read-only dashboard snapshots built from broker and audit data."""

from __future__ import annotations

import copy
import re
import threading
from datetime import datetime, timezone
from typing import Any

from thesis.audit import (
    ACTIVE_ORDER_STATUSES,
    fill_rows,
    monitoring_snapshot,
    order_snapshot,
    performance_snapshot,
    portfolio_points,
    position_snapshots,
)
from thesis.config import PAPER_BASE
from thesis.models import OrderSnapshot, Thesis
from thesis.store import ThesisStore

_PUBLIC_READ_ERROR = "Alpaca paper account data is temporarily unavailable."
MAX_FILL_ACTIVITY_PAGES = 100
MAX_LIVE_ORDER_REFRESHES = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _text(value: Any) -> str:
    return str(getattr(value, "value", value or ""))


_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b((?:[a-z0-9]+_)*(?:api(?:_secret)?_key|secret(?:_key)?|"
    r"access_token|token|password|authorization))"
    r"\s*[:=]\s*([^\s,;]+)"
)
_SECRET_TOKEN = re.compile(
    r"(?i)\b(?:sk|pk|key|secret|token)[-_][a-z0-9._-]{6,}\b"
)
_LONG_NUMBER = re.compile(r"\b\d{8,}\b")


def _public_text(value: Any) -> str:
    """Redact credential-like assignments from stored free-form evidence."""
    redacted = _SENSITIVE_ASSIGNMENT.sub(r"\1=[REDACTED]", _text(value))
    redacted = _SECRET_TOKEN.sub("[REDACTED]", redacted)
    return _LONG_NUMBER.sub("[REDACTED]", redacted)


def sanitize_public_payload(value: Any) -> Any:
    """Recursively sanitize the final JSON boundary without coercing primitives."""
    if isinstance(value, str):
        return _public_text(value)
    if isinstance(value, datetime):
        return _public_text(value.isoformat())
    if isinstance(value, dict):
        return {key: sanitize_public_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_public_payload(item) for item in value]
    return value


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _gate_rows(gates: Any) -> list[dict[str, Any]]:
    return [
        {
            "at": _iso(gate.get("at")),
            "name": _public_text(gate.get("name")),
            "status": "pass" if bool(gate.get("ok")) else "fail",
            "evidence": _public_text(gate.get("detail")),
        }
        for gate in gates or []
        if isinstance(gate, dict)
    ]


def _safe_structure(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    keys = (
        "kind",
        "underlying",
        "long_symbol",
        "short_symbol",
        "expiration",
        "long_strike",
        "short_strike",
        "dte",
        "debit_limit",
        "qty",
        "max_loss_usd",
    )
    return {key: value.get(key) for key in keys}


def _safe_thesis(value: Any) -> dict[str, Any]:
    thesis = value if isinstance(value, dict) else {}
    keys = (
        "id",
        "created_at",
        "underlying",
        "side",
        "regime",
        "setup",
        "invalidation",
        "horizon",
        "expected_move_pct",
        "iv_note",
        "conviction",
        "status",
        "decision",
        "notes",
    )
    result = {
        key: (
            _public_text(thesis.get(key))
            if key in {"regime", "setup", "invalidation", "horizon", "iv_note", "notes"}
            else thesis.get(key)
        )
        for key in keys
    }
    result["structure"] = _safe_structure(thesis.get("structure"))
    return result


def _safe_traces(traces: Any) -> list[dict[str, Any]]:
    """Expose proof of tool use without commands, environments, or stderr."""
    rows = []
    for trace in traces or []:
        if not isinstance(trace, dict):
            continue
        evidence = trace.get("status")
        if evidence is None and trace.get("fill_count") is not None:
            evidence = f"{trace.get('fill_count')} Alpaca fills"
        rows.append(
            {
                "at": _iso(trace.get("at")),
                "tool": _public_text(trace.get("tool")),
                "step": _public_text(trace.get("step")),
                "status": "pass" if bool(trace.get("ok")) else "fail",
                "evidence": _public_text(evidence),
            }
        )
    return rows


def _safe_order(snapshot: OrderSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "observed_at": _iso(snapshot.observed_at),
        "order_id": snapshot.order_id,
        "status": snapshot.status,
        "submitted_at": _iso(snapshot.submitted_at),
        "updated_at": _iso(snapshot.updated_at),
        "filled_at": _iso(snapshot.filled_at),
        "canceled_at": _iso(snapshot.canceled_at),
        "expired_at": _iso(snapshot.expired_at),
        "failed_at": _iso(snapshot.failed_at),
        "qty": snapshot.qty,
        "filled_qty": snapshot.filled_qty,
        "filled_avg_price": snapshot.filled_avg_price,
        "limit_price": snapshot.limit_price,
        "legs": [
            {
                key: leg.get(key)
                for key in (
                    "order_id",
                    "symbol",
                    "side",
                    "status",
                    "qty",
                    "filled_qty",
                    "filled_avg_price",
                )
            }
            for leg in snapshot.legs
            if isinstance(leg, dict)
        ],
    }


def _safe_monitoring(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    data = value if isinstance(value, dict) else value.model_dump(mode="json")
    keys = (
        "observed_at",
        "attribution_status",
        "entry_status",
        "position_status",
        "open_leg_count",
        "expected_leg_count",
        "market_value",
        "unrealized_pl",
        "exit_status",
        "exit_reason",
    )
    return {key: data.get(key) for key in keys}


def _safe_market_snapshots(values: Any) -> list[dict[str, Any]]:
    keys = (
        "symbol",
        "spot",
        "sma5",
        "sma20",
        "ret_5d_pct",
        "realized_vol_20d",
        "regime",
    )
    return [
        {key: value.get(key) for key in keys}
        for value in values or []
        if isinstance(value, dict)
    ]


def _safe_cycle_performance(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    keys = (
        "starting_equity",
        "equity",
        "total_pl",
        "total_return_pct",
        "realized_pl",
        "unrealized_pl",
        "reconciliation_delta",
        "fill_count",
    )
    return {key: value.get(key) for key in keys}


def _decision_reason(cycle: dict[str, Any], thesis: dict[str, Any]) -> str:
    if thesis.get("notes"):
        return _public_text(thesis["notes"])
    failed = [
        _public_text(gate.get("detail") or gate.get("name"))
        for gate in cycle.get("gates") or []
        if isinstance(gate, dict) and not gate.get("ok")
    ]
    if failed:
        return "; ".join(failed)
    if cycle.get("decision") == "submitted":
        return "All deterministic entry gates passed."
    return "Decision completed with no failed gate."


class Dashboard:
    """Owns a cached snapshot and refreshes it on one daemon thread."""

    def __init__(
        self,
        *,
        settings: Any,
        store: ThesisStore,
        client: Any,
        refresh_interval_seconds: float = 60,
    ) -> None:
        settings.assert_paper()
        if settings.base_url != PAPER_BASE:
            raise ValueError("dashboard requires the Alpaca paper endpoint")
        self.settings = settings
        self.store = store
        self.client = client
        self.refresh_interval_seconds = max(float(refresh_interval_seconds), 1.0)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot = self._empty_snapshot("loading")

    def _empty_snapshot(self, status: str) -> dict[str, Any]:
        execution_enabled = bool(getattr(self.settings, "allow_execute", False))
        return {
            "status": status,
            "generated_at": None,
            "last_attempt_at": None,
            "refresh_interval_seconds": self.refresh_interval_seconds,
            "error": None,
            "execution_enabled": execution_enabled,
            "banner": (
                "Execution is enabled for the cycle runner; this dashboard remains read-only."
                if execution_enabled
                else "Execution is off. This dashboard performs broker reads only."
            ),
            "readiness": None,
            "performance": None,
            "positions": {"tracked": [], "live_legs": [], "has_unmanaged": False},
            "cycles": [],
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snapshot = copy.deepcopy(self._snapshot)
        return sanitize_public_payload(snapshot)

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="thesis-dashboard-refresh",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(self.refresh_interval_seconds)

    def refresh(self) -> None:
        attempted_at = _now()
        with self._lock:
            self._snapshot["last_attempt_at"] = attempted_at.isoformat()
        try:
            fresh = self._build_snapshot(attempted_at)
        except Exception:
            with self._lock:
                had_success = self._snapshot["generated_at"] is not None
                self._snapshot["status"] = "stale" if had_success else "error"
                self._snapshot["error"] = _PUBLIC_READ_ERROR
                self._snapshot["last_attempt_at"] = attempted_at.isoformat()
            return
        with self._lock:
            self._snapshot = fresh

    def _refresh_orders(
        self,
        theses: list[Thesis],
        positions: list[Any],
        fills: list[dict[str, Any]],
    ) -> dict[str, OrderSnapshot]:
        orders: dict[str, OrderSnapshot] = {}
        stored: list[tuple[Thesis, OrderSnapshot | None]] = [
            (
                thesis,
                self.store.latest_order_snapshot(thesis.order_id)
                if thesis.order_id
                else None,
            )
            for thesis in theses
        ]
        refresh_count = sum(
            1
            for thesis, snapshot in stored
            if thesis.order_id
            and (snapshot is None or snapshot.status in ACTIVE_ORDER_STATUSES)
        )
        if refresh_count > MAX_LIVE_ORDER_REFRESHES:
            raise RuntimeError(
                "live order refresh limit exceeded; refusing partial order evidence"
            )

        for thesis, snapshot in stored:
            # Historical terminal orders remain local; only missing/active orders
            # are refreshed from Alpaca.
            if thesis.order_id and (
                snapshot is None or snapshot.status in ACTIVE_ORDER_STATUSES
            ):
                snapshot = order_snapshot(self.client.order(thesis.order_id))
            if snapshot is not None:
                orders[thesis.order_id or snapshot.order_id] = snapshot
                thesis.order_status = snapshot.status
                thesis.order_submitted_at = snapshot.submitted_at
                thesis.order_filled_at = snapshot.filled_at
                thesis.order_filled_qty = snapshot.filled_qty
                thesis.order_filled_avg_price = snapshot.filled_avg_price
            thesis.monitoring = monitoring_snapshot(
                thesis, positions, snapshot, fills=fills
            )
            thesis.exit_status = thesis.monitoring.exit_status
            thesis.exit_reason = thesis.monitoring.exit_reason
        return orders

    def _build_snapshot(self, at: datetime) -> dict[str, Any]:
        account = self.client.account()
        clock = self.client.clock()
        raw_positions = self.client.positions()
        history = self.client.portfolio_history()
        fills = self.client.fill_activities(max_pages=MAX_FILL_ACTIVITY_PAGES)
        positions = position_snapshots(raw_positions, observed_at=at)
        performance = performance_snapshot(
            account, raw_positions, history, fills, observed_at=at
        )
        # Store models are copied before adding live broker evidence. Refreshes
        # perform no writes after ThesisStore initialization.
        theses = [thesis.model_copy(deep=True) for thesis in self.store.list()]
        orders = self._refresh_orders(theses, positions, fills)
        theses_by_id = {thesis.id: thesis for thesis in theses}

        status = _text(_value(account, "status")).upper()
        options_level = int(_number(_value(account, "options_trading_level")))
        checks = [
            {
                "label": "Paper-only endpoint",
                "status": "pass",
                "evidence": "Alpaca paper trading",
            },
            {
                "label": "Account active",
                "status": "pass" if status == "ACTIVE" else "fail",
                "evidence": status,
            },
            {
                "label": "Demo baseline",
                "status": (
                    "pass"
                    if abs(
                        performance.starting_equity
                        - self.settings.demo_starting_equity
                    )
                    < 0.01
                    else "fail"
                ),
                "evidence": f"${performance.starting_equity:,.2f}",
            },
            {
                "label": "Options level 3",
                "status": "pass" if options_level >= 3 else "fail",
                "evidence": f"level {options_level}",
            },
            {
                "label": "Broker reads healthy",
                "status": "pass",
                "evidence": (
                    f"{performance.fill_count} fills; market "
                    f"{'open' if bool(_value(clock, 'is_open')) else 'closed'}"
                ),
            },
        ]
        readiness = {
            "ready": all(check["status"] == "pass" for check in checks),
            "account_suffix": _text(_value(account, "account_number"))[-4:],
            "account_phase": (
                "Fresh — no fills recorded"
                if performance.fill_count == 0
                else f"In use — {performance.fill_count} fills retained"
            ),
            "market_open": bool(_value(clock, "is_open")),
            "checks": checks,
        }

        linked_symbols = {
            symbol
            for thesis in theses
            if thesis.structure
            and thesis.monitoring
            and thesis.monitoring.attribution_status == "linked_by_order_fills"
            and thesis.monitoring.open_leg_count > 0
            for symbol in (
                thesis.structure.long_symbol,
                thesis.structure.short_symbol,
            )
        }
        tracked = []
        for thesis in theses:
            if not (thesis.order_id or thesis.decision == "submitted"):
                continue
            monitor = thesis.monitoring
            order = orders.get(thesis.order_id or "")
            tracked.append(
                {
                    "thesis_id": thesis.id,
                    "spread": (
                        f"{thesis.structure.long_symbol} / {thesis.structure.short_symbol}"
                        if thesis.structure
                        else ""
                    ),
                    "entry_order": (
                        order.status
                        if order
                        else thesis.order_status or "unknown"
                    ),
                    "attribution": (
                        monitor.attribution_status if monitor else "not_observed"
                    ),
                    "position": (
                        monitor.position_status if monitor else "not observed"
                    ),
                    "market_value": monitor.market_value if monitor else 0.0,
                    "unrealized_pl": monitor.unrealized_pl if monitor else 0.0,
                    "exit_status": (
                        monitor.exit_status if monitor else thesis.exit_status
                    ),
                    "exit_reason": (
                        monitor.exit_reason if monitor else thesis.exit_reason
                    ),
                    "invalidation": thesis.invalidation,
                }
            )
        live_legs = [
            {
                "symbol": position.symbol,
                "linked": position.symbol in linked_symbols,
                "side": position.side,
                "qty": position.qty,
                "avg_entry_price": position.avg_entry_price,
                "current_price": position.current_price,
                "market_value": position.market_value,
                "unrealized_pl": position.unrealized_pl,
                "unrealized_pl_pct": position.unrealized_pl_pct,
            }
            for position in positions
        ]

        cycles = [
            self._cycle_row(cycle, theses_by_id, orders)
            for cycle in self.store.list_cycles(limit=100)
        ]
        performance_data = {
            key: getattr(performance, key)
            for key in (
                "starting_equity",
                "equity",
                "total_pl",
                "total_return_pct",
                "realized_pl",
                "unrealized_pl",
                "reconciliation_delta",
                "fill_count",
            )
        }
        performance_data["equity_points"] = [
            {
                "at": _iso(point["at"]),
                "equity": point["equity"],
                "total_pl": point["total_pl"],
            }
            for point in portfolio_points(history)
        ]
        performance_data["fills"] = fill_rows(fills)

        result = self._empty_snapshot("ready")
        result.update(
            {
                "generated_at": at.isoformat(),
                "last_attempt_at": at.isoformat(),
                "readiness": readiness,
                "performance": performance_data,
                "positions": {
                    "tracked": tracked,
                    "live_legs": live_legs,
                    "has_unmanaged": any(
                        position.symbol not in linked_symbols for position in positions
                    ),
                },
                "cycles": cycles,
            }
        )
        return result

    def _cycle_row(
        self,
        cycle: dict[str, Any],
        theses: dict[str, Thesis],
        orders: dict[str, OrderSnapshot],
    ) -> dict[str, Any]:
        stored = cycle.get("thesis") if isinstance(cycle.get("thesis"), dict) else {}
        thesis_id = _text(stored.get("id"))
        current = theses.get(thesis_id)
        thesis = current.model_dump(mode="json") if current else stored
        order_id = _text(thesis.get("order_id"))
        order = orders.get(order_id) or (
            self.store.latest_order_snapshot(order_id) if order_id else None
        )
        history = (
            [_safe_order(item) for item in self.store.order_snapshots(thesis_id)]
            if thesis_id
            else []
        )
        gates = cycle.get("gates") or thesis.get("gates") or []
        passed = sum(
            1 for gate in gates if isinstance(gate, dict) and gate.get("ok")
        )
        monitoring = current.monitoring if current else thesis.get("monitoring")
        return {
            "id": _text(cycle.get("id")),
            "at": _iso(cycle.get("at")),
            "thesis_id": thesis_id,
            "underlying": _text(thesis.get("underlying")),
            "decision": _text(cycle.get("decision") or thesis.get("decision")),
            "decision_reason": _decision_reason(cycle, thesis),
            "gate_summary": f"{passed}/{len(gates)} passed",
            "tool_path": (
                _text(cycle.get("tool_path") or thesis.get("tool_path"))
                if _text(cycle.get("tool_path") or thesis.get("tool_path"))
                in {"cli", "alpaca-py", "none"}
                else "unknown"
            ),
            "order_status": order.status if order else thesis.get("order_status") or "not_submitted",
            "exit_status": (
                _value(monitoring, "exit_status")
                or thesis.get("exit_status")
                or "not_applicable"
            ),
            "thesis": _safe_thesis(thesis),
            "gates": _gate_rows(gates),
            "traces": _safe_traces(
                cycle.get("traces") or thesis.get("mcp_trace") or []
            ),
            "order": _safe_order(order),
            "order_history": [item for item in history if item is not None],
            "monitoring": _safe_monitoring(monitoring),
            "market_snapshots": _safe_market_snapshots(
                cycle.get("snapshots") or []
            ),
            "performance": _safe_cycle_performance(cycle.get("performance")),
        }