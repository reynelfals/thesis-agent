from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from thesis.research.spec import (
    strategy_spec_digest,
    verify_runtime_fingerprints,
)


def capability_report(
    probe: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    drift = verify_runtime_fingerprints(spec)
    blockers = list(probe.get("blocking_reasons") or [])
    if drift:
        blockers.append("Frozen runtime source fingerprints no longer match.")
    return {
        "schema_version": "thesis-edge-study-report-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "study_kind": "historical_data_capability_audit",
        "strategy_id": spec["strategy_id"],
        "strategy_spec_sha256": strategy_spec_digest(spec),
        "status": "not_validated",
        "claim": "No scientifically validated positive trading edge is established.",
        "decision": (
            "Reject the positive-edge claim. Required point-in-time evidence is "
            "unavailable and no return estimate was manufactured."
        ),
        "performance": {
            "trade_count": 0,
            "expectancy_dollars": None,
            "hit_rate": None,
            "cagr": None,
            "annualized_volatility": None,
            "sharpe": None,
            "sortino": None,
            "maximum_drawdown": None,
            "turnover": None,
            "exposure": None,
            "confidence_interval": None,
            "adjusted_p_value": None,
            "by_regime": {},
        },
        "benchmarks": {
            "SPY_BUY_HOLD": None,
            "ATM_25D_DEFINED_RISK_VERTICAL": None,
        },
        "runtime_drift": drift,
        "data_probe": probe,
        "blocking_reasons": blockers,
        "required_next_evidence": [
            "A licensed point-in-time OPRA NBBO dataset for both spread legs",
            "A prospective archive of every candidate and contemporaneous Grok decision",
            "At least 100 completed trades over at least 365 calendar days",
            "At least 15 completed trades in each pre-registered regime",
            "Daily mark-to-market strategy and benchmark equity curves",
            "Walk-forward and untouched out-of-sample results net of modeled costs",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    status = str(report.get("status") or "unknown")
    lines = [
        "# Thesis scientific edge study",
        "",
        f"**Status:** `{status}`",
        "",
        f"**Conclusion:** {report.get('claim', '')}",
        "",
    ]
    if report.get("decision"):
        lines.extend([f"**Decision:** {report['decision']}", ""])
    lines.extend(
        [
            "## Scientific interpretation",
            "",
            "This report tests whether the available evidence supports a positive "
            "risk-adjusted return claim. It does not treat software tests, safety "
            "gates, paper-account readiness, or one trade as evidence of alpha.",
            "",
        ]
    )
    probe = report.get("data_probe")
    if isinstance(probe, dict):
        lines.extend(["## Point-in-time data capability audit", ""])
        evidence = probe.get("evidence")
        if isinstance(evidence, dict):
            lines.extend(
                [
                    "| Evidence | HTTP | Rows | Available |",
                    "| --- | ---: | ---: | :---: |",
                ]
            )
            for name, item in evidence.items():
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"| {name.replace('_', ' ')} | {item.get('http_status')} | "
                    f"{item.get('rows')} | {'yes' if item.get('available') else 'no'} |"
                )
            lines.append("")
    blockers = report.get("blocking_reasons") or report.get(
        "data_integrity_errors"
    )
    if blockers:
        lines.extend(["## Why the edge claim is rejected", ""])
        lines.extend(f"- {blocker}" for blocker in blockers)
        lines.append("")
    performance = report.get("performance")
    if not isinstance(performance, dict):
        performance = (
            report.get("strategy", {}).get("trade_metrics", {})
            if isinstance(report.get("strategy"), dict)
            else {}
        )
    lines.extend(
        [
            "## Pre-registered performance fields",
            "",
            "| Metric | Result |",
            "| --- | --- |",
        ]
    )
    for key in (
        "trade_count",
        "expectancy_dollars",
        "hit_rate",
        "cagr",
        "annualized_volatility",
        "sharpe",
        "sortino",
        "maximum_drawdown",
        "turnover",
        "exposure",
        "adjusted_p_value",
    ):
        lines.append(f"| {key.replace('_', ' ')} | {performance.get(key)} |")
    lines.extend(
        [
            "",
            "Null results mean the required evidence does not exist; they are not zero returns.",
            "",
        ]
    )
    criteria = report.get("criteria")
    if isinstance(criteria, list):
        lines.extend(["## Pre-registered decision criteria", ""])
        for criterion in criteria:
            if not isinstance(criterion, dict):
                continue
            mark = "PASS" if criterion.get("passed") else "FAIL"
            lines.append(
                f"- **{mark} — {criterion.get('name')}:** {criterion.get('detail')}"
            )
        lines.append("")
    next_evidence = report.get("required_next_evidence")
    if next_evidence:
        lines.extend(["## Evidence required before retesting", ""])
        lines.extend(f"- {item}" for item in next_evidence)
        lines.append("")
    lines.extend(
        [
            "## Guardrail",
            "",
            "Thesis remains a paper-only, defined-risk experimental framework. "
            "This report is not investment advice and does not establish profitability.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(
    report: dict[str, Any],
    *,
    json_path: Path,
    markdown_path: Path,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")