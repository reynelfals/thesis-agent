# Final submission checklist

**Deadline:** September 4, 2026 at 11:00 AM EDT  
**Track:** Options Alpha Agents

## Required deliverables

- [ ] Public GitHub repository
- [ ] Published application URL
- [ ] Demo video URL
- [x] One-page write-up from `SUBMISSION.md`
- [x] Verified single-page US Letter PDF at
  `submission_assets/thesis-one-page.pdf`
- [ ] Brand-new Alpaca paper account dedicated to the hackathon
- [ ] Competition account starting balance set to exactly $100,000
- [ ] Options trading level 3
- [ ] Full Alpaca paper account ID entered in lablab.ai’s private submission field
- [ ] Trading API, options strategy, and real Alpaca CLI path demonstrated
- [ ] Current P&L and trading activity visible from the submitted account
- [ ] Up to five optional X/LinkedIn build-in-public links

## Before publishing

```bash
export PATH="/home/runner/go/bin:$PATH"
cd thesis-agent
bash scripts/install_alpaca_cli.sh
PYTHONPATH=. pytest -q
PYTHONPATH=. python -m thesis.smoke
```

- [ ] Dashboard loads without a blank screen
- [ ] `/api/health` reports ready
- [ ] All displayed account identifiers remain masked
- [ ] Repository contains no `.env`, keys, raw broker responses, or screenshots
  containing credentials
- [ ] `THESIS_ALLOW_EXECUTE=0` for normal dashboard hosting

## Monday one-shot CLI order capture

Do this after **9:30 AM EDT on Monday, August 31, 2026**, while the US options
market is open.

1. Open the dashboard and confirm paper endpoint, fresh-account baseline, options
   level 3, broker reads, and CLI readiness.
2. Keep execution disabled and run one read-only cycle first:

   ```bash
   cd thesis-agent
   THESIS_ALLOW_EXECUTE=0 PYTHONPATH=. python scripts/run_cycle.py
   ```

3. Review the proposed thesis and every gate. If the result is `no_trade`,
   `rejected`, or `blocked`, accept it. Do not lower conviction, change risk limits,
   fabricate quotes, or substitute a hand-built order.
4. Only for a one-shot eligible cycle, set `THESIS_ALLOW_EXECUTE=1` through Replit
   Secrets/environment controls and run:

   ```bash
   PYTHONPATH=. python scripts/run_cycle.py
   ```

5. Immediately restore `THESIS_ALLOW_EXECUTE=0`.
6. If an order was submitted, wait for the dashboard refresh and capture:
   - CLI `api POST /v2/orders` success
   - returned paper order ID
   - broker status and fills
   - linked position/monitoring state
   - updated P&L and equity curve
7. If no order qualified, keep the refusal as evidence and try only after genuinely
   new market information. Never loop until the model happens to say yes.

## Final lablab.ai form

- **Project name:** Thesis
- **Tagline:** Propose. Prove. Execute.
- **Category:** Options Alpha Agents
- **Repository:** `[ADD PUBLIC GITHUB URL]`
- **App URL:** `[ADD PUBLISHED REPLIT URL]`
- **Video:** `[ADD VIDEO URL]`
- **Paper account ID:** `[ENTER IN PRIVATE FORM — DO NOT COMMIT]`
- **Write-up:** Paste `SUBMISSION.md`
- **Social links:** Add up to five approved posts from `SOCIAL_POSTS.md`

Official challenge page:
<https://lablab.ai/event/raise-your-hack>