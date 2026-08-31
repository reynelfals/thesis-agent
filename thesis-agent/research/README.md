# Thesis edge-validation protocol

This directory contains a pre-registered, read-only protocol for answering one
question: **does the available evidence support a repeatable positive trading
edge?**

Current answer: **no scientifically validated edge is established**. The result
is not a claim that the strategy must lose money. It means the required evidence
does not exist, so the positive claim is rejected rather than estimated from an
optimistic substitute.

## Reproduce the current data audit

From the `thesis-agent` directory:

```bash
PYTHONPATH=. python scripts/run_edge_study.py
```

The command calls only Alpaca `GET` endpoints. It cannot place, replace, cancel,
or close an order. It writes:

- [`edge-study-report.json`](edge-study-report.json), the machine-readable result;
- [`edge-study-report.md`](edge-study-report.md), the human-readable result;
- [`strategy-spec-v1.json`](strategy-spec-v1.json), the frozen rules and rejection
  thresholds.

The probe verifies that Alpaca historical stock bars, expired option contracts,
historical option bars, and historical option trades are available. Alpaca does
not expose the required historical option quote/NBBO endpoint through the tested
historical-options interface. A last trade or one-minute OHLC bar is not a valid
substitute for the two contemporaneous bid/ask pairs needed to model a debit
vertical.

## What is frozen

`strategy-spec-v1.json` freezes:

- the 29-symbol universe and stock-factor weights;
- Monday 09:35 America/New_York prospective decision schedule;
- top-five stock probe and top-three option-feasible shortlist;
- recorded-only Grok-4.6 decision mapping and minimum conviction;
- 14–45 DTE, target DTE, strike width, quote, and debit rules;
- 2% per-position and 6% aggregate debit caps;
- conservative multi-leg entry fills, displayed size, slippage, and fees;
- a study-only profit, stop, time-exit, and expiration policy;
- minimum sample, regime, benchmark, confidence, and significance criteria.

The specification fingerprints the production strategy source files. A changed
fingerprint invalidates an already registered study instead of silently testing a
new strategy.

The exit policy is explicitly **prospective and study-only**. The deployed
hackathon agent monitors positions but does not implement executable closes.
Applying the new exit policy to older trades would test a different strategy and
is forbidden.

## Point-in-time requirements

A full dataset must use schema `thesis-edge-dataset-v1` and provide:

```json
{
  "_artifact_sha256": "canonical-dataset-sha256-computed-before-evaluation",
  "schema_version": "thesis-edge-dataset-v1",
  "metadata": {
    "dataset_id": "content-addressed-or-versioned-id",
    "initial_equity": 100000,
    "declared_variants": 1,
    "point_in_time": true,
    "completed_bars_only": true,
    "selection_log_complete": true,
    "corporate_actions_adjusted": true,
    "survivorship_bias_control": "point_in_time_membership",
    "nbbo_source": "OPRA",
    "grok_decisions": "contemporaneous_recorded",
    "walk_forward": true,
    "untouched_out_of_sample": true,
    "daily_mark_to_market": true,
    "holdout_started_flat": true,
    "source_manifest": {
      "provider": "OPRA",
      "immutable": true,
      "artifacts": [
        {
          "role": "raw_archive",
          "path": "raw/licensed-opra-archive",
          "sha256": "64-lowercase-hex-characters"
        },
        {
          "role": "query_manifest",
          "path": "raw/query-manifest.json",
          "sha256": "64-lowercase-hex-characters"
        }
      ]
    },
    "walk_forward_folds": [
      {
        "fold_id": "fold-1",
        "train_start": "2026-01-01T00:00:00Z",
        "train_end": "2026-06-30T23:59:59Z",
        "test_start": "2026-07-01T00:00:00Z",
        "test_end": "2026-09-30T23:59:59Z"
      }
    ],
    "untouched_holdout": {
      "sealed_at": "2026-09-30T23:59:59Z",
      "start": "2026-10-01T00:00:00Z",
      "end": "2026-12-31T23:59:59Z"
    }
  },
  "events": [],
  "valuation_calendar": [
    "2026-10-01T20:00:00Z"
  ],
  "benchmark_inputs": {
    "SPY_BUY_HOLD": [
      {
        "at": "2026-10-01T20:00:00Z",
        "price": 700.0,
        "source": "SIP"
      }
    ]
  }
}
```

Each event contains the signal timestamp, raw completed bars and timestamped SIP
spots for all 29 symbols, the complete candidate and abstention log, hashes for
all top-five option probes, the point-in-time contract chain, recorded Grok
decision ID/model/schema, submission timestamp, shortlist ranks, strikes,
expiration, contract-bound paired OPRA NBBO snapshots with displayed sizes, and a
timestamped SIP expiration underlying mark when required. The evaluator
recalculates the stock ranking and selected vertical from the raw event evidence.
It rejects quotes after the expiration market close.

Each event also references a decision artifact by relative `path` and `sha256`.
That artifact must use schema `thesis-grok-decision-record-v1` and contain:

```json
{
  "schema_version": "thesis-grok-decision-record-v1",
  "record_id": "same-as-event-decision-record-id",
  "request": {
    "recorded_at": "timestamp-between-signal-and-response",
    "model": "grok-4.6",
    "request_schema": "request_defined_risk_spread-v1",
    "selection_log_sha256": "canonical-selection-log-sha256"
  },
  "response": {
    "recorded_at": "same-as-event-decision-recorded-at",
    "underlying": "SPY",
    "direction": "bullish",
    "regime": "model-authored-context",
    "setup": "nonempty",
    "invalidation": "nonempty",
    "horizon": "nonempty",
    "expected_move_pct": 2.0,
    "iv_note": "nonempty",
    "conviction": 0.8
  }
}
```

An ID alone is never evidence. The evaluator reads and hashes the file, validates
every frozen response field, checks request/response chronology, binds the request
to the full candidate log, and checks the selected symbol, direction, and
conviction against the event.

`thesis.research.historical.completed_bar_snapshot` discards every bar whose
explicit completion time is at or after the decision cutoff.
`thesis.research.historical.select_vertical_as_of` calculates DTE from the
historical timestamp rather than today. These pure adapters prevent the live
helpers from leaking current time or current prices into a historical run.

Run a supplied dataset with:

```bash
PYTHONPATH=. python scripts/run_edge_study.py \
  --dataset /path/to/point-in-time-dataset.json
```

This computes metrics but cannot return a positive verdict by itself. A positive
verdict also requires a distinct replication dataset, which the CLI loads and
evaluates rather than trusting a metadata assertion:

```bash
PYTHONPATH=. python scripts/run_edge_study.py \
  --dataset /path/to/primary/dataset.json \
  --replication-dataset /path/to/replication/dataset.json
```

Each dataset is canonically hashed after parsing. Its raw archive and query
manifest paths are resolved inside that dataset’s directory and hashed from disk;
missing files, path traversal, or mismatched hashes fail the study.
The detached `_artifact_sha256` must already be present and match; the CLI never
rewrites it. To calculate the commitment before sealing a dataset:

```bash
PYTHONPATH=. python -c \
  'import json,sys; from thesis.research.study import canonical_dataset_digest; print(canonical_dataset_digest(json.load(open(sys.argv[1]))))' \
  /path/to/dataset.json
```

Primary and replication datasets must use different verified raw archives,
non-overlapping holdout windows, and disjoint event IDs/timestamps. Changing only
the dataset ID is not independent replication.

Daily performance is never accepted from an operator-supplied equity curve.
`valuation_calendar` defines one aligned observation per session. For each open
spread at each timestamp, the evaluator requires exactly one contract-bound OPRA
NBBO pair, applies conservative close slippage and fees, and combines that
liquidation value with simulator-derived entry/exit cash flows. It rejects any
supplied `equity_curves` field. The SPY benchmark is constructed from aligned,
positive SIP prices; the cash benchmark is constructed as a constant balance.

Raw licensed OPRA data is intentionally not committed to this public repository.

## Conservative execution model

The simulator never assumes a midpoint fill:

1. It builds the order limit from the final pre-submission two-sided quote.
2. It waits the frozen minimum submission latency.
3. It buys the long leg at ask plus slippage and sells the short leg at bid
   minus slippage.
4. It fills only if the paired debit remains within the limit.
5. It caps quantity by displayed size on both legs and cancels the remainder.
6. It closes at long bid minus slippage and short ask plus slippage.
7. It applies per-leg contract fees, closing regulatory fees, partial fills, and
   expiration/assignment costs.
8. It leaves any lifecycle with missing close or expiration evidence unresolved,
   which invalidates the dataset.

Submission must occur within 120 seconds of the Monday 09:35 signal, and no
entry may fill more than 300 seconds after that signal. Moving the order window
later to benefit from observed prices invalidates the event.

## Bias and leakage controls

The evaluator fails closed unless the dataset certifies and demonstrates:

- completed bars only and point-in-time contract membership;
- a full candidate/abstention log, not only winning selections;
- historical universe membership rather than today’s survivors;
- contemporaneously recorded Grok outputs; retrospective prompting is forbidden;
- corporate-action-adjusted stock history;
- pre-declared strategy variant count;
- non-overlapping walk-forward folds;
- a holdout sealed before its evaluation period;
- daily mark-to-market equity and P&L reconciliation;
- OPRA NBBO evidence for both option legs.

Bull/bear and high-/low-volatility labels are not supplied by the operator. The
evaluator derives them from SPY’s pre-decision SIP spot, completed-bar SMA20, and
20-session realized volatility, then rejects conflicting event labels.

These controls address lookahead, in-progress bars, survivorship, post-selection,
regime cherry-picking, and multiple testing. They cannot make paper fills evidence
of live execution quality.

## Required metrics and decision rule

The report includes trade count, expectancy, hit rate, CAGR, annualized
volatility, Sharpe, Sortino, maximum drawdown, turnover, average open positions,
circular moving-block bootstrap confidence intervals and a centered-null
one-sided moving-block test, regime results, and Bonferroni correction for the
declared variant count. Chronological block resampling preserves short-range
dependence and does not assume symmetric debit-spread outcomes.

The positive-edge claim is rejected unless **every** frozen criterion passes:

- at least 100 completed trades;
- at least 365 calendar days;
- at least 15 completed trades in each bull, bear, high-volatility, and
  low-volatility regime;
- positive lower bound of the 95% expectancy interval;
- adjusted one-sided p-value no greater than 0.05;
- Sharpe greater than both SPY buy-and-hold and the defined-risk vertical
  benchmark;
- a distinct, content-addressed independent replication using the pinned strategy
  specification;
- no source drift or point-in-time integrity error.

One Monday paper outcome cannot satisfy these criteria.