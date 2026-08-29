# Thesis demo script — 2 minutes 30 seconds

**Rendered video:** [`submission_assets/thesis-demo.mp4`](submission_assets/thesis-demo.mp4)  
**Slide presentation:** [`submission_assets/thesis-slides.pdf`](submission_assets/thesis-slides.pdf)  
**Public demo:** <https://thesis-agent.replit.app>

## Recording checklist

- Use the published app, not localhost.
- Confirm the dashboard says `READY RECORD` and `READ ONLY`.
- Keep the full paper account ID, keys, terminal environment, and raw broker
  responses out of frame.
- Use the fresh $100,000 competition paper account.
- The packaged video truthfully shows pre-cycle readiness. After Monday, replace
  only the result scene if broker evidence exists. A blocked/no-trade record is
  still truthful evidence; never force a setup.

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
> conviction. Grok sees only filtered, read-only `get_stock_snapshot` and
> `get_option_chain` tools scoped to the deterministic shortlist. The application
> harness owns account, clock, and order-status calls.

### 0:40–1:05 — Deterministic options and risk

**Screen:** Show the spread and gate evidence.

> Grok calls `request_defined_risk_spread`, which is only a request. Code rebuilds
> the contracts, refreshes quotes, and validates liquidity, DTE, sizing, and risk.
> Before submission it also enforces the allowlist, minimum conviction, two-percent
> per-trade risk, six-percent aggregate risk, position limits, paper-only endpoint,
> mandatory options level 3, market hours, and explicit execution enablement.

### 1:05–1:35 — Official Alpaca MCP proof

**Screen:** Tool trace and order-intent evidence.

> The official Alpaca MCP server is the agent tool boundary.
> `place_option_order` is the only write path, with no CLI or SDK write fallback.
> A timeout, malformed response, missing order ID, or ambiguous submission is
> terminal and never retried.

**Only if a real MCP order has been submitted:** Point to the MCP order ID and
broker status; show a fill only if broker evidence exists.

**If blocked/no trade:**

> This cycle correctly refused to trade. The exact two-leg payload is still visible,
> along with the failed gate and the reason no broker order was submitted.

### 1:35–2:05 — Monitoring and performance

**Screen:** Position monitoring, fill ledger, P&L, and equity curve.

> The dashboard reconciles the local append-only decision ledger with Alpaca orders,
> fills, positions, and portfolio history. It separates realized from unrealized
> P&L, shows reconciliation, and never invents an exit before the broker evidence
> exists. SDK reads may support this monitoring and performance view. Historical
> CLI rows remain readable; new cycles record `tool_path=mcp` and no CLI commands.

### 2:05–2:30 — Close

**Screen:** Return to the hero, keeping evidence visible below.

> Thesis is an autonomous options agent built around evidence over assertion:
> Grok proposes, deterministic controls prove, and Alpaca executes. Every trade—and
> every refusal to trade—is explainable from one read-only screen.