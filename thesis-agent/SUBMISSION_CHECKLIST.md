# Final submission checklist

**Deadline:** September 4, 2026 at 11:00 AM EDT  
**Track:** Options Alpha Agents

## Required deliverables

- [x] Public GitHub repository: <https://github.com/reynelfals/thesis-agent>
- [x] Published application URL: <https://thesis-agent.replit.app>
- [x] 16:9 cover image at `submission_assets/submission-brief.jpg`
- [x] 3:30 narrated MP4 at `submission_assets/thesis-demo.mp4`
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
- [x] Trading API, defined-risk options strategy, and official Alpaca MCP path
  demonstrated
- [x] Current P&L and autonomous no-trade activity visible from the submitted
  account
- [ ] Up to five optional X/LinkedIn build-in-public links

## Before publishing

```bash
cd thesis-agent
PYTHONPATH=. pytest -q
PYTHONPATH=. python -m thesis.smoke
```

- [x] Dashboard loads without a blank screen
- [x] `/api/health` reports ready
- [x] All displayed account identifiers remain masked
- [x] Repository contains no `.env`, keys, raw broker responses, or screenshots
  containing credentials
- [x] `THESIS_ALLOW_EXECUTE=0` for normal dashboard hosting

## Monday one-shot MCP result

On **Monday, August 31, 2026**, the published worker completed one autonomous
paper-market cycle. The public audit console records a **NO_TRADE** result after
paper endpoint, account, options level, official Alpaca MCP discovery, MCP account
and clock reads, market-open, and execution-authorization checks. Deterministic
scouting ranked the universe and probed five option chains, but no
option-feasible candidate passed and conviction remained below the hard minimum.

- No broker order was submitted.
- The fill ledger remains at zero and the account remains at the fresh $100,000
  baseline with zero realized or unrealized P&L.
- The result is an auditable safe refusal, not a failed demo or a fabricated fill.
- The dashboard subprocess remains read-only with execution disabled.

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
- **Cover image:** `submission_assets/submission-brief.jpg`
- **Short description:** Thesis is an autonomous options agent where Grok
  proposes a falsifiable thesis, deterministic code proves bounded risk, and
  Alpaca paper infrastructure executes—or safely refuses.
- **Long description:** Paste `SUBMISSION.md` from “The problem” through “Why it
  is different,” including the disclaimer.
- **Technology tags:** Grok 4.6, xAI API, official Alpaca MCP server, Alpaca
  Trading API, Alpaca Market Data API, Python, FastAPI, SQLite, Replit
- **Repository:** <https://github.com/reynelfals/thesis-agent>
- **App URL:** <https://thesis-agent.replit.app>
- **Video:** <https://raw.githubusercontent.com/reynelfals/thesis-agent/main/thesis-agent/submission_assets/thesis-demo.mp4>
- **Slides:** <https://raw.githubusercontent.com/reynelfals/thesis-agent/main/thesis-agent/submission_assets/thesis-slides.pdf>
- **Paper account ID:** `[ENTER IN PRIVATE FORM — DO NOT COMMIT]`
- **Write-up:** Paste `SUBMISSION.md`
- **Social links:** Add up to five approved posts from `SOCIAL_POSTS.md`

Official challenge page:
<https://lablab.ai/event/raise-your-hack>