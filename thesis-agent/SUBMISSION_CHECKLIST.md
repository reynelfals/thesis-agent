# Final submission checklist

**Deadline:** September 4, 2026 at 11:00 AM EDT  
**Track:** Options Alpha Agents

## Required deliverables

- [x] Public GitHub repository: <https://github.com/reynelfals/thesis-agent>
- [x] Published application URL: <https://thesis-agent.replit.app>
- [x] Narrated MP4 at `submission_assets/thesis-demo.mp4`
- [x] Direct video URL:
  <https://raw.githubusercontent.com/reynelfals/thesis-agent/main/thesis-agent/submission_assets/thesis-demo.mp4>
- [x] Dedicated PDF presentation at `submission_assets/thesis-slides.pdf`
- [x] MIT license
- [x] One-page write-up from `SUBMISSION.md`
- [x] Verified single-page US Letter PDF at
  `submission_assets/thesis-one-page.pdf`
- [x] Brand-new Alpaca paper account dedicated to the hackathon
- [x] Competition account starting balance set to exactly $100,000
- [x] Options trading level 3
- [ ] Full Alpaca paper account ID entered in lablab.ai’s private submission field
- [ ] Trading API, options strategy, and official Alpaca MCP path demonstrated
- [ ] Current P&L and trading activity visible from the submitted account
- [ ] Up to five optional X/LinkedIn build-in-public links

## Before publishing

```bash
cd thesis-agent
PYTHONPATH=. pytest -q
PYTHONPATH=. python -m thesis.smoke
```

- [ ] Dashboard loads without a blank screen
- [ ] `/api/health` reports ready
- [ ] All displayed account identifiers remain masked
- [ ] Repository contains no `.env`, keys, raw broker responses, or screenshots
  containing credentials
- [ ] `THESIS_ALLOW_EXECUTE=0` for normal dashboard hosting

## Automatic Monday one-shot MCP order capture

The published VM's dedicated scheduler subprocess is armed for **9:35 AM EDT on
Monday, August 31, 2026**. It enables execution only inside that worker process;
the dashboard subprocess remains read-only. The development scheduler must stay
stopped so there is only one execution worker.

1. Before Monday, confirm the production log reports `state=waiting`,
   `outcome=armed`, and the correct target time.
2. Leave the dashboard workflow running normally with execution disabled. Do not
   set `THESIS_ALLOW_EXECUTE=1` globally.
3. At 9:35 AM EDT, the separate worker atomically claims the fixed run ID and
   starts one fresh cycle. A 15-minute grace window allows minor infrastructure
   delay; after 9:50 AM EDT an unclaimed run is permanently recorded as skipped.
4. The worker exits after a completed, failed, or skipped attempt. Any restart
   after the claim is a no-op, including after an ambiguous submission.
5. After the cycle, inspect the dashboard and worker result:
   - `completed` with `submitted`, `blocked`, `rejected`, or `no_trade`
   - `failed` when configuration or cycle execution failed after the claim
   - `skipped` when the authorized window expired
   - `already_claimed` when a duplicate worker start was safely ignored
6. If an order was submitted, wait for the dashboard refresh and capture:
   - the single `place_option_order` dispatch
   - returned MCP paper order ID
   - broker status and fills
   - linked position/monitoring state
   - updated P&L and equity curve

If no order qualifies, keep the refusal as evidence. Do not lower conviction,
change risk limits, fabricate quotes, substitute a hand-built order, or launch
another cycle until genuinely new market information exists.

Do not claim a real MCP fill until broker evidence has been captured. The harness
owns account, clock, and order-status MCP calls. Grok receives only shortlist-scoped,
read-only `get_stock_snapshot` and `get_option_chain`, then requests
`request_defined_risk_spread`; deterministic code rebuilds and validates the spread.
`place_option_order` is the only write path, with no CLI/SDK write fallback. Timeout,
malformed, missing-order-ID, and ambiguous outcomes are terminal and never retried.
New cycles must show `tool_path=mcp` and empty `cli_commands`; historical CLI rows
may remain readable.

## Final lablab.ai form

- **Project name:** Thesis
- **Tagline:** Propose. Prove. Execute.
- **Category:** Options Alpha Agents
- **Repository:** <https://github.com/reynelfals/thesis-agent>
- **App URL:** <https://thesis-agent.replit.app>
- **Video:** <https://raw.githubusercontent.com/reynelfals/thesis-agent/main/thesis-agent/submission_assets/thesis-demo.mp4>
- **Slides:** `submission_assets/thesis-slides.pdf`
- **Paper account ID:** `[ENTER IN PRIVATE FORM — DO NOT COMMIT]`
- **Write-up:** Paste `SUBMISSION.md`
- **Social links:** Add up to five approved posts from `SOCIAL_POSTS.md`

Official challenge page:
<https://lablab.ai/event/raise-your-hack>