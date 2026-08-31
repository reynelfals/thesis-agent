#!/usr/bin/env python3
"""Run the read-only, pre-registered Thesis scientific edge study."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thesis.research.alpaca_probe import probe_historical_capabilities
from thesis.research.report import capability_report, write_report
from thesis.research.spec import DEFAULT_SPEC_PATH, load_strategy_spec
from thesis.research.study import (
    evaluate_dataset,
    evaluate_study_pair,
)

ROOT = Path(__file__).resolve().parents[1]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate Thesis without submitting orders. With no dataset, run a "
            "GET-only Alpaca historical-data capability audit."
        )
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=DEFAULT_SPEC_PATH,
        help="Frozen strategy specification JSON.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        help="Point-in-time dataset conforming to thesis-edge-dataset-v1.",
    )
    parser.add_argument(
        "--replication-dataset",
        type=Path,
        help=(
            "Distinct point-in-time dataset evaluated independently before any "
            "positive verdict is possible."
        ),
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=ROOT / "research" / "edge-study-report.json",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=ROOT / "research" / "edge-study-report.md",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    spec = load_strategy_spec(args.spec)
    if args.dataset:
        dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
        if not isinstance(dataset, dict):
            raise ValueError("study dataset must be a JSON object")
        if args.replication_dataset:
            replication = json.loads(
                args.replication_dataset.read_text(encoding="utf-8")
            )
            if not isinstance(replication, dict):
                raise ValueError("replication dataset must be a JSON object")
            report = evaluate_study_pair(
                dataset,
                replication,
                spec,
                primary_artifact_root=args.dataset.parent,
                replication_artifact_root=args.replication_dataset.parent,
            )
        else:
            report = evaluate_dataset(
                dataset,
                spec,
                artifact_root=args.dataset.parent,
            )
    elif args.replication_dataset:
        raise ValueError("--replication-dataset requires --dataset")
    else:
        report = capability_report(
            probe_historical_capabilities(),
            spec,
        )
    write_report(
        report,
        json_path=args.json_output,
        markdown_path=args.markdown_output,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "claim": report["claim"],
                "json_report": str(args.json_output),
                "markdown_report": str(args.markdown_output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())