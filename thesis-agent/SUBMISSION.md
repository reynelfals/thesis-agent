# Thesis — Propose. Prove. Execute.

**Track:** Options Alpha Agents  
**Demo:** <https://thesis-agent.replit.app>
**Public repository:** <https://github.com/reynelfals/thesis-agent>
**Narrated video:** [`submission_assets/thesis-demo.mp4`](submission_assets/thesis-demo.mp4)
**Direct video URL:** <https://raw.githubusercontent.com/reynelfals/thesis-agent/main/thesis-agent/submission_assets/thesis-demo.mp4>
**Slide presentation:** [`submission_assets/thesis-slides.pdf`](submission_assets/thesis-slides.pdf)
**Paper account ID:** Enter the full ID only in lablab.ai’s required private field.

**Printable version:** [`submission_assets/thesis-one-page.pdf`](submission_assets/thesis-one-page.pdf)

## Form-ready metadata

**Cover image:** [`submission_assets/submission-brief.jpg`](submission_assets/submission-brief.jpg)
(1280×720, 16:9)
**Project title:** Thesis
**Tagline:** Propose. Prove. Execute.
**Short description:** Thesis is an autonomous options agent where Grok proposes a
falsifiable thesis, deterministic code proves bounded risk, and Alpaca paper
infrastructure executes—or safely refuses.
**Long description:** Paste this document from “The problem” through “Why it is
different,” including the paper-trading disclaimer.
**Technology tags:** Grok 4.6, xAI API, official Alpaca MCP server, Alpaca Trading
API, Alpaca Market Data API, Python, FastAPI, SQLite, Replit
**Direct slide PDF:** <https://raw.githubusercontent.com/reynelfals/thesis-agent/main/thesis-agent/submission_assets/thesis-slides.pdf>
**Direct MP4:** <https://raw.githubusercontent.com/reynelfals/thesis-agent/main/thesis-agent/submission_assets/thesis-demo.mp4>

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
Alpaca. A deterministic scout ranks the selected universe, probes options only for
its top five, and advances at most three option-feasible candidates. Grok-4.6 sees
only filtered, read-only official Alpaca MCP `get_stock_snapshot` and
`get_option_chain` tools scoped to that shortlist.

Grok returns its thesis through `request_defined_risk_spread`: underlying,
direction, regime, setup, invalidation, horizon, expected move, an IV note, and
conviction. This requests analysis, not execution. Grok never chooses final
contracts, sizes risk, reads the account or clock, checks order status, or submits
orders; the application harness owns those system calls.

If conviction is high enough, deterministic code independently rebuilds a call or
put debit vertical, refreshes quotes, and revalidates liquidity, 14–45 DTE,
contract identity, sizing, and risk. It selects an expiration near 25 DTE, an
approximately at-the-money long leg, and a defined-width short leg. The order is a
day-limit MLEG with a 1:1 ratio. Maximum loss is known before submission: net debit
× 100 × quantity.

The cycle then applies hard gates: paper endpoint only; official Alpaca MCP ready;
paper account active and unblocked; mandatory options level 3; MCP clock valid and
in agreement with the SDK read; allowlisted underlying; conviction at least 0.35;
valid liquidity, quotes, debit, and 14–45 DTE; no more than three open theses; at
most 2% of equity at risk per thesis and 6% in aggregate; market open; and explicit
execution enablement. Any failed gate produces a recorded no-trade or blocked
decision.

## Alpaca implementation

Alpaca’s Trading and Market Data APIs provide account state, positions, option
contracts and quotes, stock trades and bars, order status, portfolio history, and
fill activities. The official Alpaca MCP server is the agent tool boundary. The
application harness owns MCP account, clock, and order-status calls; SDK reads may
support deterministic scouting, monitoring, and performance.

`place_option_order` is the only write path. There is no CLI or SDK write fallback.
A timeout, malformed response, missing order ID, rejection, or otherwise ambiguous
submission is terminal and never retried. Paper-only operation and options level 3
are mandatory.

## Evidence and performance

The hosted FastAPI dashboard is a read-only audit console. It renders before broker
data arrives, then reconciles the append-only SQLite decision ledger with Alpaca
paper data in the background. Judges can expand any cycle and inspect the Grok
thesis, deterministic gates, sanitized MCP/API trace, exact blocked MLEG intent,
broker order state, fills, position attribution, exit state, realized and unrealized
P&L, reconciliation delta, and equity curve.

Historical CLI audit rows remain readable. New cycles record `tool_path=mcp` with
empty `cli_commands`. No real MCP order fill is claimed until one is captured and
reconciled from the submitted paper account.

Performance values come from the submitted paper account and are displayed directly
from Alpaca; they are not hardcoded or backfilled. A no-trade is treated as a valid,
auditable outcome rather than forcing activity for a demo.

This operational evidence is not a claim of alpha. The
[pre-registered scientific study](research/edge-study-report.md) currently rejects
a positive-edge claim: historical option bars and trades are available, but the
required contemporaneous OPRA NBBO pairs and historical recorded Grok decisions are
not. No synthetic fill series is substituted, and one paper result cannot establish
repeatability.

## Why it is different

Thesis optimizes for **evidence over assertion**. The model is creative where
language is useful and powerless where deterministic controls are safer. The result
is an agent whose reasoning, abstention, risk, tool use, and broker outcome can be
judged from one screen.

Paper trading only. This project is a hackathon demonstration, not investment advice.