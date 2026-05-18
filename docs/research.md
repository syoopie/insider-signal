# Research References

All scoring factors, thresholds, and disqualifiers are grounded in peer-reviewed academic research. This document records the specific findings applied and how they map to the implementation.

---

## Core Studies

### Lakonishok & Lee (2001)
*"Are Insider Trades Informative?"*
Review of Financial Studies, 14(1), 79–111.

**Finding:** Small-cap insider purchases generate +7.4% abnormal returns over 12 months. Large-cap insider purchases show near-zero alpha.

**Applied as:** `cap_small` = +15, `cap_large` = +0 in the scoring table. The large information asymmetry in small companies — fewer analysts, less media coverage — is why insiders have a bigger edge there.

---

### Jeng, Metrick & Zeckhauser (2003)
*"Estimating the Returns to Insider Trading: A Performance-Evaluation Perspective."*
Review of Economics and Statistics, 85(2), 453–471.

**Finding:** Insider purchase portfolios earn approximately 6% annualized alpha over the broad market. Optimal holding horizon: 60–90 days.

**Applied as:** Benchmark for expected signal performance. Hold horizon recommendation shown in every alert and on the dashboard. Scores purchases only (not sales) for consistency with the study's methodology.

---

### Cohen, Malloy & Pomorski (2012)
*"Decoding Inside Information."*
Journal of Finance, 67(3), 1009–1043.

**Finding:** "Opportunistic" insider trades (non-routine, not pre-arranged) earn 82 basis points/month (~9.8%/year). "Routine" trades — where an insider has a consistent seasonal buying pattern — earn approximately zero alpha. The distinction matters more than role or company size.

**Applied as two separate controls:**
1. **10b5-1 plan disqualifier** — pre-arranged trading plans are excluded entirely before scoring.
2. **Routine trader disqualifier** — an insider who has bought in the same calendar month in ≥ 2 of the preceding 3 years is classified as routine and excluded. Requires 3 years of history; silently skips the check when data doesn't span that far.

---

### TipRanks / ResearchGate CFO Study
*(Multiple attributed authors — see ResearchGate for full citation)*

**Finding:** Average annual returns when insiders buy their own stock:
- CFO: 21.5%
- Director: 20.7%
- Named Officer: 19.8%
- CEO: 19.3%

**Applied as:** Role scoring — CFO highest (+20), CEO lowest among executives (+10). This is counterintuitive and worth noting explicitly in any presentation of the signals.

---

### Cluster Research (Multiple Studies)

**Finding:** When 3 or more insiders from the same company buy independently within a short window, the resulting signal generates approximately 2× the alpha of a single insider buy.

**Applied as:** Cluster detection (3+ insiders, 14-day rolling window) → CLUSTER_BUY classification. Cluster flag triggers alerts at a lower score threshold (≥ 50) than a single buy (≥ 65).

---

### Pficdn et al. — Holdings Fraction Studies

**Finding:** Large purchases expressed as a percentage of an insider's existing position predict significantly positive future abnormal returns. Small position-fraction purchases (even large in absolute dollar terms) are not reliably informative.

**Applied as:** `holdings_increase_*` score factors — 5%, 15%, and 30% increase tiers. A director adding 0.1% to a $50M position is treated differently from a director doubling their $100K position.

---

### 52-Week Low Studies (City Research Online and others)

**Finding:** Insider buy trades near the 52-week low generate ~9.6% 1-year buy-and-hold abnormal returns. The recency of the trade relative to the low matters — the top decile of recency generates ~30.8% 1-year BHAR.

**Applied as:** Tiered 52-week low scoring:
- Within 5% of low: +12 (sharper proximity = stronger conviction signal)
- Within 10% of low: +7 (still informative, weaker tier)

The recency-of-low component (when was the low set) is not currently implemented — extracting the date from the Yahoo Finance OHLC series adds code complexity for marginal gain given that Yahoo Finance is unreliable on GitHub Actions IPs.

---

## Factors Not Implemented (and Why)

**Filing lag as a penalty** — A 9-day filing vs. a 1-day filing might indicate lower urgency. Directionally sensible, but no robust empirical backing was found. Not added without backtest evidence.

**Recent selling history as a penalty** — Directionally logical (an insider who just sold shouldn't score high for buying). But insider sell informativeness is weak and inconsistent in the research. Not recommended without caution.

**Ownership % of company as a signal** — Evidence is curvilinear: moderate ownership increases informativeness, but very high ownership may reflect entrenchment rather than information. Not a clean additive factor.

**Near 52-week high penalty** — Literature is genuinely split. One study shows counter-anchoring purchases (buying near the high) outperform by 3.9% over 60 days. Another (Lee & Piqueira 2019) finds these trades predict losses. No adjustment made in either direction given the empirical conflict.

---

## Backtesting Methodology

To avoid biased results, the backtest engine applies several controls:

- **No look-ahead:** Signal date = filing date + 1 calendar day (never transaction date). Transactions can occur weeks before the filing; using the transaction date would assume knowledge before public disclosure.
- **Execution lag:** Entry price is fetched at signal date + 3 calendar days (realistic fill lag for a retail investor seeing the alert).
- **Delisted stocks:** When yfinance returns no data for a ticker, it is treated as a −50% loss (survivorship bias correction). Using last available price would overstate returns.
- **No parameter tuning:** The score threshold (65) and cluster window (14 days) come from the literature, not from optimising on backtest results.

Horizons evaluated: 30, 60, 90, 180 days.
