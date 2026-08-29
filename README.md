# Thesis

**Propose. Prove. Execute.**

Thesis is an autonomous, defined-risk options agent built for the Alpaca AI
Trading Agents Hackathon. Grok proposes a falsifiable market thesis,
deterministic code constructs and risk-gates a debit vertical, and Alpaca paper
infrastructure provides the execution and performance evidence.

The hosted FastAPI interface is a read-only audit console. It exposes the model
thesis, deterministic gates, sanitized Alpaca CLI/API traces, exact MLEG intent,
broker orders and fills, position monitoring, realized and unrealized P&L,
reconciliation, and the equity curve.

## Start here

- [Project documentation](thesis-agent/README.md)
- [One-page submission write-up](thesis-agent/SUBMISSION.md)
- [Verified one-page PDF](thesis-agent/submission_assets/thesis-one-page.pdf)
- [Presentation deck PDF](thesis-agent/submission_assets/thesis-slides.pdf)
- [Narrated demo video](thesis-agent/submission_assets/thesis-demo.mp4)
- [Demo script](thesis-agent/DEMO_SCRIPT.md)
- [Submission checklist](thesis-agent/SUBMISSION_CHECKLIST.md)
- [MIT license](LICENSE)

**Live audit console:** <https://thesis-agent.replit.app>

## Run

```bash
cd thesis-agent
bash scripts/install_alpaca_cli.sh
PYTHONPATH=. pytest -q
PYTHONPATH=. uvicorn thesis.web.app:app --host 0.0.0.0 --port 5000
```

Add broker and model credentials through Replit Secrets; never commit them.
Execution is disabled by default and the application is paper-trading only.

Paper trading only. This project is a hackathon demonstration, not investment
advice.