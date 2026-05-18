# Scoring System

Every open-market purchase (transaction code `P`) is scored 0–100. Scores above 45 are surfaced on the dashboard; scores above 65 or cluster buys trigger a Telegram alert.

---

## Hard Disqualifiers

These filters run before scoring. A disqualified filing scores 0 and is not stored as a signal.

| Filter | Reason |
|---|---|
| **Not a purchase** | Sales, option exercises, RSU grants, and awards are excluded. Only open-market buys (code `P`) carry predictive signal. |
| **10b5-1 plan flag** | Pre-arranged trading plan. The insider set this up months in advance — not a response to current conditions. Research shows zero alpha. |
| **Routine trader** | Insider bought in the same calendar month in ≥ 2 of the preceding 3 years (Cohen, Malloy & Pomorski 2012). Mechanical seasonal trades have near-zero predictive value regardless of role or size. Requires 3 years of history to apply; silently skips the check if data doesn't span that far (no false positives). |

---

## Scoring Factors

Factors are additive. The final score is capped at 100.

### Role

| Role | Points | Basis |
|---|---|---|
| CFO | +20 | Highest returns when buying own stock: 21.5%/yr avg (TipRanks/ResearchGate study). Deepest operational view of revenues, expenses, and cash position. |
| Director | +16 | 20.7%/yr avg. Board-level strategic visibility and fiduciary accountability. |
| Chairman | +14 | Strategic visibility similar to Director. |
| COO / Named Officer | +12 | 19.8%/yr avg. Operational insight, narrower than CFO. |
| CEO | +10 | 19.3%/yr avg — counterintuitively the weakest executive signal. CEO trades attract public scrutiny and can carry PR motivation. |
| Other | +6 | Lower operational visibility. |

### Market Cap

| Cap Tier | Points | Basis |
|---|---|---|
| Small-cap (< $2B) | +15 | +7.4% abnormal return at 12 months (Lakonishok & Lee 2001). Information asymmetry is highest here — analysts follow these stocks less closely, giving insiders a bigger edge. |
| Mid-cap ($2B–$10B) | +8 | Moderate information asymmetry benefit. |
| Large-cap (> $10B) | +0 | Widely followed, heavily analysed. Insider alpha is minimal. |
| Unknown | +5 | Can't determine tier; small positive assumed. |

### Transaction Value (Absolute)

| Value | Points | Basis |
|---|---|---|
| ≥ $500,000 | +12 | High-conviction capital commitment. Very few people casually spend $500K+ on a single stock purchase. |
| ≥ $100,000 | +8 | Meaningful commitment relative to typical insider incomes. |

### Purchase as % of Prior Holdings

Only fires when `shares_after > shares_bought` (i.e. the insider held shares before this purchase).

| Holdings Increase | Points | Basis |
|---|---|---|
| ≥ 30% | +15 | Large fraction-of-holdings purchases predict significantly positive abnormal returns (Pficdn et al.). A director doubling their stake is a much stronger signal than one adding 1%. |
| ≥ 15% | +10 | |
| ≥ 5% | +5 | |

### Timing

| Factor | Points | Basis |
|---|---|---|
| **First purchase in 12+ months** | +10 | Non-routine. The insider had 12 months of opportunities and chose now — suggests a specific view formed. |
| **Sequenced buying** — same insider bought again within 30 days | +8 | Sustained conviction. A second purchase shortly after the first suggests the insider is actively accumulating, not making a one-time gesture. |

### Price Context

| Factor | Points | Basis |
|---|---|---|
| **Within 5% of 52-week low** | +12 | Buying into weakness. Research on insider trades at 52-week extremes finds ~9.6% 1-year BHAR; the sharper the proximity, the stronger the signal. |
| **Within 10% of 52-week low** | +7 | Same pattern, weaker signal. Only one tier fires per trade. |

---

## Cluster Signals

A **cluster** is detected when 3 or more distinct insiders from the same company buy in a 14-day rolling window. The cluster flag is computed separately from the score.

The cluster flag affects **signal classification** — it does not add points to the numeric score. When multiple insiders buy independently at the same time, the cluster classification triggers an alert at a lower score threshold than a single insider buy.

Research basis: cluster signals generate roughly 2× the alpha of a single insider buy (multiple cluster studies).

---

## Signal Classification

| Condition | Signal Type | Action |
|---|---|---|
| Cluster flag AND score ≥ 50 | **⚡ CLUSTER_BUY** | Telegram alert sent immediately. Highest priority. |
| Score ≥ 65 | **✅ BUY** | Telegram alert sent immediately. |
| Score ≥ 45 | **👁 WATCH** | Logged and shown on dashboard. No alert. |
| Cluster flag AND score < 50 | **👁 WATCH** | Weak cluster — logged on dashboard, no alert. The score threshold ensures a cluster of low-conviction trades doesn't spam alerts. |
| Score < 45 | **LOW** | Stored in database but not surfaced. |

---

## Example Alert

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ CLUSTER BUY — $ACME · AcmeCorp Inc
Score: 82/100 · Small-cap · 2026-05-19
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

👥 3 insiders · $1,178,600 total

WHO BOUGHT:
  • Jane Smith (CFO) — 15,000 shares avg $42.10 = $631,500
    Now holds 45,000 (+50% increase)
  • Bob Lee (Director) — 5,000 shares @ $42.30 = $211,500
    Now holds 12,000 (+71% increase)
  • Tom Park (COO) — 3 buys · 8,000 shares avg $41.90 = $335,600
    Now holds 22,000 (+57% increase)
  3 insiders in a 9-day window → CLUSTER SIGNAL

SCORE BREAKDOWN (highest individual transaction):
  CFO purchase:                +20
  Small-cap ($1.8B):           +15
  Transaction value ≥$500K:   +12
  Holdings increase ≥30%:     +15  (Smith: +50% of prior position)
  First purchase in 12mo:     +10
  Within 5% of 52-week low:   +12  (4.8% above $38.90)
  ────────────────────────────────
  Total:                        84  → capped at 82 after rounding

CONTEXT:
  Stock is 4.8% above 52-week low ($38.90, set Apr 2)
  All purchases are open-market (not grants or exercises)
  None flagged as 10b5-1 pre-arranged plan
  Smith last bought shares 14 months ago

RESEARCH BASIS:
  Cluster: ~2× alpha vs single buy (multiple studies)
  CFO buy: 21.5% avg annual return (TipRanks/ResearchGate)
  Small-cap: +7.4% abnormal return at 12 months (Lakonishok & Lee 2001)

SUGGESTED HOLD: 60–90 days (Jeng et al. 2003 optimal window)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Score Range in Practice

A non-routine CFO buying a large fraction of their position in a small-cap stock near its 52-week low can score 70–85+. Routine traders and 10b5-1 filers are removed before scoring. Large-cap single buys by a CEO with a small position increase typically score 30–40 — below any alert threshold.

The cluster classification allows a moderate-score event (e.g. three directors each scoring 52) to trigger a CLUSTER_BUY alert that a single director at the same score would not.
