# Thesis

An autonomous options agent that will not take a trade until it can write **why**, **when it is wrong**, and the **max dollar loss**.

Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) (Options Alpha Agents). Paper trading only.

**Stack:** Alpaca Trading API (`alpaca-py`) + Alpaca CLI + Grok + FastAPI.
**Structure:** defined-risk debit verticals (Level 3 multi-leg). No 0DTE, no naked short.

## Setup

```bash
# Python 3.11+
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # paper keys only
python -m thesis.smoke
```

`.env` must point at `https://paper-api.alpaca.markets`. The process refuses to start otherwise.

## Agent cycle (not the public dashboard)

Dashboard is **read-only**. The cycle runs in Shell:

```bash
PYTHONPATH=. python scripts/run_cycle.py
```

Grok-4.6 writes the thesis. Code picks the debit vertical and applies risk gates. **No order** unless `THESIS_ALLOW_EXECUTE=1` **and** the US market is open **and** conviction ≥ 0.35.

Every cycle invokes Alpaca **CLI** (`alpaca account get`, `alpaca clock`) and records
sanitized evidence. Eligible options orders go only through
`alpaca api POST /v2/orders`. There is no SDK order fallback: if CLI readiness or
submission fails, the cycle stops without retrying so it cannot create an
unattributed or duplicate order.

```bash
# pinned, repeatable CLI install
bash scripts/install_alpaca_cli.sh
export PATH="/home/runner/go/bin:$PATH"
alpaca version
```

## Judge audit dashboard

The custom FastAPI dashboard is a read-only audit surface. Its page shell renders
immediately while a background worker reconciles the local thesis ledger with live
Alpaca paper data. Explicit loading, stale, and error states replace a blocking blank
screen. The dashboard shows:

- a chronological decision timeline: thesis, invalidation, every deterministic risk
  gate, sanitized CLI/API trace, order state, fills, and the reason a trade was skipped;
- thesis-linked position monitoring with current spread legs, unrealized P&L, and an
  explicit exit state (`monitoring`, `not_started`, `not_applicable`, or `flat_unlinked`);
- realized P&L calculated FIFO from Alpaca `FILL` activities, live unrealized P&L,
  current equity versus the Alpaca history baseline, a reconciliation delta, and
  the broker equity curve;
- append-only SQLite cycle, order-status, and performance snapshots. `last_cycle.json`
  remains only a convenience view of the newest cycle.

The dashboard never submits, replaces, cancels, or closes an order. It exposes only
`GET /api/dashboard` and `GET /api/health`; all returned evidence is allow-listed and
sanitized.

Run it locally from this directory:

```bash
PYTHONPATH=. uvicorn thesis.web.app:app --host 0.0.0.0 --port 5000
```

## Submission package

- [One-page write-up](SUBMISSION.md)
- [Verified one-page PDF](submission_assets/thesis-one-page.pdf)
- [2½-minute demo script](DEMO_SCRIPT.md)
- [Final submission and Monday execution checklist](SUBMISSION_CHECKLIST.md)
- [Optional build-in-public posts](SOCIAL_POSTS.md)

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
   | `APCA_API_KEY_ID` | paper key |
   | `APCA_API_SECRET_KEY` | paper secret |
   | `APCA_API_BASE_URL` | `https://paper-api.alpaca.markets` |
   | `XAI_API_KEY` | Grok thesis generation |

4. Run. Replit launches the FastAPI dashboard on port 5000. It is **read-only**
   (decision audit + orders + fills + positions + P&L) and does not place orders.
5. Publish the app and place its public URL in the lablab.ai submission.
