# Thesis scientific edge study

**Status:** `not_validated`

**Conclusion:** No scientifically validated positive trading edge is established.

**Decision:** Reject the positive-edge claim. Required point-in-time evidence is unavailable and no return estimate was manufactured.

## Scientific interpretation

This report tests whether the available evidence supports a positive risk-adjusted return claim. It does not treat software tests, safety gates, paper-account readiness, or one trade as evidence of alpha.

## Point-in-time data capability audit

| Evidence | HTTP | Rows | Available |
| --- | ---: | ---: | :---: |
| stock bars | 200 | 5 | yes |
| expired option contracts | 200 | 112 | yes |
| historical option bars | 200 | 100 | yes |
| historical option trades | 200 | 100 | yes |
| historical option nbbo quotes | 404 | 0 | no |

## Why the edge claim is rejected

- Alpaca exposes historical option bars and trades but the historical option quotes endpoint is unavailable; neither bars nor last trades are acceptable substitutes for contemporaneous NBBO fills.
- The deployed Grok strategy has no historical archive of contemporaneously recorded decisions and therefore cannot be reconstructed without hindsight.
- The deployed strategy has no executable exit policy; the frozen study exit rules are prospective and cannot be represented as historical production behavior.

## Pre-registered performance fields

| Metric | Result |
| --- | --- |
| trade count | 0 |
| expectancy dollars | None |
| hit rate | None |
| cagr | None |
| annualized volatility | None |
| sharpe | None |
| sortino | None |
| maximum drawdown | None |
| turnover | None |
| exposure | None |
| adjusted p value | None |

Null results mean the required evidence does not exist; they are not zero returns.

## Evidence required before retesting

- A licensed point-in-time OPRA NBBO dataset for both spread legs
- A prospective archive of every candidate and contemporaneous Grok decision
- At least 100 completed trades over at least 365 calendar days
- At least 15 completed trades in each pre-registered regime
- Daily mark-to-market strategy and benchmark equity curves
- Walk-forward and untouched out-of-sample results net of modeled costs

## Guardrail

Thesis remains a paper-only, defined-risk experimental framework. This report is not investment advice and does not establish profitability.
