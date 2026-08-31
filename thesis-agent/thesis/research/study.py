from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from thesis.research.historical import (
    HistoricalDataError,
    classify_validation_regimes,
    completed_bar_snapshot,
    rank_universe_as_of,
    select_vertical_as_of,
)
from thesis.research.metrics import (
    equity_metrics,
    metrics_by_regime,
    trade_metrics,
)
from thesis.research.simulation import simulate_event
from thesis.research.spec import (
    PINNED_STRATEGY_SPEC_SHA256,
    strategy_spec_digest,
    verify_runtime_fingerprints,
)


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value.lower())


def canonical_dataset_digest(dataset: dict[str, Any]) -> str:
    """Hash parsed dataset content while excluding the detached digest field."""
    content = dict(dataset)
    content.pop("_artifact_sha256", None)
    encoded = json.dumps(
        content,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_errors(
    manifest: dict[str, Any],
    artifact_root: Path | None,
) -> list[str]:
    errors: list[str] = []
    if manifest.get("provider") != "OPRA":
        errors.append("raw source manifest provider must be OPRA")
    if manifest.get("immutable") is not True:
        errors.append("raw source manifest must be immutable")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return errors + ["source manifest artifacts are missing"]
    roles: set[str] = set()
    root = artifact_root.resolve() if artifact_root else None
    if root is None:
        errors.append("dataset artifact root is required to verify source files")
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            errors.append(f"source artifact {index} is invalid")
            continue
        role = str(artifact.get("role") or "")
        relative = str(artifact.get("path") or "")
        expected = artifact.get("sha256")
        if role not in {"raw_archive", "query_manifest"} or role in roles:
            errors.append(f"source artifact {index} has an invalid or duplicate role")
        roles.add(role)
        if not _is_sha256(expected):
            errors.append(f"source artifact {index} SHA-256 is invalid")
        if not relative or Path(relative).is_absolute() or root is None:
            errors.append(f"source artifact {index} path is invalid")
            continue
        source = (root / relative).resolve()
        if not source.is_relative_to(root):
            errors.append(f"source artifact {index} escapes the dataset directory")
            continue
        try:
            actual = _file_sha256(source)
        except OSError:
            errors.append(f"source artifact {index} cannot be read")
            continue
        if _is_sha256(expected) and actual != expected:
            errors.append(f"source artifact {index} SHA-256 does not match")
    if roles != {"raw_archive", "query_manifest"}:
        errors.append("source manifest must bind raw_archive and query_manifest files")
    return errors


def _metadata_errors(
    dataset: dict[str, Any],
    spec: dict[str, Any],
    *,
    artifact_root: Path | None,
) -> list[str]:
    errors: list[str] = []
    if dataset.get("schema_version") != spec["validation"]["dataset_schema"]:
        errors.append("unsupported or missing dataset schema")
    if not _is_sha256(dataset.get("_artifact_sha256")):
        errors.append("content-addressed dataset artifact hash is missing")
    elif dataset["_artifact_sha256"] != canonical_dataset_digest(dataset):
        errors.append("dataset artifact SHA-256 does not match canonical content")
    metadata = dataset.get("metadata")
    if not isinstance(metadata, dict):
        return errors + ["dataset metadata is missing"]
    required_true = {
        "point_in_time": "point-in-time capture is not declared",
        "completed_bars_only": "completed-bar filtering is not declared",
        "selection_log_complete": "candidate selection log is incomplete",
        "corporate_actions_adjusted": "corporate-action handling is not declared",
        "walk_forward": "walk-forward evaluation is not declared",
        "untouched_out_of_sample": "untouched out-of-sample evaluation is not declared",
        "daily_mark_to_market": "daily mark-to-market equity is not declared",
        "holdout_started_flat": "holdout must begin without carried positions",
    }
    for field, message in required_true.items():
        if metadata.get(field) is not True:
            errors.append(message)
    if metadata.get("nbbo_source") != spec["validation"]["required_nbbo_source"]:
        errors.append("historical OPRA NBBO evidence is required")
    if metadata.get("survivorship_bias_control") != "point_in_time_membership":
        errors.append("point-in-time universe membership is required")
    if metadata.get("grok_decisions") != "contemporaneous_recorded":
        errors.append("contemporaneously recorded Grok decisions are required")
    variants = metadata.get("declared_variants")
    if isinstance(variants, bool) or not isinstance(variants, int) or variants < 1:
        errors.append("declared_variants must be a positive integer")
    try:
        initial_equity = float(metadata.get("initial_equity"))
        if not math.isfinite(initial_equity) or initial_equity <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("initial_equity must be finite and positive")
    manifest = metadata.get("source_manifest")
    if not isinstance(manifest, dict):
        errors.append("raw source manifest is missing")
    else:
        errors.extend(_manifest_errors(manifest, artifact_root))
    folds = metadata.get("walk_forward_folds")
    fold_ids: set[str] = set()
    if not isinstance(folds, list) or not folds:
        errors.append("walk-forward fold definitions are missing")
    else:
        for index, fold in enumerate(folds):
            if not isinstance(fold, dict):
                errors.append(f"walk-forward fold {index} is invalid")
                continue
            fold_id = str(fold.get("fold_id") or "")
            if not fold_id or fold_id in fold_ids:
                errors.append(f"walk-forward fold {index} has an invalid ID")
            fold_ids.add(fold_id)
            try:
                train_start = _time(fold.get("train_start"))
                train_end = _time(fold.get("train_end"))
                test_start = _time(fold.get("test_start"))
                test_end = _time(fold.get("test_end"))
            except (TypeError, ValueError):
                errors.append(f"walk-forward fold {index} has invalid timestamps")
                continue
            if not train_start < train_end < test_start < test_end:
                errors.append(f"walk-forward fold {index} overlaps or is reversed")
    holdout = metadata.get("untouched_holdout")
    if not isinstance(holdout, dict):
        errors.append("untouched holdout definition is missing")
    else:
        try:
            holdout_start = _time(holdout.get("start"))
            holdout_end = _time(holdout.get("end"))
            sealed_at = _time(holdout.get("sealed_at"))
            if not sealed_at <= holdout_start < holdout_end:
                errors.append("untouched holdout was not sealed before evaluation")
            if isinstance(folds, list):
                for fold in folds:
                    if not isinstance(fold, dict):
                        continue
                    try:
                        if _time(fold.get("test_end")) >= holdout_start:
                            errors.append(
                                "walk-forward test periods overlap the untouched holdout"
                            )
                    except (TypeError, ValueError):
                        pass
        except (TypeError, ValueError):
            errors.append("untouched holdout timestamps are invalid")
    return errors


def _partition_for_event(
    event: dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[str, str | None]:
    partition = str(event.get("evaluation_partition") or "")
    try:
        signal_at = _time(event.get("signal_at"))
    except (TypeError, ValueError):
        return partition, "event signal timestamp is invalid"
    holdout = metadata.get("untouched_holdout")
    if partition == "untouched_holdout":
        if not isinstance(holdout, dict):
            return partition, "holdout partition is undefined"
        try:
            if _time(holdout["start"]) <= signal_at < _time(holdout["end"]):
                return partition, None
        except (KeyError, TypeError, ValueError):
            pass
        return partition, "event is labeled holdout but falls outside the holdout"
    if partition.startswith("walk_forward:"):
        fold_id = partition.split(":", 1)[1]
        folds = metadata.get("walk_forward_folds")
        if isinstance(folds, list):
            for fold in folds:
                if not isinstance(fold, dict) or fold.get("fold_id") != fold_id:
                    continue
                try:
                    if _time(fold["test_start"]) <= signal_at < _time(
                        fold["test_end"]
                    ):
                        return partition, None
                except (KeyError, TypeError, ValueError):
                    break
        return partition, "event is not inside its walk-forward test fold"
    return partition, "event evaluation_partition is missing or unsupported"


def _canonical_value_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _grok_record_errors(
    event: dict[str, Any],
    spec: dict[str, Any],
    artifact_root: Path | None,
) -> list[str]:
    errors: list[str] = []
    reference = event.get("grok_record")
    if not isinstance(reference, dict):
        return ["content-addressed Grok decision artifact is required"]
    relative = str(reference.get("path") or "")
    expected_hash = reference.get("sha256")
    if not _is_sha256(expected_hash):
        errors.append("Grok decision artifact SHA-256 is invalid")
    if (
        artifact_root is None
        or not relative
        or Path(relative).is_absolute()
    ):
        return errors + ["Grok decision artifact path is invalid"]
    root = artifact_root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        return errors + ["Grok decision artifact escapes the dataset directory"]
    try:
        raw = path.read_bytes()
    except OSError:
        return errors + ["Grok decision artifact cannot be read"]
    actual_hash = hashlib.sha256(raw).hexdigest()
    if _is_sha256(expected_hash) and actual_hash != expected_hash:
        errors.append("Grok decision artifact SHA-256 does not match")
    try:
        record = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return errors + ["Grok decision artifact is not valid JSON"]
    if not isinstance(record, dict):
        return errors + ["Grok decision artifact must be a JSON object"]
    frozen = spec["grok_decision"]
    if record.get("schema_version") != frozen["artifact_schema"]:
        errors.append("Grok decision artifact schema does not match")
    if record.get("record_id") != event.get("decision_record_id"):
        errors.append("Grok decision artifact record ID does not match event")
    request = record.get("request")
    response = record.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        return errors + ["Grok request and response records are required"]
    try:
        signal_at = _time(event.get("signal_at"))
        decision_at = _time(event.get("decision_recorded_at"))
        request_at = _time(request.get("recorded_at"))
        response_at = _time(response.get("recorded_at"))
        if not signal_at <= request_at <= response_at == decision_at:
            errors.append("Grok artifact timestamps do not bind to the decision")
    except (TypeError, ValueError):
        errors.append("Grok artifact timestamps are invalid")
    if request.get("model") != frozen["model"]:
        errors.append("Grok artifact model does not match")
    if request.get("request_schema") != frozen["request_schema"]:
        errors.append("Grok artifact request schema does not match")
    expected_selection_hash = _canonical_value_sha256(
        event.get("selection_log")
    )
    if request.get("selection_log_sha256") != expected_selection_hash:
        errors.append("Grok request is not bound to the candidate selection log")
    required = [str(field) for field in frozen["required_fields"]]
    for field in required:
        if field not in response:
            errors.append(f"Grok response field {field} is missing")
    for field in (
        "underlying",
        "direction",
        "regime",
        "setup",
        "invalidation",
        "horizon",
        "iv_note",
    ):
        if field in response and not str(response.get(field) or "").strip():
            errors.append(f"Grok response field {field} is empty")
    try:
        expected_move = float(response.get("expected_move_pct"))
        conviction = float(response.get("conviction"))
        if not math.isfinite(expected_move):
            raise ValueError
        if not math.isfinite(conviction) or not 0 <= conviction <= 1:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Grok numeric response fields are invalid")
    if response.get("underlying") != event.get("underlying"):
        errors.append("Grok response underlying does not match event")
    if response.get("direction") != event.get("side"):
        errors.append("Grok response direction does not match event")
    try:
        if abs(float(response.get("conviction")) - float(event.get("conviction"))) > 1e-9:
            errors.append("Grok response conviction does not match event")
    except (TypeError, ValueError):
        pass
    return errors


def _event_evidence_errors(
    event: dict[str, Any],
    spec: dict[str, Any],
    *,
    artifact_root: Path | None,
) -> list[str]:
    errors: list[str] = []
    try:
        signal_at = _time(event.get("signal_at"))
        decision_at = _time(event.get("decision_recorded_at"))
    except (TypeError, ValueError):
        return ["event evidence timestamps are invalid"]
    bars_by_symbol = event.get("universe_bars")
    spots_raw = event.get("stock_spots")
    expected_symbols = set(str(symbol) for symbol in spec["universe"])
    if not isinstance(bars_by_symbol, dict) or set(bars_by_symbol) != expected_symbols:
        return ["raw completed bars for the entire frozen universe are required"]
    if not isinstance(spots_raw, dict) or set(spots_raw) != expected_symbols:
        return ["point-in-time stock spots for the entire universe are required"]
    spots: dict[str, float] = {}
    history_sessions = int(spec["signal"]["history_sessions"])
    required_spot_source = spec["signal"]["required_stock_spot_source"]
    for symbol in sorted(expected_symbols):
        bars = bars_by_symbol.get(symbol)
        if not isinstance(bars, list):
            errors.append(f"{symbol}: stock bars are missing")
            continue
        completed = 0
        seen_ends: set[datetime] = set()
        for bar in bars:
            if not isinstance(bar, dict):
                errors.append(f"{symbol}: stock bar is invalid")
                continue
            try:
                end_at = _time(bar.get("end_at"))
            except (TypeError, ValueError):
                errors.append(f"{symbol}: stock bar completion time is invalid")
                continue
            if end_at in seen_ends:
                errors.append(f"{symbol}: duplicate stock bar completion time")
            seen_ends.add(end_at)
            if end_at < signal_at:
                completed += 1
        if completed < history_sessions:
            errors.append(
                f"{symbol}: {completed} completed bars; {history_sessions} required"
            )
        spot = spots_raw.get(symbol)
        if not isinstance(spot, dict):
            errors.append(f"{symbol}: point-in-time spot record is missing")
            continue
        try:
            spot_at = _time(spot.get("at"))
            spot_price = float(spot.get("price"))
            if (
                not math.isfinite(spot_price)
                or spot_price <= 0
                or not signal_at <= spot_at <= decision_at
            ):
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{symbol}: point-in-time spot record is invalid")
            continue
        if spot.get("source") != required_spot_source:
            errors.append(f"{symbol}: point-in-time spot source is not approved")
        spots[symbol] = spot_price
    if errors:
        return errors
    try:
        expected_ranking = rank_universe_as_of(
            bars_by_symbol,
            as_of=event["signal_at"],
            spots_by_symbol=spots,
        )
    except (HistoricalDataError, KeyError, TypeError, ValueError) as exc:
        return [f"full-universe ranking cannot be reproduced: {exc}"]
    classifier = spec["validation"]["regime_classifier"]
    classifier_symbol = str(classifier["symbol"])
    try:
        classifier_snapshot = completed_bar_snapshot(
            classifier_symbol,
            bars_by_symbol[classifier_symbol],
            as_of=event["signal_at"],
            spot=spots[classifier_symbol],
        )
        expected_regimes = classify_validation_regimes(
            classifier_snapshot,
            high_volatility_threshold=float(
                classifier["high_volatility_threshold"]
            ),
        )
    except (HistoricalDataError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"objective regimes cannot be reproduced: {exc}")
        expected_regimes = []
    recorded_regimes = event.get("regimes")
    if (
        not isinstance(recorded_regimes, list)
        or sorted(str(value) for value in recorded_regimes)
        != sorted(expected_regimes)
    ):
        errors.append("recorded regime labels do not match the frozen classifier")
    selection_log = event.get("selection_log")
    if not isinstance(selection_log, list) or len(selection_log) != len(
        expected_symbols
    ):
        return ["complete 29-symbol candidate and abstention log is required"]
    errors.extend(_grok_record_errors(event, spec, artifact_root))
    recorded = {
        str(row.get("symbol") or ""): row
        for row in selection_log
        if isinstance(row, dict)
    }
    if set(recorded) != expected_symbols:
        return ["selection log symbols do not match the frozen universe"]
    for expected in expected_ranking:
        symbol = str(expected["symbol"])
        row = recorded[symbol]
        if row.get("stock_rank") != expected["stock_rank"]:
            errors.append(f"{symbol}: recorded stock rank is not reproducible")
        try:
            if abs(float(row.get("stock_score")) - float(expected["stock_score"])) > 1e-8:
                errors.append(f"{symbol}: recorded stock score is not reproducible")
        except (TypeError, ValueError):
            errors.append(f"{symbol}: recorded stock score is invalid")
        if row.get("factors") != expected["factors"]:
            errors.append(f"{symbol}: recorded factor scores are not reproducible")
        for field in ("status", "reason", "probed", "feasible_sides"):
            if field not in row:
                errors.append(f"{symbol}: selection log field {field} is missing")
        if int(expected["stock_rank"]) <= int(spec["signal"]["stock_probe_limit"]):
            if not _is_sha256(row.get("option_probe_evidence_sha256")):
                errors.append(f"{symbol}: option probe evidence hash is missing")
    underlying = str(event.get("underlying") or "")
    selected = recorded.get(underlying)
    selection = event.get("selection")
    if not selected or not isinstance(selection, dict):
        errors.append("selected candidate is absent from the full selection log")
        return errors
    if selection.get("stock_rank") != selected.get("stock_rank"):
        errors.append("selected stock rank conflicts with the full selection log")
    feasible_sides = selected.get("feasible_sides")
    if not isinstance(feasible_sides, list) or event.get("side") not in feasible_sides:
        errors.append("selected direction is not option-feasible in the selection log")
    feasible_rows = [
        row
        for row in selection_log
        if isinstance(row, dict) and row.get("status") == "feasible"
    ]
    try:
        feasible_rows.sort(
            key=lambda row: (
                -float(row["total_score"]),
                int(row["stock_rank"]),
                str(row["symbol"]),
            )
        )
    except (KeyError, TypeError, ValueError):
        errors.append("option-feasible shortlist scores are invalid")
        return errors
    shortlisted = feasible_rows[: int(spec["signal"]["option_feasible_shortlist_limit"])]
    shortlist_symbols = [str(row["symbol"]) for row in shortlisted]
    if underlying not in shortlist_symbols:
        errors.append("selected candidate is outside the reproducible shortlist")
    elif selection.get("shortlist_rank") != shortlist_symbols.index(underlying) + 1:
        errors.append("selected shortlist rank is not reproducible")
    chain = event.get("contract_chain")
    if not isinstance(chain, list):
        errors.append("point-in-time option contract chain is missing")
        return errors
    try:
        pair = select_vertical_as_of(
            chain,
            spot=spots[underlying],
            side=str(event.get("side") or ""),
            as_of=event["decision_recorded_at"],
            minimum_dte=int(spec["spread"]["minimum_dte"]),
            maximum_dte=int(spec["spread"]["maximum_dte"]),
            target_dte=int(spec["spread"]["target_dte"]),
        )
    except (HistoricalDataError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"selected option pair cannot be reproduced: {exc}")
        return errors
    for field in (
        "expiration",
        "long_symbol",
        "short_symbol",
        "long_strike",
        "short_strike",
    ):
        if event.get(field) != pair[field]:
            errors.append(f"selected option pair field {field} is not reproducible")
    return errors


def _valuation_calendar(
    dataset: dict[str, Any],
    spec: dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[list[datetime], list[str]]:
    errors: list[str] = []
    if "equity_curves" in dataset:
        errors.append(
            "operator-supplied equity curves are prohibited; curves must be derived"
        )
    raw = dataset.get("valuation_calendar")
    if not isinstance(raw, list):
        return [], errors + ["valuation calendar is missing"]
    try:
        parsed = [_time(value) for value in raw]
    except (TypeError, ValueError):
        return [], errors + ["valuation calendar timestamps are invalid"]
    minimum = int(spec["validation"]["minimum_daily_equity_observations"])
    if len(parsed) < minimum:
        errors.append(
            f"valuation calendar: {len(parsed)} daily observations; {minimum} required"
        )
    if parsed != sorted(parsed) or len(set(parsed)) != len(parsed):
        errors.append("valuation calendar is not strictly increasing")
    if len({value.date() for value in parsed}) != len(parsed):
        errors.append("valuation calendar has multiple observations on one date")
    if any(
        (current - previous).days > 4
        for previous, current in zip(parsed, parsed[1:])
    ):
        errors.append("valuation calendar contains a gap over four days")
    holdout = metadata.get("untouched_holdout")
    try:
        holdout_start = _time(holdout["start"])
        holdout_end = _time(holdout["end"])
    except (KeyError, TypeError, ValueError):
        return parsed, errors + [
            "valuation calendar cannot be checked without a valid holdout"
        ]
    if parsed and (parsed[0] < holdout_start or parsed[-1] >= holdout_end):
        errors.append("valuation calendar extends outside the untouched holdout")
    if parsed and (parsed[0] - holdout_start).days > 4:
        errors.append("valuation calendar starts too late in the holdout")
    if parsed and (holdout_end - parsed[-1]).days > 4:
        errors.append("valuation calendar ends too early in the holdout")
    return parsed, errors


def _curve_integrity_errors(
    dataset: dict[str, Any],
    spec: dict[str, Any],
    metadata: dict[str, Any],
) -> list[str]:
    """Compatibility wrapper for calendar and anti-fabrication checks."""
    return _valuation_calendar(dataset, spec, metadata)[1]


def _derived_strategy_curve(
    dataset: dict[str, Any],
    events: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    calendar: list[datetime],
    spec: dict[str, Any],
    *,
    initial_equity: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    event_map: dict[str, dict[str, Any]] = {}
    for event in events:
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in event_map:
            errors.append("holdout event IDs must be nonempty and unique")
        else:
            event_map[event_id] = event
    completed = [trade for trade in trades if trade.get("status") == "completed"]
    if calendar:
        for trade in completed:
            try:
                if _time(trade["entry_at"]) <= calendar[0]:
                    errors.append(
                        f"{trade.get('event_id')}: entry predates the first valuation"
                    )
                if _time(trade["exit_at"]) > calendar[-1]:
                    errors.append(
                        f"{trade.get('event_id')}: exit follows the final valuation"
                    )
            except (KeyError, TypeError, ValueError):
                errors.append(
                    f"{trade.get('event_id')}: lifecycle endpoints are invalid"
                )
    for trade in completed:
        cash_flows = trade.get("cash_flows")
        if not isinstance(cash_flows, list) or not cash_flows:
            errors.append(f"{trade.get('event_id')}: derived cash flows are missing")
            continue
        try:
            total = sum(float(flow["amount"]) for flow in cash_flows)
            if abs(total - float(trade["net_pnl"])) > 0.01:
                errors.append(
                    f"{trade.get('event_id')}: cash flows do not reconcile to net P&L"
                )
        except (KeyError, TypeError, ValueError):
            errors.append(f"{trade.get('event_id')}: cash flows are invalid")
    curve: list[dict[str, Any]] = []
    close_slippage = float(spec["exit"]["close_slippage_per_leg"])
    option_fee = float(spec["execution"]["option_fee_per_contract_per_leg"])
    regulatory_fee = float(
        spec["execution"]["closing_regulatory_fee_per_contract"]
    )
    for at in calendar:
        cash = initial_equity
        liquidation_value = 0.0
        for trade in completed:
            event_id = str(trade.get("event_id") or "")
            event = event_map.get(event_id)
            if event is None:
                errors.append(f"{event_id}: trade has no matching raw event")
                continue
            cash_flows = trade.get("cash_flows")
            if not isinstance(cash_flows, list):
                continue
            try:
                for flow in cash_flows:
                    if _time(flow["at"]) <= at:
                        cash += float(flow["amount"])
                entry_at = _time(trade["entry_at"])
                exited_qty = sum(
                    int(flow["qty"])
                    for flow in cash_flows
                    if flow.get("kind") != "entry" and _time(flow["at"]) <= at
                )
                remaining = (
                    int(trade["filled_qty"]) - exited_qty
                    if entry_at <= at
                    else 0
                )
            except (KeyError, TypeError, ValueError):
                errors.append(f"{event_id}: lifecycle cash-flow evidence is invalid")
                continue
            if remaining <= 0:
                continue
            exact_quotes = [
                quote
                for quote in event.get("quotes", [])
                if isinstance(quote, dict)
                and isinstance(quote.get("at"), str)
                and _time(quote["at"]) == at
            ]
            if len(exact_quotes) != 1:
                errors.append(
                    f"{event_id}: exactly one paired NBBO mark is required at "
                    f"{at.isoformat()}"
                )
                continue
            quote = exact_quotes[0]
            try:
                close_credit = max(
                    0.0,
                    float(quote["long_bid"])
                    - close_slippage
                    - (float(quote["short_ask"]) + close_slippage),
                )
                close_fees = remaining * (
                    2 * option_fee + 2 * regulatory_fee
                )
                liquidation_value += close_credit * remaining * 100 - close_fees
            except (KeyError, TypeError, ValueError):
                errors.append(f"{event_id}: daily NBBO mark is invalid")
        curve.append(
            {
                "at": at.isoformat(),
                "equity": round(cash + liquidation_value, 2),
            }
        )
    return curve, sorted(set(errors))


def _derived_benchmark_curves(
    dataset: dict[str, Any],
    calendar: list[datetime],
    spec: dict[str, Any],
    *,
    initial_equity: float,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    errors: list[str] = []
    curves: dict[str, list[dict[str, Any]]] = {
        "CASH": [
            {"at": at.isoformat(), "equity": round(initial_equity, 2)}
            for at in calendar
        ]
    }
    inputs = dataset.get("benchmark_inputs")
    spy_points = inputs.get("SPY_BUY_HOLD") if isinstance(inputs, dict) else None
    if not isinstance(spy_points, list):
        return curves, ["SPY buy-and-hold SIP price inputs are missing"]
    parsed: list[tuple[datetime, float]] = []
    for index, point in enumerate(spy_points):
        if not isinstance(point, dict):
            errors.append(f"SPY benchmark point {index} is invalid")
            continue
        try:
            at = _time(point.get("at"))
            price = float(point.get("price"))
            if not math.isfinite(price) or price <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"SPY benchmark point {index} is invalid")
            continue
        if point.get("source") != spec["validation"]["benchmark_spot_source"]:
            errors.append(f"SPY benchmark point {index} source is not approved")
        parsed.append((at, price))
    if [at for at, _ in parsed] != calendar:
        errors.append("SPY benchmark prices do not align exactly to valuation calendar")
        return curves, errors
    if not parsed:
        errors.append("SPY benchmark has no prices")
        return curves, errors
    first_price = parsed[0][1]
    curves["SPY_BUY_HOLD"] = [
        {
            "at": at.isoformat(),
            "equity": round(initial_equity * price / first_price, 2),
        }
        for at, price in parsed
    ]
    return curves, errors


def _criterion(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _source_artifact_identities(metadata: dict[str, Any]) -> dict[str, str]:
    manifest = metadata.get("source_manifest")
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(artifacts, list):
        return {}
    return {
        str(artifact.get("role")): str(artifact.get("sha256"))
        for artifact in artifacts
        if isinstance(artifact, dict)
        and artifact.get("role") in {"raw_archive", "query_manifest"}
        and _is_sha256(artifact.get("sha256"))
    }


def _holdout_window(metadata: dict[str, Any]) -> dict[str, str]:
    holdout = metadata.get("untouched_holdout")
    if not isinstance(holdout, dict):
        return {}
    start = holdout.get("start")
    end = holdout.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        return {}
    return {"start": start, "end": end}


def _windows_disjoint(
    first: dict[str, Any],
    second: dict[str, Any],
) -> bool:
    try:
        first_start = _time(first["start"])
        first_end = _time(first["end"])
        second_start = _time(second["start"])
        second_end = _time(second["end"])
    except (KeyError, TypeError, ValueError):
        return False
    return first_end <= second_start or second_end <= first_start


def _replication_criterion(
    metadata: dict[str, Any],
    replication_report: dict[str, Any] | None,
    *,
    spec_digest: str,
    primary_event_keys: list[str],
) -> dict[str, Any]:
    replication = replication_report or {}
    replication_evidence = replication.get("verified_evidence")
    evidence = (
        replication_evidence
        if isinstance(replication_evidence, dict)
        else {}
    )
    primary_sources = _source_artifact_identities(metadata)
    replication_sources = evidence.get("source_artifacts")
    replication_sources = (
        replication_sources if isinstance(replication_sources, dict) else {}
    )
    replication_event_keys = evidence.get("holdout_event_keys")
    replication_event_keys = (
        [str(value) for value in replication_event_keys]
        if isinstance(replication_event_keys, list)
        else []
    )
    criteria = replication.get("criteria")
    passed = (
        replication.get("status") == "replication_criteria_passed"
        and replication.get("strategy_spec_sha256") == spec_digest
        and _is_sha256(replication.get("dataset_artifact_sha256"))
        and str(replication.get("dataset_id") or "")
        and replication.get("dataset_id") != metadata.get("dataset_id")
        and not replication.get("data_integrity_errors")
        and bool(criteria)
        and all(
            isinstance(criterion, dict) and criterion.get("passed") is True
            for criterion in criteria
        )
        and _is_sha256(primary_sources.get("raw_archive"))
        and _is_sha256(replication_sources.get("raw_archive"))
        and primary_sources["raw_archive"]
        != replication_sources["raw_archive"]
        and set(primary_event_keys).isdisjoint(replication_event_keys)
        and bool(primary_event_keys)
        and bool(replication_event_keys)
        and _windows_disjoint(
            _holdout_window(metadata),
            evidence.get("holdout_window")
            if isinstance(evidence.get("holdout_window"), dict)
            else {},
        )
    )
    return _criterion(
        "independent_replication",
        bool(passed),
        (
            "a separately loaded dataset with a distinct verified archive and "
            "disjoint holdout evidence passed the same criteria"
            if passed
            else "replication must use a distinct verified archive, disjoint "
            "holdout window/events, and pass the same evaluator"
        ),
    )


def evaluate_dataset(
    dataset: dict[str, Any],
    spec: dict[str, Any],
    *,
    artifact_root: Path | None = None,
    independent_replication_report: dict[str, Any] | None = None,
    require_independent_replication: bool = True,
) -> dict[str, Any]:
    """Evaluate only pre-declared test/holdout events and fail closed."""
    metadata_errors = _metadata_errors(
        dataset,
        spec,
        artifact_root=artifact_root,
    )
    if strategy_spec_digest(spec) != PINNED_STRATEGY_SPEC_SHA256:
        metadata_errors.append("strategy specification is not the pre-registered version")
    runtime_drift = verify_runtime_fingerprints(spec)
    metadata = (
        dataset.get("metadata")
        if isinstance(dataset.get("metadata"), dict)
        else {}
    )
    events_raw = dataset.get("events")
    events = (
        [event for event in events_raw if isinstance(event, dict)]
        if isinstance(events_raw, list)
        else []
    )
    if not events:
        metadata_errors.append("dataset contains no decision events")
    try:
        events.sort(key=lambda event: _time(event.get("signal_at")))
    except (TypeError, ValueError):
        metadata_errors.append("event signal timestamps are invalid")
    for event in events:
        partition, partition_error = _partition_for_event(event, metadata)
        event["_validated_partition"] = partition
        if partition_error:
            metadata_errors.append(
                f"{event.get('event_id') or 'event'}: {partition_error}"
            )
        for error in _event_evidence_errors(
            event,
            spec,
            artifact_root=artifact_root,
        ):
            metadata_errors.append(
                f"{event.get('event_id') or 'event'}: {error}"
            )

    try:
        initial_equity = float(metadata.get("initial_equity"))
    except (TypeError, ValueError):
        initial_equity = 100_000.0
    realized_equity = initial_equity
    active: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    for event in events:
        try:
            signal_at = _time(event.get("signal_at"))
        except (TypeError, ValueError):
            trades.append(
                {
                    "event_id": str(event.get("event_id") or ""),
                    "status": "invalid",
                    "reason": "signal_at is invalid",
                    "regimes": [],
                    "evaluation_partition": event.get("_validated_partition"),
                }
            )
            continue
        still_active: list[dict[str, Any]] = []
        for trade in active:
            exit_at_raw = trade.get("exit_at")
            if isinstance(exit_at_raw, str) and _time(exit_at_raw) <= signal_at:
                realized_equity += float(trade.get("net_pnl") or 0)
            else:
                still_active.append(trade)
        active = still_active
        result = simulate_event(
            event,
            spec,
            equity=realized_equity,
            open_positions=len(active),
            aggregate_debit_at_risk=sum(
                float(trade.get("debit_at_risk") or 0) for trade in active
            ),
        )
        result["evaluation_partition"] = event.get("_validated_partition")
        trades.append(result)
        if result.get("filled_qty") and result.get("exit_at"):
            if _time(str(result["exit_at"])) > signal_at:
                active.append(result)
            else:
                realized_equity += float(result.get("net_pnl") or 0)

    invalid_trades = [
        trade
        for trade in trades
        if trade.get("status") in {"invalid", "open_unresolved"}
    ]
    if invalid_trades:
        metadata_errors.append(
            f"{len(invalid_trades)} event(s) lack valid point-in-time lifecycle evidence"
        )
    try:
        holdout_start = _time(metadata["untouched_holdout"]["start"])
        holdout_end = _time(metadata["untouched_holdout"]["end"])
        for trade in trades:
            if str(trade.get("evaluation_partition", "")).startswith("walk_forward:"):
                exit_at = trade.get("exit_at")
                if isinstance(exit_at, str) and _time(exit_at) >= holdout_start:
                    metadata_errors.append(
                        "walk-forward position carries into the untouched holdout"
                    )
            if trade.get("evaluation_partition") == "untouched_holdout":
                exit_at = trade.get("exit_at")
                if isinstance(exit_at, str) and _time(exit_at) >= holdout_end:
                    metadata_errors.append("holdout position exits after holdout end")
    except (KeyError, TypeError, ValueError):
        pass

    validation = spec["validation"]
    samples = int(validation["bootstrap_samples"])
    block_length = int(validation["bootstrap_block_length"])
    seed = int(validation["random_seed"])
    variants = int(metadata.get("declared_variants") or 1)
    primary_trades = [
        trade
        for trade in trades
        if trade.get("evaluation_partition") == validation["primary_partition"]
    ]
    primary_event_keys = sorted(
        f"{event.get('event_id') or ''}|{event.get('signal_at') or ''}"
        for event in events
        if event.get("_validated_partition") == validation["primary_partition"]
    )
    strategy_trade_metrics = trade_metrics(
        primary_trades,
        bootstrap_samples=samples,
        seed=seed,
        declared_variants=variants,
        block_length=block_length,
    )
    regime_metrics = metrics_by_regime(
        primary_trades,
        bootstrap_samples=samples,
        seed=seed,
        declared_variants=variants,
        block_length=block_length,
    )
    fold_metrics: dict[str, Any] = {}
    for fold in metadata.get("walk_forward_folds", []):
        if not isinstance(fold, dict) or not fold.get("fold_id"):
            continue
        partition = f"walk_forward:{fold['fold_id']}"
        fold_metrics[str(fold["fold_id"])] = trade_metrics(
            [
                trade
                for trade in trades
                if trade.get("evaluation_partition") == partition
            ],
            bootstrap_samples=samples,
            seed=seed + len(fold_metrics) + 100,
            declared_variants=variants,
            block_length=block_length,
        )

    calendar, calendar_errors = _valuation_calendar(dataset, spec, metadata)
    metadata_errors.extend(calendar_errors)
    primary_events = [
        event
        for event in events
        if event.get("_validated_partition") == validation["primary_partition"]
    ]
    strategy_curve, strategy_curve_errors = _derived_strategy_curve(
        dataset,
        primary_events,
        primary_trades,
        calendar,
        spec,
        initial_equity=initial_equity,
    )
    metadata_errors.extend(strategy_curve_errors)
    try:
        strategy_equity_metrics = equity_metrics(strategy_curve)
    except (KeyError, TypeError, ValueError) as exc:
        strategy_equity_metrics = equity_metrics([])
        metadata_errors.append(f"THESIS equity curve is invalid: {exc}")
    benchmark_curves, benchmark_errors = _derived_benchmark_curves(
        dataset,
        calendar,
        spec,
        initial_equity=initial_equity,
    )
    metadata_errors.extend(benchmark_errors)
    benchmark_metrics: dict[str, Any] = {}
    for benchmark in validation["required_benchmarks"]:
        curve = benchmark_curves.get(str(benchmark), [])
        try:
            benchmark_metrics[str(benchmark)] = equity_metrics(curve)
            benchmark_metrics[str(benchmark)]["derived_equity_curve"] = curve
        except (KeyError, TypeError, ValueError) as exc:
            metadata_errors.append(f"benchmark curve {benchmark} is invalid: {exc}")
            benchmark_metrics[str(benchmark)] = equity_metrics([])
    if strategy_curve and strategy_trade_metrics["total_net_pnl"] is not None:
        try:
            ending_equity = float(strategy_curve[-1]["equity"])
            tolerance = max(1.0, initial_equity * 0.0001)
            expected_equity = (
                initial_equity + float(strategy_trade_metrics["total_net_pnl"])
            )
            if abs(ending_equity - expected_equity) > tolerance:
                metadata_errors.append(
                    "THESIS equity curve does not reconcile to holdout net P&L"
                )
        except (KeyError, TypeError, ValueError):
            metadata_errors.append("THESIS equity reconciliation values are invalid")

    completed = int(strategy_trade_metrics["trade_count"])
    criteria: list[dict[str, Any]] = [
        _criterion(
            "runtime_specification_unchanged",
            not runtime_drift,
            "no strategy-code drift" if not runtime_drift else "; ".join(runtime_drift),
        ),
        _criterion(
            "data_integrity",
            not metadata_errors,
            "all raw-evidence and provenance checks passed"
            if not metadata_errors
            else "; ".join(sorted(set(metadata_errors))),
        ),
    ]
    minimum_trades = int(validation["minimum_filled_trades"])
    criteria.append(
        _criterion(
            "minimum_holdout_sample",
            completed >= minimum_trades,
            f"{completed} completed holdout trades; minimum {minimum_trades}",
        )
    )
    minimum_regime = int(validation["minimum_filled_trades_per_regime"])
    required_regimes = [str(value) for value in validation["required_regimes"]]
    regime_counts = {
        regime: int(regime_metrics.get(regime, {}).get("trade_count", 0))
        for regime in required_regimes
    }
    criteria.append(
        _criterion(
            "holdout_regime_coverage",
            all(count >= minimum_regime for count in regime_counts.values()),
            ", ".join(
                f"{regime}={count}" for regime, count in regime_counts.items()
            )
            + f"; minimum each {minimum_regime}",
        )
    )
    calendar_days = int(strategy_equity_metrics.get("calendar_days") or 0)
    gross_position_days = sum(
        float(trade.get("holding_days") or 0)
        for trade in primary_trades
        if trade.get("status") == "completed"
    )
    strategy_trade_metrics["gross_position_days"] = round(gross_position_days, 6)
    strategy_trade_metrics["average_open_positions"] = (
        round(gross_position_days / calendar_days, 8) if calendar_days else None
    )
    strategy_trade_metrics["turnover_fraction_initial_equity"] = (
        round(float(strategy_trade_metrics["total_turnover"]) / initial_equity, 8)
        if strategy_trade_metrics["total_turnover"] is not None
        else None
    )
    minimum_days = int(validation["minimum_calendar_days"])
    minimum_observations = int(validation["minimum_daily_equity_observations"])
    observations = int(strategy_equity_metrics.get("observations") or 0)
    criteria.append(
        _criterion(
            "holdout_time_span_and_daily_coverage",
            calendar_days >= minimum_days and observations >= minimum_observations,
            f"{calendar_days} calendar days and {observations} aligned observations; "
            f"minimums {minimum_days} and {minimum_observations}",
        )
    )
    confidence = strategy_trade_metrics["expectancy_95pct_confidence_interval"]
    lower_bound = confidence[0]
    minimum_bound = float(validation["minimum_expectancy_confidence_bound"])
    criteria.append(
        _criterion(
            "positive_holdout_expectancy_confidence_bound",
            lower_bound is not None and float(lower_bound) > minimum_bound,
            f"moving-block 95% lower bound={lower_bound}; required > {minimum_bound}",
        )
    )
    adjusted_p = strategy_trade_metrics["adjusted_p_value"]
    maximum_p = float(validation["maximum_adjusted_one_sided_p_value"])
    criteria.append(
        _criterion(
            "dependence_and_multiple_testing_adjusted_significance",
            adjusted_p is not None and float(adjusted_p) <= maximum_p,
            f"moving-block adjusted p={adjusted_p}; required <= {maximum_p}",
        )
    )
    strategy_sharpe = strategy_equity_metrics.get("sharpe")
    benchmark_sharpes = [
        float(metrics["sharpe"])
        for metrics in benchmark_metrics.values()
        if metrics.get("sharpe") is not None
    ]
    best_benchmark = max(benchmark_sharpes) if benchmark_sharpes else None
    advantage = float(validation["minimum_sharpe_advantage_over_best_benchmark"])
    sharpe_passed = (
        strategy_sharpe is not None
        and best_benchmark is not None
        and float(strategy_sharpe) > best_benchmark + advantage
    )
    criteria.append(
        _criterion(
            "holdout_risk_adjusted_benchmark_outperformance",
            sharpe_passed,
            f"THESIS Sharpe={strategy_sharpe}; best benchmark={best_benchmark}; "
            f"required advantage > {advantage}",
        )
    )
    spec_digest = strategy_spec_digest(spec)
    if (
        require_independent_replication
        and validation.get("independent_replication_required") is True
    ):
        criteria.append(
            _replication_criterion(
                metadata,
                independent_replication_report,
                spec_digest=spec_digest,
                primary_event_keys=primary_event_keys,
            )
        )
    edge_validated = all(criterion["passed"] for criterion in criteria)
    positive_status = (
        "validated_positive_edge"
        if require_independent_replication
        else "replication_criteria_passed"
    )
    return {
        "schema_version": "thesis-edge-study-report-v1",
        "strategy_id": spec["strategy_id"],
        "strategy_spec_sha256": spec_digest,
        "dataset_id": str(metadata.get("dataset_id") or ""),
        "dataset_artifact_sha256": dataset.get("_artifact_sha256"),
        "primary_partition": validation["primary_partition"],
        "status": positive_status if edge_validated else "not_validated",
        "claim": (
            (
                "All pre-registered holdout and independent-replication criteria passed."
                if require_independent_replication
                else "The independent dataset passed all non-replication criteria."
            )
            if edge_validated
            else "No scientifically validated positive trading edge is established."
        ),
        "criteria": criteria,
        "data_integrity_errors": sorted(set(metadata_errors)),
        "runtime_drift": runtime_drift,
        "verified_evidence": {
            "source_artifacts": _source_artifact_identities(metadata),
            "holdout_window": _holdout_window(metadata),
            "holdout_event_keys": primary_event_keys,
        },
        "strategy": {
            "trade_metrics": strategy_trade_metrics,
            "equity_metrics": strategy_equity_metrics,
            "derived_equity_curve": strategy_curve,
            "by_regime": regime_metrics,
            "walk_forward_test_folds": fold_metrics,
            "event_outcomes": {
                status: sum(trade.get("status") == status for trade in primary_trades)
                for status in sorted(
                    {str(trade.get("status") or "") for trade in primary_trades}
                )
            },
        },
        "benchmarks": benchmark_metrics,
        "trades": trades,
    }


def evaluate_study_pair(
    primary_dataset: dict[str, Any],
    replication_dataset: dict[str, Any],
    spec: dict[str, Any],
    *,
    primary_artifact_root: Path,
    replication_artifact_root: Path,
) -> dict[str, Any]:
    """Evaluate the replication independently, then use its computed report."""
    replication_report = evaluate_dataset(
        replication_dataset,
        spec,
        artifact_root=replication_artifact_root,
        require_independent_replication=False,
    )
    return evaluate_dataset(
        primary_dataset,
        spec,
        artifact_root=primary_artifact_root,
        independent_replication_report=replication_report,
        require_independent_replication=True,
    )