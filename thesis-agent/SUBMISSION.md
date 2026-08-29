# Thesis — Propose. Prove. Execute.

**Track:** Options Alpha Agents  
**Demo:** `[ADD PUBLISHED REPLIT URL]`  
**Public repository:** `[ADD GITHUB URL]`  
**Paper account ID:** Enter the full ID only in lablab.ai’s required private field.

**Printable version:** [`submission_assets/thesis-one-page.pdf`](submission_assets/thesis-one-page.pdf)

## One-line pitch

Thesis is an autonomous options agent that cannot risk a dollar until it writes
why the trade exists, when it is wrong, and the maximum amount it can lose.

## The problem

Most “AI traders” collapse analysis, risk, and execution into one opaque prompt.
That makes a confident hallucination indistinguishable from a valid trade and
makes a demo hard to audit. Thesis separates those responsibilities. Grok proposes
a falsifiable idea; deterministic code decides whether the idea is tradable; Alpaca
paper infrastructure proves what actually happened.

## How the agent works

Each cycle reads live paper-account state, positions, fills, and market data from
Alpaca. Grok-4.6 receives a compact market snapshot and returns structured JSON:
underlying, direction, regime, setup, invalidation, horizon, expected move, an IV
note, and conviction. Grok never chooses contracts, sizes risk, or submits orders.

If conviction is high enough, deterministic code constructs a call or put debit
vertical. It selects a 14–45 DTE expiration near 25 DTE, an approximately at-the-money
long leg, and a defined-width short leg. The order is a day-limit MLEG with a 1:1
ratio. Maximum loss is known before submission: net debit × 100 × quantity.

The cycle then applies hard gates: paper endpoint only; Alpaca CLI v0.0.13 present;
CLI paper account active, unblocked, and options level 3; CLI clock valid and in
agreement with the SDK; allowlisted underlying; conviction at least 0.35; valid
quotes and debit; 14–45 DTE; no more than three open theses; at most 2% of equity
at risk per thesis and 6% in aggregate; market open; and explicit execution
enablement. Any failed gate produces a recorded no-trade or blocked decision.

## Alpaca implementation

Alpaca’s Trading and Market Data APIs provide account state, positions, option
contracts and quotes, stock trades and bars, order status, portfolio history, and
fill activities. Alpaca CLI is part of the actual agent path—not a screenshot-only
demo. Every cycle runs `alpaca account get` and `alpaca clock`. An eligible spread
is submitted only through `alpaca api POST /v2/orders`.

The CLI boundary fails closed. Invalid CLI JSON, a blocked account, insufficient
options level, clock disagreement, timeout, rejection, or a response without an
order ID stops the cycle. It never retries through the SDK, avoiding both false
tool attribution and duplicate orders after an ambiguous response.

## Evidence and performance

The hosted FastAPI dashboard is a read-only audit console. It renders before broker
data arrives, then reconciles the append-only SQLite decision ledger with Alpaca
paper data in the background. Judges can expand any cycle and inspect the Grok
thesis, deterministic gates, sanitized CLI/API trace, exact blocked MLEG intent,
broker order state, fills, position attribution, exit state, realized and unrealized
P&L, reconciliation delta, and equity curve.

Performance values come from the submitted paper account and are displayed directly
from Alpaca; they are not hardcoded or backfilled. A no-trade is treated as a valid,
auditable outcome rather than forcing activity for a demo.

## Why it is different

Thesis optimizes for **evidence over assertion**. The model is creative where
language is useful and powerless where deterministic controls are safer. The result
is an agent whose reasoning, abstention, risk, tool use, and broker outcome can be
judged from one screen.

Paper trading only. This project is a hackathon demonstration, not investment advice.