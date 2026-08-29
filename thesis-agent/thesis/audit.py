from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Iterable

from thesis.models import (
    MonitoringSnapshot,
    OrderSnapshot,
    PerformanceSnapshot,
    PositionSnapshot,
    Thesis,
)


ACTIVE_ORDER_STATUSES = {
    "accepted",
    "accepted_for_bidding",
    "calculated",
    "held",
    "new",
    "partially_filled",
    "pending_cancel",
    "pending_new",
    "pending_replace",
    "stopped",
}
TERMINAL_UNFILLED_STATUSES = {
    "canceled",
    "expired",
    "failed",
    "rejected",
    "replaced",
    "suspended",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sum_numbers(value: Any) -> float:
    if isinstance(value, dict):
        return sum(_sum_numbers(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_sum_numbers(item) for item in value)
    return _float(value)


def _datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _history_value(history: Any, name: str, default: Any = None) -> Any:
    return _value(history, name, default) if history is not None else default


def order_snapshot(order: Any, *, observed_at: datetime | None = None) -> OrderSnapshot:
    legs = []
    for leg in _value(order, "legs", []) or []:
        legs.append(
            {
                "order_id": _text(_value(leg, "id")),
                "symbol": _text(_value(leg, "symbol")),
                "side": _text(_value(leg, "side")),
                "status": _text(_value(leg, "status")),
                "qty": _float(_value(leg, "qty")),
                "filled_qty": _float(_value(leg, "filled_qty")),
                "filled_avg_price": (
                    _float(_value(leg, "filled_avg_price"))
                    if _value(leg, "filled_avg_price") is not None
                    else None
                ),
            }
        )
    return OrderSnapshot(
        observed_at=observed_at or _now(),
        order_id=_text(_value(order, "id")),
        client_order_id=_text(_value(order, "client_order_id")),
        status=_text(_value(order, "status")) or "unknown",
        submitted_at=_datetime(_value(order, "submitted_at")),
        updated_at=_datetime(_value(order, "updated_at")),
        filled_at=_datetime(_value(order, "filled_at")),
        canceled_at=_datetime(_value(order, "canceled_at")),
        expired_at=_datetime(_value(order, "expired_at")),
        failed_at=_datetime(_value(order, "failed_at")),
        qty=_float(_value(order, "qty")),
        filled_qty=_float(_value(order, "filled_qty")),
        filled_avg_price=(
            _float(_value(order, "filled_avg_price"))
            if _value(order, "filled_avg_price") is not None
            else None
        ),
        limit_price=(
            _float(_value(order, "limit_price"))
            if _value(order, "limit_price") is not None
            else None
        ),
        legs=legs,
    )


def position_snapshots(
    positions: Iterable[Any], *, observed_at: datetime | None = None
) -> list[PositionSnapshot]:
    at = observed_at or _now()
    rows = []
    for position in positions:
        side = _text(_value(position, "side"))
        qty = _float(_value(position, "qty"))
        if side.lower() == "short":
            qty = -abs(qty)
        elif side.lower() == "long":
            qty = abs(qty)
        rows.append(
            PositionSnapshot(
            observed_at=at,
            symbol=_text(_value(position, "symbol")),
            side=side,
            qty=qty,
            avg_entry_price=_float(_value(position, "avg_entry_price")),
            current_price=_float(_value(position, "current_price")),
            market_value=_float(_value(position, "market_value")),
            cost_basis=_float(_value(position, "cost_basis")),
            unrealized_pl=_float(_value(position, "unrealized_pl")),
            unrealized_pl_pct=_float(_value(position, "unrealized_plpc")) * 100,
        )
        )
    return rows


def fill_rows(fills: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for fill in fills:
        fill_id = _text(fill.get("id"))
        if fill_id and fill_id in seen:
            continue
        seen.add(fill_id)
        rows.append(
            {
                "id": fill_id,
                "at": _text(fill.get("transaction_time")),
                "order_id": _text(fill.get("order_id")),
                "symbol": _text(fill.get("symbol")),
                "side": _text(fill.get("side")).lower(),
                "qty": _float(fill.get("qty")),
                "price": _float(fill.get("price")),
                "order_status": _text(fill.get("order_status")),
            }
        )
    return sorted(rows, key=lambda row: row["at"])


def _contract_multiplier(symbol: str) -> int:
    # OCC option symbols end in YYMMDD + C/P + eight strike digits.
    tail = symbol[-15:]
    return 100 if len(tail) == 15 and tail[:6].isdigit() and tail[6:7] in {"C", "P"} and tail[7:].isdigit() else 1


def realized_pl_from_fills(fills: Iterable[dict[str, Any]]) -> float:
    """FIFO realized execution P&L from Alpaca fill activities."""
    lots: dict[str, deque[list[float]]] = defaultdict(deque)
    realized = 0.0
    for fill in fill_rows(fills):
        symbol = fill["symbol"]
        qty = fill["qty"]
        price = fill["price"]
        signed_qty = qty if fill["side"] == "buy" else -qty
        if not symbol or signed_qty == 0:
            continue
        symbol_lots = lots[symbol]
        remaining = signed_qty
        while symbol_lots and symbol_lots[0][0] * remaining < 0:
            lot_qty, lot_price = symbol_lots[0]
            closed_qty = min(abs(lot_qty), abs(remaining))
            direction = 1.0 if lot_qty > 0 else -1.0
            realized += (
                (price - lot_price)
                * closed_qty
                * direction
                * _contract_multiplier(symbol)
            )
            lot_qty += closed_qty if lot_qty < 0 else -closed_qty
            remaining += closed_qty if remaining < 0 else -closed_qty
            if abs(lot_qty) < 1e-9:
                symbol_lots.popleft()
            else:
                symbol_lots[0][0] = lot_qty
        if abs(remaining) >= 1e-9:
            symbol_lots.append([remaining, price])
    return round(realized, 2)


def performance_snapshot(
    account: Any,
    positions: Iterable[Any],
    history: Any,
    fills: Iterable[dict[str, Any]],
    *,
    observed_at: datetime | None = None,
) -> PerformanceSnapshot:
    position_rows = position_snapshots(positions, observed_at=observed_at)
    fill_list = list(fills)
    equity = _float(_value(account, "equity"))
    starting_equity = _float(_history_value(history, "base_value"), equity)
    cashflow = _history_value(history, "cashflow", {}) or {}
    net_cashflow = _sum_numbers(cashflow)
    latest_total = equity - starting_equity - net_cashflow
    unrealized = sum(position.unrealized_pl for position in position_rows)
    fees = _float(_value(account, "accrued_fees"))
    realized = realized_pl_from_fills(fill_list) - fees
    total_return = (latest_total / starting_equity * 100) if starting_equity else 0.0
    return PerformanceSnapshot(
        observed_at=observed_at or _now(),
        starting_equity=round(starting_equity, 2),
        equity=round(equity, 2),
        last_equity=round(_float(_value(account, "last_equity")), 2),
        cash=round(_float(_value(account, "cash")), 2),
        buying_power=round(_float(_value(account, "buying_power")), 2),
        options_buying_power=round(
            _float(_value(account, "options_buying_power")), 2
        ),
        total_pl=round(latest_total, 2),
        total_return_pct=round(total_return, 4),
        realized_pl=round(realized, 2),
        unrealized_pl=round(unrealized, 2),
        fees=round(fees, 2),
        reconciliation_delta=round(latest_total - realized - unrealized, 2),
        fill_count=len(fill_rows(fill_list)),
    )


def portfolio_points(history: Any) -> list[dict[str, Any]]:
    timestamps = list(_history_value(history, "timestamp", []) or [])
    equities = list(_history_value(history, "equity", []) or [])
    profit_loss = list(_history_value(history, "profit_loss", []) or [])
    rows = []
    for index, timestamp in enumerate(timestamps):
        if index >= len(equities) or equities[index] is None:
            continue
        at = datetime.fromtimestamp(_float(timestamp), tz=timezone.utc)
        rows.append(
            {
                "at": at,
                "equity": _float(equities[index]),
                "total_pl": (
                    _float(profit_loss[index])
                    if index < len(profit_loss) and profit_loss[index] is not None
                    else None
                ),
            }
        )
    return rows


def monitoring_snapshot(
    thesis: Thesis,
    positions: Iterable[PositionSnapshot],
    order: OrderSnapshot | None,
    *,
    fills: Iterable[dict[str, Any]] = (),
    observed_at: datetime | None = None,
) -> MonitoringSnapshot:
    at = observed_at or _now()
    expected_symbols = set()
    if thesis.structure:
        expected_symbols = {
            thesis.structure.long_symbol,
            thesis.structure.short_symbol,
        }
    symbol_positions = [
        position
        for position in positions
        if position.symbol in expected_symbols and abs(position.qty) > 1e-9
    ]
    if not thesis.order_id and order is None:
        symbol_positions = []
    order_status = order.status if order else thesis.order_status or "not_submitted"
    valid_order_ids = {thesis.order_id or ""}
    if order:
        valid_order_ids.add(order.order_id)
        valid_order_ids.update(
            str(leg.get("order_id") or "") for leg in order.legs
        )
    valid_order_ids.discard("")
    attributed_qty: dict[str, float] = defaultdict(float)
    for fill in fill_rows(fills):
        if (
            fill["order_id"] in valid_order_ids
            and fill["symbol"] in expected_symbols
        ):
            direction = 1.0 if fill["side"] == "buy" else -1.0
            attributed_qty[fill["symbol"]] += fill["qty"] * direction
    positions_by_symbol = {
        position.symbol: position for position in symbol_positions
    }
    attribution_complete = bool(expected_symbols) and set(attributed_qty) == expected_symbols
    attribution_matches = attribution_complete and set(positions_by_symbol) == expected_symbols
    if attribution_matches:
        attribution_matches = all(
            abs(positions_by_symbol[symbol].qty - attributed_qty[symbol])
            < 1e-9
            for symbol in expected_symbols
        )

    if symbol_positions and attribution_matches:
        market_value = sum(
            position.market_value for position in symbol_positions
        )
        unrealized = sum(
            position.unrealized_pl for position in symbol_positions
        )
        return MonitoringSnapshot(
            observed_at=at,
            attribution_status="linked_by_order_fills",
            entry_status=order_status,
            position_status=(
                f"open — {len(symbol_positions)}/{len(expected_symbols)} "
                "order-linked legs on Alpaca"
            ),
            open_leg_count=len(symbol_positions),
            expected_leg_count=len(expected_symbols),
            market_value=round(market_value, 2),
            unrealized_pl=round(unrealized, 2),
            exit_status=thesis.exit_status if thesis.exit_order_id else "monitoring",
            exit_reason=thesis.exit_reason or f"Watching invalidation: {thesis.invalidation}",
        )

    if symbol_positions:
        return MonitoringSnapshot(
            observed_at=at,
            attribution_status="ambiguous",
            entry_status=order_status,
            position_status=(
                "open symbols present — order/fill quantities do not prove "
                "this thesis owns them"
            ),
            open_leg_count=len(symbol_positions),
            expected_leg_count=len(expected_symbols),
            market_value=round(
                sum(position.market_value for position in symbol_positions),
                2,
            ),
            unrealized_pl=round(
                sum(position.unrealized_pl for position in symbol_positions),
                2,
            ),
            exit_status="unmanaged",
            exit_reason=(
                "No invalidation or exit claim is applied until Alpaca order "
                "fills reconcile exactly to the live leg quantities."
            ),
        )

    if order_status in ACTIVE_ORDER_STATUSES:
        return MonitoringSnapshot(
            observed_at=at,
            attribution_status="pending_entry",
            entry_status=order_status,
            position_status="entry pending — no open Alpaca legs yet",
            expected_leg_count=len(expected_symbols),
            exit_status="not_started",
            exit_reason="Exit monitoring starts after the entry fills.",
        )

    if order_status == "filled":
        return MonitoringSnapshot(
            observed_at=at,
            attribution_status=(
                "linked_by_order_fills"
                if attribution_complete
                else "unverified"
            ),
            entry_status=order_status,
            position_status="flat — no matching open Alpaca legs",
            expected_leg_count=len(expected_symbols),
            exit_status=thesis.exit_status if thesis.exit_order_id else "flat_unlinked",
            exit_reason=thesis.exit_reason
            or "Alpaca is flat; no exit order is linked to this thesis yet.",
        )

    if order_status in TERMINAL_UNFILLED_STATUSES:
        return MonitoringSnapshot(
            observed_at=at,
            attribution_status="not_applicable",
            entry_status=order_status,
            position_status="not opened — entry order is terminal",
            expected_leg_count=len(expected_symbols),
            exit_status="not_applicable",
            exit_reason="No filled position required an exit.",
        )

    return MonitoringSnapshot(
        observed_at=at,
        attribution_status="not_applicable",
        entry_status=order_status,
        position_status="not opened",
        expected_leg_count=len(expected_symbols),
        exit_status="not_applicable",
        exit_reason=thesis.notes or "The agent did not open a position.",
    )