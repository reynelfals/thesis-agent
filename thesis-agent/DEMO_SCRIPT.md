# Thesis demo script — 2 minutes 30 seconds

## Recording checklist

- Use the published app, not localhost.
- Confirm the dashboard says `READY RECORD` and `READ ONLY`.
- Keep the full paper account ID, keys, terminal environment, and raw broker
  responses out of frame.
- Use the fresh $100,000 competition paper account.
- Prefer the Monday market-open record if it qualifies. A blocked/no-trade record
  is still truthful evidence; never force a setup.

## Shot list and narration

### 0:00–0:15 — Hook

**Screen:** Dashboard hero and readiness ledger.

> Most AI traders ask a model what to buy and hope the answer is safe. Thesis does
> the opposite: the model may propose, but it cannot execute until deterministic
> code proves the trade is bounded, attributable, and allowed.

### 0:15–0:40 — The autonomous loop

**Screen:** Expand a decision record and show the Grok thesis.

> Each autonomous cycle reads live Alpaca paper data and asks Grok-4.6 for a
> structured thesis: direction, setup, invalidation, horizon, expected move, and
> conviction. Grok cannot choose contracts, size risk, or place an order.

### 0:40–1:05 — Deterministic options and risk

**Screen:** Show the spread and gate evidence.

> Code constructs a defined-risk debit vertical between 14 and 45 days to expiry.
> Before submission it enforces the allowlist, minimum conviction, quote quality,
> two-percent per-trade risk, six-percent aggregate risk, position limits, paper
> endpoint, options approval, market hours, and explicit execution enablement.

### 1:05–1:35 — Real Alpaca CLI proof

**Screen:** Tool trace and order-intent evidence.

> Alpaca CLI is in the actual trading path. Every cycle proves the paper account
> and clock through the CLI. Eligible orders use `alpaca api POST /v2/orders`.
> There is no SDK order fallback. If the CLI fails or returns an ambiguous response,
> Thesis stops instead of risking a duplicate order.

**If submitted:** Point to the CLI order ID and broker status.

**If blocked/no trade:**

> This cycle correctly refused to trade. The exact two-leg payload is still visible,
> along with the failed gate and the reason no broker order was submitted.

### 1:35–2:05 — Monitoring and performance

**Screen:** Position monitoring, fill ledger, P&L, and equity curve.

> The dashboard reconciles the local append-only decision ledger with Alpaca orders,
> fills, positions, and portfolio history. It separates realized from unrealized
> P&L, shows reconciliation, and never invents an exit before the broker evidence
> exists.

### 2:05–2:30 — Close

**Screen:** Return to the hero, keeping evidence visible below.

> Thesis is an autonomous options agent built around evidence over assertion:
> Grok proposes, deterministic controls prove, and Alpaca executes. Every trade—and
> every refusal to trade—is explainable from one read-only screen.