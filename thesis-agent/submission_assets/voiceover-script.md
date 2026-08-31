# Thesis demo voiceover

The rendered submission video uses eight narrated scenes.

1. **Hook** — Most AI traders ask a model what to buy and hope the answer is
   safe. Thesis does the opposite. Grok may propose, but it cannot execute until
   deterministic code proves the trade is bounded, attributable, and allowed.
2. **Architecture** — Thesis separates the autonomous loop into three trust
   zones. Grok writes a falsifiable market thesis. Deterministic Python owns
   contract selection, risk, sizing, and every gate. Alpaca paper infrastructure
   owns the market truth, order state, fills, positions, and performance evidence.
3. **Scout** — The scout begins with 29 liquid stocks and sector ETFs.
   Deterministic ranking narrows that universe to five names before any option
   chain work. Liquidity checks then reduce the research set to no more than three
   candidates, so Grok receives focused evidence instead of an unrestricted
   market search.
4. **Risk** — Grok returns direction, setup, invalidation, horizon, expected
   move, volatility context, and conviction. That is only a request. Code
   independently rebuilds a defined-risk debit vertical and enforces paper-only
   access, options level 3, 14–45 DTE, quote quality, market hours, position
   limits, and strict 2% per-thesis and 6% aggregate risk caps.
5. **MCP proof** — The official Alpaca MCP server is the agent tool boundary.
   `place_option_order` is the only write path. There is no trading SDK fallback.
   A timeout, malformed result, missing order ID, or ambiguous response is
   terminal and is never retried. Duplicate prevention is part of the design, not
   a promise in the prompt.
6. **Audit console** — The public audit console is read-only. It reconciles the
   append-only decision ledger with Alpaca orders, fills, positions, and portfolio
   history. Judges can inspect the thesis, every gate, sanitized MCP trace, exact
   multi-leg intent, broker status, realized and unrealized P&L, reconciliation,
   and the equity curve from one screen.
7. **Paper-market result** — The Monday autonomous cycle reached the fresh
   $100,000 paper account and official Alpaca MCP with options level 3. The
   deterministic scout ranked the full universe and probed five option chains,
   but no option-feasible candidate passed. Thesis recorded a no-trade instead of
   forcing a spread. No order was submitted, there are zero fills and zero P&L,
   and the public dashboard remains read-only. A safe refusal is evidence.
8. **Close** — Thesis is built around evidence over assertion. Grok proposes.
   Deterministic controls prove. Alpaca executes in paper. Every trade—and every
   refusal to trade—is explainable from the public audit console. Thesis is a
   hackathon demonstration and not investment advice.