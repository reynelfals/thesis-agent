from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


class SimulationError(ValueError):
    pass


def _time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise SimulationError(f"{field} must be an RFC-3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SimulationError(f"{field} is not valid RFC-3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SimulationError(f"{field} must include a timezone")
    return parsed


def _day(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise SimulationError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SimulationError(f"{field} is not a valid ISO date") from exc


def _number(value: Any, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool):
        raise SimulationError(f"{field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SimulationError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed) or parsed < minimum:
        raise SimulationError(f"{field} must be at least {minimum}")
    return parsed


def _size(value: Any, field: str) -> int:
    parsed = _number(value, field)
    if not parsed.is_integer():
        raise SimulationError(f"{field} must be an integer")
    return int(parsed)


def _quote_time(quote: dict[str, Any]) -> datetime:
    return _time(quote.get("at"), "quotes[].at")


def _validate_quote(
    quote: dict[str, Any],
    *,
    max_bid_ask_pct: float,
    long_symbol: str,
    short_symbol: str,
    required_source: str,
) -> None:
    if quote.get("source") != required_source:
        raise SimulationError("quote source does not match the frozen NBBO source")
    if quote.get("long_symbol") != long_symbol:
        raise SimulationError("long quote is not bound to the selected contract")
    if quote.get("short_symbol") != short_symbol:
        raise SimulationError("short quote is not bound to the selected contract")
    for leg in ("long", "short"):
        bid = _number(quote.get(f"{leg}_bid"), f"{leg}_bid")
        ask = _number(quote.get(f"{leg}_ask"), f"{leg}_ask")
        _size(quote.get(f"{leg}_bid_size"), f"{leg}_bid_size")
        _size(quote.get(f"{leg}_ask_size"), f"{leg}_ask_size")
        if bid <= 0 or ask <= 0:
            raise SimulationError(f"{leg} quote must be two-sided")
        if ask < bid:
            raise SimulationError(f"{leg} quote is crossed")
        midpoint = (bid + ask) / 2
        spread_pct = (ask - bid) / midpoint * 100
        if spread_pct > max_bid_ask_pct:
            raise SimulationError(
                f"{leg} quote spread {spread_pct:.2f}% exceeds "
                f"{max_bid_ask_pct:.2f}%"
            )


def _entry_debit(quote: dict[str, Any], slippage_per_leg: float) -> float:
    return round(
        _number(quote["long_ask"], "long_ask")
        + slippage_per_leg
        - (_number(quote["short_bid"], "short_bid") - slippage_per_leg),
        4,
    )


def _close_credit(quote: dict[str, Any], slippage_per_leg: float) -> float:
    return round(
        max(
            0.0,
            _number(quote["long_bid"], "long_bid")
            - slippage_per_leg
            - (_number(quote["short_ask"], "short_ask") + slippage_per_leg),
        ),
        4,
    )


def _intrinsic_value(
    *,
    side: str,
    underlying_price: float,
    long_strike: float,
    short_strike: float,
) -> float:
    if side == "bullish":
        return max(underlying_price - long_strike, 0.0) - max(
            underlying_price - short_strike,
            0.0,
        )
    return max(long_strike - underlying_price, 0.0) - max(
        short_strike - underlying_price,
        0.0,
    )


def _base_result(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": str(event.get("event_id") or ""),
        "underlying": str(event.get("underlying") or ""),
        "side": str(event.get("side") or ""),
        "regimes": (
            list(event.get("regimes"))
            if isinstance(event.get("regimes"), list)
            else []
        ),
        "signal_at": str(event.get("signal_at") or ""),
        "status": "invalid",
        "reason": "",
        "requested_qty": 0,
        "filled_qty": 0,
        "closed_qty": 0,
        "assigned_qty": 0,
        "entry_at": None,
        "exit_at": None,
        "entry_debit": None,
        "average_exit_credit": None,
        "debit_at_risk": 0.0,
        "gross_pnl": None,
        "fees": None,
        "net_pnl": None,
        "return_on_risk": None,
        "turnover": 0.0,
        "holding_days": None,
        "exit_reasons": [],
        "cash_flows": [],
    }


def _validate_provenance(
    event: dict[str, Any],
    spec: dict[str, Any],
) -> tuple[datetime, datetime, datetime, date]:
    signal_at = _time(event.get("signal_at"), "signal_at")
    features_at = _time(event.get("stock_features_as_of"), "stock_features_as_of")
    contracts_at = _time(
        event.get("contract_snapshot_at"),
        "contract_snapshot_at",
    )
    decision_at = _time(event.get("decision_recorded_at"), "decision_recorded_at")
    submitted_at = _time(event.get("submitted_at"), "submitted_at")
    expiration = _day(event.get("expiration"), "expiration")
    schedule = spec["schedule"]
    timezone = ZoneInfo(str(schedule["timezone"]))
    local_signal = signal_at.astimezone(timezone)
    expected_weekday = {
        "Monday": 0,
        "Tuesday": 1,
        "Wednesday": 2,
        "Thursday": 3,
        "Friday": 4,
    }.get(str(schedule["weekday"]))
    expected_time = time.fromisoformat(str(schedule["decision_time"]))
    scheduled_at = datetime.combine(
        local_signal.date(),
        expected_time,
        tzinfo=timezone,
    )
    tolerance = float(schedule.get("timestamp_tolerance_seconds", 0))
    if expected_weekday is None or local_signal.weekday() != expected_weekday:
        raise SimulationError("signal is outside the frozen study weekday")
    if abs((local_signal - scheduled_at).total_seconds()) > tolerance:
        raise SimulationError("signal is outside the frozen study decision time")
    if features_at >= signal_at:
        raise SimulationError(
            "stock features must end before the signal to exclude incomplete bars"
        )
    if contracts_at > decision_at:
        raise SimulationError("contract snapshot occurs after the recorded decision")
    if decision_at < signal_at:
        raise SimulationError("recorded decision predates the study signal")
    if submitted_at < decision_at:
        raise SimulationError("submission predates the recorded decision")
    if (
        submitted_at - signal_at
    ).total_seconds() > float(
        spec["execution"]["maximum_signal_to_submission_seconds"]
    ):
        raise SimulationError("submission is outside the frozen signal deadline")
    grok = spec["grok_decision"]
    if event.get("grok_model") != grok["model"]:
        raise SimulationError("Grok model does not match the frozen specification")
    if event.get("request_schema") != grok["request_schema"]:
        raise SimulationError("Grok request schema does not match the frozen specification")
    if not str(event.get("decision_record_id") or ""):
        raise SimulationError("contemporaneous decision record is required")
    selection = event.get("selection")
    if not isinstance(selection, dict):
        raise SimulationError("complete candidate selection record is required")
    stock_rank = _size(selection.get("stock_rank"), "selection.stock_rank")
    shortlist_rank = _size(
        selection.get("shortlist_rank"),
        "selection.shortlist_rank",
    )
    if stock_rank < 1 or stock_rank > int(spec["signal"]["stock_probe_limit"]):
        raise SimulationError("selected stock was outside the pre-registered probe set")
    if shortlist_rank < 1 or shortlist_rank > int(
        spec["signal"]["option_feasible_shortlist_limit"]
    ):
        raise SimulationError("decision was outside the option-feasible shortlist")
    if selection.get("side_was_feasible") is not True:
        raise SimulationError("selected side was not recorded as option-feasible")
    return signal_at, decision_at, submitted_at, expiration


def _validate_contracts(
    event: dict[str, Any],
    *,
    side: str,
    expiration: date,
    decision_at: datetime,
) -> tuple[str, str, float, float]:
    option_type = "call" if side == "bullish" else "put"
    values: list[tuple[str, float]] = []
    for leg in ("long", "short"):
        contract = event.get(f"{leg}_contract")
        if not isinstance(contract, dict):
            raise SimulationError(f"{leg} contract evidence is required")
        symbol = str(contract.get("symbol") or "")
        if not symbol or symbol != str(event.get(f"{leg}_symbol") or ""):
            raise SimulationError(f"{leg} contract symbol is inconsistent")
        strike = _number(contract.get("strike"), f"{leg}_contract.strike")
        if strike != _number(event.get(f"{leg}_strike"), f"{leg}_strike"):
            raise SimulationError(f"{leg} contract strike is inconsistent")
        if contract.get("option_type") != option_type:
            raise SimulationError(f"{leg} contract type is inconsistent")
        if _day(contract.get("expiration"), f"{leg}_contract.expiration") != expiration:
            raise SimulationError(f"{leg} contract expiration is inconsistent")
        if _time(contract.get("snapshot_at"), f"{leg}_contract.snapshot_at") > decision_at:
            raise SimulationError(f"{leg} contract snapshot uses future information")
        values.append((symbol, strike))
    return values[0][0], values[1][0], values[0][1], values[1][1]


def simulate_event(
    event: dict[str, Any],
    spec: dict[str, Any],
    *,
    equity: float,
    open_positions: int = 0,
    aggregate_debit_at_risk: float = 0.0,
) -> dict[str, Any]:
    """Simulate one pre-recorded decision using conservative paired NBBO evidence."""
    result = _base_result(event)
    try:
        signal_at, decision_at, submitted_at, expiration = _validate_provenance(
            event,
            spec,
        )
        underlying = str(event.get("underlying") or "")
        if underlying not in spec["universe"]:
            raise SimulationError("underlying is outside the frozen universe")
        side = str(event.get("side") or "")
        if side not in {"bullish", "bearish"}:
            raise SimulationError("side must be bullish or bearish")
        conviction = _number(event.get("conviction"), "conviction")
        if conviction < float(spec["grok_decision"]["minimum_conviction"]):
            raise SimulationError("conviction is below the frozen threshold")
        long_symbol, short_symbol, long_strike, short_strike = _validate_contracts(
            event,
            side=side,
            expiration=expiration,
            decision_at=decision_at,
        )
        if side == "bullish" and long_strike >= short_strike:
            raise SimulationError("bullish call spread strikes are reversed")
        if side == "bearish" and long_strike <= short_strike:
            raise SimulationError("bearish put spread strikes are reversed")
        width = abs(short_strike - long_strike)
        dte = (expiration - decision_at.date()).days
        spread_spec = spec["spread"]
        if not int(spread_spec["minimum_dte"]) <= dte <= int(
            spread_spec["maximum_dte"]
        ):
            raise SimulationError("expiration is outside the frozen DTE range")
        quotes_raw = event.get("quotes")
        if not isinstance(quotes_raw, list) or not quotes_raw:
            raise SimulationError("paired historical NBBO quotes are required")
        quotes = sorted(
            (
                quote
                for quote in quotes_raw
                if isinstance(quote, dict)
            ),
            key=_quote_time,
        )
        quote_times = [_quote_time(quote) for quote in quotes]
        if len(set(quote_times)) != len(quote_times):
            raise SimulationError("duplicate paired quote timestamp")
        execution = spec["execution"]
        max_bid_ask_pct = float(spec["signal"]["maximum_leg_bid_ask_percent"])
        for quote in quotes:
            local_quote_time = _quote_time(quote).astimezone(
                ZoneInfo(str(spec["schedule"]["timezone"]))
            )
            expiration_close = datetime.combine(
                expiration,
                time.fromisoformat(str(spec["exit"]["expiration_market_close"])),
                tzinfo=ZoneInfo(str(spec["schedule"]["timezone"])),
            )
            if local_quote_time > expiration_close:
                raise SimulationError("quote occurs after option expiration")
            _validate_quote(
                quote,
                max_bid_ask_pct=max_bid_ask_pct,
                long_symbol=long_symbol,
                short_symbol=short_symbol,
                required_source=str(execution["required_quote_source"]),
            )
        intent_quotes = [
            quote for quote in quotes if _quote_time(quote) <= submitted_at
        ]
        if not intent_quotes:
            raise SimulationError("no point-in-time intent quote precedes submission")
        intent = intent_quotes[-1]
        intent_age = (submitted_at - _quote_time(intent)).total_seconds()
        if intent_age > float(
            spec["execution"]["maximum_intent_quote_age_seconds"]
        ):
            raise SimulationError("intent quote is stale")
        natural = round(
            _number(intent["long_ask"], "long_ask")
            - _number(intent["short_bid"], "short_bid"),
            4,
        )
        if natural <= float(spread_spec["minimum_natural_debit"]):
            raise SimulationError("natural debit is below the frozen minimum")
        if natural >= width * float(
            spread_spec["maximum_debit_fraction_of_width"]
        ):
            raise SimulationError("natural debit is too rich relative to width")
        limit_debit = round(
            natural + float(spread_spec["limit_above_natural_debit"]),
            2,
        )
        risk = spec["risk"]
        if open_positions >= int(risk["maximum_open_positions"]):
            result["status"] = "rejected_risk"
            result["reason"] = "maximum open positions"
            return result
        budget = (
            equity
            * float(risk["maximum_debit_per_trade_fraction"])
            * max(conviction, float(spec["grok_decision"]["minimum_conviction"]))
        )
        requested_qty = int(budget // (limit_debit * 100))
        result["requested_qty"] = requested_qty
        if requested_qty < 1:
            result["status"] = "rejected_risk"
            result["reason"] = "risk budget cannot afford one spread"
            return result
        requested_debit = limit_debit * requested_qty * 100
        aggregate_cap = equity * float(risk["maximum_aggregate_debit_fraction"])
        if aggregate_debit_at_risk + requested_debit > aggregate_cap + 1e-9:
            result["status"] = "rejected_risk"
            result["reason"] = "aggregate debit cap"
            return result

        earliest_fill = submitted_at + timedelta(
            milliseconds=float(execution["minimum_submit_latency_milliseconds"])
        )
        latest_fill = submitted_at + timedelta(
            seconds=float(execution["entry_window_seconds"])
        )
        latest_fill = min(
            latest_fill,
            signal_at
            + timedelta(
                seconds=float(execution["maximum_signal_to_fill_seconds"])
            ),
        )
        filled_qty = 0
        fill_debit: float | None = None
        fill_at: datetime | None = None
        for quote in quotes:
            at = _quote_time(quote)
            if at < earliest_fill or at > latest_fill:
                continue
            debit = _entry_debit(
                quote,
                float(execution["slippage_per_leg"]),
            )
            if debit > limit_debit + 1e-9:
                continue
            displayed = min(
                _size(quote["long_ask_size"], "long_ask_size"),
                _size(quote["short_bid_size"], "short_bid_size"),
            )
            if displayed < 1:
                continue
            filled_qty = min(requested_qty, displayed)
            fill_debit = debit
            fill_at = at
            break
        if not fill_at or fill_debit is None:
            result["status"] = "unfilled"
            result["reason"] = "no conservative fill inside the entry window"
            return result

        result["filled_qty"] = filled_qty
        result["entry_at"] = fill_at.isoformat()
        result["entry_debit"] = fill_debit
        result["debit_at_risk"] = round(fill_debit * filled_qty * 100, 2)
        remaining = filled_qty
        exit_notional = 0.0
        exit_reasons: list[str] = []
        exit_fills: list[dict[str, Any]] = []
        last_exit_at: datetime | None = None
        exit_spec = spec["exit"]
        option_fee = float(execution["option_fee_per_contract_per_leg"])
        regulatory_fee = float(
            execution["closing_regulatory_fee_per_contract"]
        )
        assignment_fee = float(exit_spec["assignment_fee_per_contract"])
        profit_credit = fill_debit * (
            1 + float(exit_spec["profit_target_fraction_of_entry_debit"])
        )
        stop_credit = fill_debit * (
            1 - float(exit_spec["stop_loss_fraction_of_entry_debit"])
        )
        for quote in quotes:
            at = _quote_time(quote)
            if at <= fill_at or remaining <= 0:
                continue
            credit = _close_credit(
                quote,
                float(exit_spec["close_slippage_per_leg"]),
            )
            quote_dte = (expiration - at.date()).days
            reason = ""
            if credit >= profit_credit:
                reason = "profit_target"
            elif credit <= stop_credit:
                reason = "stop_loss"
            elif quote_dte <= int(exit_spec["close_by_dte"]):
                reason = "time_exit"
            if not reason:
                continue
            displayed = min(
                _size(quote["long_bid_size"], "long_bid_size"),
                _size(quote["short_ask_size"], "short_ask_size"),
            )
            close_qty = min(remaining, displayed)
            if close_qty < 1:
                continue
            exit_notional += credit * close_qty
            remaining -= close_qty
            last_exit_at = at
            exit_reasons.append(reason)
            exit_fills.append(
                {
                    "at": at.isoformat(),
                    "kind": "close",
                    "qty": close_qty,
                    "credit": credit,
                    "fees": round(
                        close_qty * (2 * option_fee + 2 * regulatory_fee),
                        8,
                    ),
                }
            )

        assigned_qty = 0
        if remaining:
            expiration_mark = event.get("expiration_underlying_mark")
            if isinstance(expiration_mark, dict):
                mark_at = _time(
                    expiration_mark.get("at"),
                    "expiration_underlying_mark.at",
                )
                local_mark = mark_at.astimezone(
                    ZoneInfo(str(spec["schedule"]["timezone"]))
                )
                if local_mark.date() != expiration or local_mark.time() < time(16, 0):
                    raise SimulationError(
                        "expiration mark is not timestamped after the expiration close"
                    )
                if expiration_mark.get("source") != exit_spec[
                    "required_underlying_settlement_source"
                ]:
                    raise SimulationError("expiration mark source is not approved")
                expiration_price = _number(
                    expiration_mark.get("price"),
                    "expiration_underlying_mark.price",
                )
                settlement = max(
                    0.0,
                    min(
                        width,
                        _intrinsic_value(
                            side=side,
                            underlying_price=expiration_price,
                            long_strike=long_strike,
                            short_strike=short_strike,
                        ),
                    ),
                )
                exit_notional += settlement * remaining
                assigned_qty = remaining
                exit_fills.append(
                    {
                        "at": mark_at.isoformat(),
                        "kind": "expiration_settlement",
                        "qty": remaining,
                        "credit": settlement,
                        "fees": round(remaining * 2 * assignment_fee, 8),
                    }
                )
                remaining = 0
                last_exit_at = mark_at
                exit_reasons.append("expiration_settlement")
        result["closed_qty"] = filled_qty - assigned_qty - remaining
        result["assigned_qty"] = assigned_qty
        result["exit_reasons"] = exit_reasons
        if remaining:
            result["status"] = "open_unresolved"
            result["reason"] = "position lacks a complete close or expiration mark"
            return result

        closed_qty = int(result["closed_qty"])
        fees = (
            filled_qty * 2 * option_fee
            + closed_qty * (2 * option_fee + 2 * regulatory_fee)
            + assigned_qty * 2 * assignment_fee
        )
        gross_pnl = (exit_notional - fill_debit * filled_qty) * 100
        net_pnl = gross_pnl - fees
        average_exit = exit_notional / filled_qty
        debit_at_risk = fill_debit * filled_qty * 100
        result.update(
            {
                "status": "completed",
                "reason": (
                    "partial entry quantity; unfilled remainder cancelled"
                    if filled_qty < requested_qty
                    else "completed"
                ),
                "exit_at": last_exit_at.isoformat() if last_exit_at else None,
                "average_exit_credit": round(average_exit, 4),
                "gross_pnl": round(gross_pnl, 2),
                "fees": round(fees, 2),
                "net_pnl": round(net_pnl, 2),
                "return_on_risk": round(net_pnl / debit_at_risk, 8),
                "turnover": round(
                    (fill_debit * filled_qty + exit_notional) * 100,
                    2,
                ),
                "holding_days": (
                    round((last_exit_at - fill_at).total_seconds() / 86400, 6)
                    if last_exit_at
                    else None
                ),
                "cash_flows": [
                    {
                        "at": fill_at.isoformat(),
                        "kind": "entry",
                        "qty": filled_qty,
                        "amount": round(
                            -(fill_debit * filled_qty * 100)
                            - (filled_qty * 2 * option_fee),
                            8,
                        ),
                    },
                    *[
                        {
                            "at": fill["at"],
                            "kind": fill["kind"],
                            "qty": fill["qty"],
                            "amount": round(
                                float(fill["credit"])
                                * int(fill["qty"])
                                * 100
                                - float(fill["fees"]),
                                8,
                            ),
                        }
                        for fill in exit_fills
                    ],
                ],
            }
        )
        return result
    except SimulationError as exc:
        result["status"] = "invalid"
        result["reason"] = str(exc)
        return result