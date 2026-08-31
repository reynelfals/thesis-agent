# Thesis

An autonomous options agent that will not take a trade until it can write **why**, **when it is wrong**, and the **max dollar loss**.

Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) (Options Alpha Agents). Paper trading only.

**Stack:** Official Alpaca MCP server + Alpaca APIs (`alpaca-py` for deterministic
reads) + Grok + FastAPI.
**Current demo structure:** one long call (bullish) or long put (bearish), with
14–45 DTE and premium paid as max loss. No 0DTE or short option legs.

**Scientific status:** no repeatable positive trading edge has been validated.
The [pre-registered research protocol](research/README.md) rejects the claim
because the available Alpaca archive has bars and trades but not the historical
OPRA NBBO pairs required for defensible multi-leg fills, and Thesis has no
historical archive of contemporaneously recorded Grok decisions. See the
[current evidence report](research/edge-study-report.md) and
[frozen strategy specification](research/strategy-spec-v1.json). That frozen v1
vertical study is intentionally separate from the
[current paper-demo v2 strategy](research/current-demo-strategy-v2.json).

## Setup

```bash
# Python 3.11+
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # separate development and judge paper keys only
python -m thesis.smoke
```

`THESIS_ACCOUNT_PROFILE` is mandatory: `development` uses only the `DEV_APCA_*`
keys, while `judge` uses only the `APCA_*` keys. There is no credential fallback.
`APCA_API_BASE_URL` must point at `https://paper-api.alpaca.markets`; the process
refuses to start otherwise. Keep development and judge data in
`data/development-thesis.sqlite` and `data/judge-thesis.sqlite`, respectively.

## Agent cycle (not the public dashboard)

Dashboard is **read-only**. The cycle runs in Shell:

```bash
PYTHONPATH=. python scripts/run_cycle.py
```

Grok-4.6 chooses a stock-ranked underlying and bullish/bearish direction. Code
selects one liquid long call/put and applies deterministic risk gates, including a
1% equity premium cap. **No order** unless `THESIS_ALLOW_EXECUTE=1`, the US market
is open, conviction is valid and ≥ 0.35, and all execution evidence is available.

Stock ranking is deterministic; Grok is still called when the legacy debit-vertical
probe finds no feasible spread. Final single-option construction requires 20-day
average stock dollar volume of at least `THESIS_MIN_AVG_DOLLAR_VOLUME` (default
$50M), plus a fresh two-sided option quote no wider than
`THESIS_MAX_OPTION_BID_ASK_PCT` (default 25% of midpoint).

### Scout universe rollout

The default `THESIS_SCOUT_UNIVERSE=expanded` profile covers 29 symbols. The
`baseline` profile remains available as the original ten-symbol reference: SPY,
QQQ, IWM, AAPL, MSFT, NVDA, AMZN, META, GOOGL, and TSLA. Expanded scouting adds a
liquid stock and sector ETF across all eleven GICS sectors:

| Coverage | Approved symbols |
|---|---|
| Broad market | SPY, QQQ, IWM |
| Communication Services | META, GOOGL, XLC |
| Consumer Discretionary | AMZN, TSLA, XLY |
| Consumer Staples | COST, XLP |
| Energy | XOM, XLE |
| Financials | JPM, XLF |
| Health Care | LLY, XLV |
| Industrials | CAT, XLI |
| Information Technology | AAPL, MSFT, NVDA, XLK |
| Materials | FCX, XLB |
| Real Estate | PLD, XLRE |
| Utilities | NEE, XLU |

Promotion requires three alternating baseline/expanded paper-cycle pairs collected
within 15 minutes per pair. Every expanded comparison must be ready. Median expanded
full-scout latency must be at most 2.5× its paired baseline and no expanded scout may
exceed 15 seconds. Every expanded cycle must observe at least 95% of its symbols,
with median reliability regression no worse than 2 percentage points versus the
same-cycle baseline subset. Candidate quality must improve by at least 0.02 median
top-five average stock score, and at least two of three cycles must add a new symbol
to the top five.

On August 29, 2026, three execution-disabled paper pairs were run sequentially in
the same closed-market data window. All three comparisons were ready and referenced
the immediately preceding baseline:

| Evidence | Pair 1 | Pair 2 | Pair 3 | Threshold result |
|---|---:|---:|---:|---|
| Baseline full scout | 3.080s | 2.988s | 2.893s | reference |
| Expanded full scout | 5.324s | 5.328s | 5.282s | pass: 1.78× median; 5.328s max |
| Expanded observations | 100% | 100% | 100% | pass: 100% min; 0-point median regression |
| Top-five average score delta | +0.1449 | +0.1449 | +0.1449 | pass: +0.1449 median |
| Baseline/expanded overlap | 2/5 | 2/5 | 2/5 | pass: new symbols in 3/3 pairs |

The evidence passed every threshold, so `expanded` is now the default; set
`THESIS_SCOUT_UNIVERSE=baseline` to run the retained reference profile. Expanded
traces continue to compare actual full-scout duration with the latest stored
baseline cycle and record same-cycle observation timing, success rates, fixed stage
targets, and top-five score delta/overlap. If no measured baseline is available, the
trace marks the comparison not ready instead of estimating one. Only stock data is
used to rank the full universe; legacy option-chain probes remain limited to the top
five, and Grok receives at most three stock-ranked candidates even when no legacy
vertical is feasible.

### Alpaca MCP trust boundary

Grok sees only the filtered, read-only official Alpaca MCP tools
`get_stock_snapshot` and `get_option_chain`, restricted to the deterministic
shortlist (at most three stock-ranked candidates). The application harness—not
Grok—owns account, clock, and order-status calls. Grok finishes research by calling
the local `request_single_long_option` directional request tool; that is not an
order. Older audit records may show the compatibility name
`request_defined_risk_spread`; for current demo v2 it maps only to the same
directional thesis request and does not authorize a vertical or broker write.

Deterministic code then selects one long call or put, refreshes its quote, and
validates liquidity, 14–45 DTE, the 1% equity premium cap, and maximum loss equal to
premium paid. `place_option_order` is the only write path and submits a simple
single-leg limit/day buy-to-open order. There is no CLI or SDK write fallback. A
timeout, malformed response, missing order ID, or otherwise ambiguous submission
is terminal and is never retried, preventing unattributed or duplicate orders.

Paper trading and options level 3 are mandatory. SDK reads may still support
deterministic scouting, monitoring, and performance reconciliation. Historical CLI
audit rows remain readable, but every new cycle records `tool_path=mcp` and an empty
`cli_commands` list.

## Judge audit dashboard

The custom FastAPI dashboard is a read-only audit surface. Its page shell renders
immediately while a background worker reconciles the local thesis ledger with live
Alpaca paper data. Explicit loading, stale, and error states replace a blocking blank
screen. The dashboard shows:

- a chronological decision timeline: thesis, invalidation, every deterministic risk
  gate, sanitized MCP/API trace, order state, fills, and the reason a trade was skipped;
- thesis-linked position monitoring with current option contracts, unrealized P&L, and an
  explicit exit state (`monitoring`, `not_started`, `not_applicable`, or `flat_unlinked`);
- realized P&L calculated FIFO from Alpaca `FILL` activities, live unrealized P&L,
  current equity versus the Alpaca history baseline, a reconciliation delta, and
  the broker equity curve;
- append-only SQLite cycle, order-status, and performance snapshots. `last_cycle.json`
  remains only a convenience view of the newest cycle.

The dashboard never submits, replaces, cancels, or closes an order. It exposes only
`GET /api/dashboard` and `GET /api/health`; all returned evidence is allow-listed and
sanitized.

The production process also supervises a durable **analysis-only** worker. On
weekdays it considers slots at 09:35, 10:05, and every 30 minutes through 15:35
America/New_York. Each slot is atomically claimed in SQLite and always calls the
cycle with `execute=False`; the worker refuses execution-enabled configuration.
An authoritative paper-SDK clock read skips closed-market and holiday slots
before analysis. Each analysis runs in an isolated process with a 14-minute
deadline, so a stalled cycle is recorded as a terminal timeout without blocking
later slots. It does not backfill missed slots. A **republish is required** for
this worker change to take effect, and execution remains disabled after
republishing.

Run it locally from this directory:

```bash
THESIS_ACCOUNT_PROFILE=development THESIS_DB=data/development-thesis.sqlite \
  PYTHONPATH=. uvicorn thesis.web.app:app --host 0.0.0.0 --port 5000
```

## Submission package

- [One-page write-up](SUBMISSION.md)
- [Verified one-page PDF](submission_assets/thesis-one-page.pdf)
- [Presentation deck PDF](submission_assets/thesis-slides.pdf)
- [Narrated demo video](submission_assets/thesis-demo.mp4)
- [Demo script](DEMO_SCRIPT.md)
- [Final submission and Monday execution checklist](SUBMISSION_CHECKLIST.md)
- [Optional build-in-public posts](SOCIAL_POSTS.md)
- [Scientific edge study](research/edge-study-report.md)

**Live audit console:** <https://thesis-agent.replit.app>

The hosted app also serves the one-page judge brief at `/brief`.
Printing `/brief` produces the same verified single-page US Letter PDF layout.

## Dedicated $100,000 final-demo account

Use a brand-new Alpaca **paper** account dedicated to the hackathon:

1. Create or reset the paper account to exactly `$100,000`.
2. Confirm options approval/trading level 3.
3. Put only that account's paper keys in Replit Secrets. Never paste them into code,
   chat, git, screenshots, or the write-up.
4. Keep `APCA_API_BASE_URL=https://paper-api.alpaca.markets` and
   `THESIS_DEMO_STARTING_EQUITY=100000`.
5. Load the dashboard before trading. All readiness rows must pass and the account
   phase should say `Fresh — no fills recorded`.
6. Run the autonomous cycle from Shell. The dashboard refreshes automatically to
   capture the decision, order/fills, position state, P&L, and equity curve.
7. Enter the full Alpaca paper account ID only in the private lablab.ai submission
   field. Keep the repository and public screenshots masked.

After the first fill the phase changes to `In use`; that is expected. Do not reset the
account until the final screenshots and write-up have been captured, or the broker
history will no longer match the audit story.

## Replit judge URL

1. Push this repository to a public GitHub repository.
2. Import it at [replit.com/import](https://replit.com/import) using **GitHub**.
3. Add these values in **Replit Secrets**, never in Agent chat, source files,
   screenshots, or git:

   | Name | Value |
   |---|---|
   | `THESIS_ACCOUNT_PROFILE` | `judge` |
   | `APCA_API_KEY_ID` | paper key |
   | `APCA_API_SECRET_KEY` | paper secret |
   | `APCA_API_BASE_URL` | `https://paper-api.alpaca.markets` |
   | `XAI_API_KEY` | Grok thesis generation |

   Production launchers also pin `THESIS_DB=data/judge-thesis.sqlite`; development
   uses the separate `DEV_APCA_*` credentials and development database.

4. Run. Replit launches the FastAPI dashboard on port 5000. It is **read-only**
   (decision audit + orders + fills + positions + P&L) and does not place orders.
5. Publish the app and place its public URL in the lablab.ai submission.
