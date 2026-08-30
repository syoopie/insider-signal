# Scoring Improvement Plan

Written 2026-08-29, against the clean post-audit baseline (backtest run_date 2026-08-29).

This plan argues that **the scoring model cannot be improved by changing weights or adding
factors until the measurement apparatus is rebuilt**, and lays out the rebuild, the
evaluation protocol, and the variables worth adding once those exist.

Every number below was measured against the production database or live Yahoo Finance on
2026-08-29. Where something is inference rather than measurement it says so.

---

## 1. Summary

The current model is a four-factor conjunction wearing the costume of a 100-point additive
score, tuned across five rounds by a procedure that cannot distinguish signal from noise.

Three facts, each independently sufficient to block progress:

1. **The score has a theoretical maximum of 61 and the BUY threshold is 60.** A signal is a
   BUY if and only if all four positive factors fire. There is no ranking, no headroom, and
   no way for one factor to compensate for another.
2. **The negative class is thrown away.** 9,477 insider purchase-days produce 1,406 stored
   signals, because `signal_type == "LOW"` is discarded before the write. Of those, 347 are
   ever priced. We are fitting a model on 3.7% of the data, selected by the model itself.
3. **Factor weights are set by univariate lift computed on the sample that already passed the
   filter.** Conditioning on a sum induces negative correlation among its terms, so this
   procedure manufactures spurious negative lift for heavily-weighted factors. The tuning
   history is consistent with it having measured mostly that artifact.

Fixing measurement is worth more than any new variable. The plan is therefore ordered:
substrate first, protocol second, model form third, new variables fourth.

---

## 2. Evidence

### 2.1 The score is degenerate

Maximum attainable score from the current weight table:

```
role_director            +16   (best role)
cap_small                +15   (best cap tier)
holdings_increase_5pct   +15
prior_purchase_31_365d   +15   (best timing factor)
                        ----
                          61
```

Observed distribution over all 1,406 stored signals:

| score | signals | of which BUY |
|---|---|---|
| 61 | 222 | 202 |
| 60 | 51 | 44 |
| 46 | 594 | 0 |
| 45 | 133 | 0 |
| everything else | 406 | 2 |

246 of 248 BUY signals sit at 60 or 61. Two values (46 and 61) hold 58% of the population.
The four scores above 61 are stale rows from a May 2026 scoring round, discussed in 2.5.

`min(score, 100)` in `score_transaction` has never been reached and cannot be.

**Consequence.** "Score ≥ 60" is the boolean `director-or-cfo AND small-cap AND holdings-up-5%
AND prior-purchase`. Every weight change of more than one point relocates a large block of
signals across the threshold at once, which is why successive tuning rounds swung
`first_purchase_12mo` from +10 to −10 and `role_ceo` from +10 to −5 on a 61-point scale.

### 2.2 The four load-bearing factors barely discriminate

Fire rate across the 1,406 stored signals:

| factor | fires | rate | weight |
|---|---|---|---|
| `cap_small` | 1,078 | 77% | +15 |
| `role_director` | 1,070 | 76% | +16 |
| `holdings_increase_5pct` | 1,043 | 74% | +15 |
| `prior_purchase_31_365d` | 797 | 57% | +15 |

A factor present in three quarters of the population carries almost no information about
which member of that population to pick. These four are the entire model.

The rates are themselves inflated by selection: they are conditional on a signal having been
stored, and storage requires clearing WATCH, which these factors are what produce.

Meanwhile the cells that recent weight changes were based on:
`role_chairman` n=2, `role_ceo` n=18, `cap_mid` n=19, `role_officer` n=72.

### 2.3 The negative class is discarded

`scripts/backfill_signals.py:403` — `if signal_type == "LOW": continue`.

| stage | count |
|---|---|
| P transaction rows, last 730d | 13,294 |
| eligible (not 10b5-1) | 11,938 |
| distinct insider purchase-days | **9,477** across 1,314 issuers |
| stored signals (all types) | **1,406** — 1,021 WATCH, 248 BUY, 137 CLUSTER_BUY |
| priced by the backtest, 30d horizon | **347** |
| priced by the backtest, 180d horizon | 253 |

Two separate losses. LOW is dropped entirely, and signals are keyed `(ticker, signal_date)`,
so five insiders buying the same company on the same day collapse into one row carrying
**only the highest-scoring insider's breakdown** (`backfill_signals.py:237-239`). The other
four contribute nothing to any subsequent analysis, and the return gets attributed to one
person's factors.

You cannot estimate whether a factor predicts return from a sample that only contains
observations the factor helped select.

### 2.4 Returns are not dividend-adjusted

`src/market/prices.py:239` reads `indicators.quote[0].close`. Yahoo's chart API also exposes
`indicators.adjclose[0].adjclose`. Measured on 2026-08-29:

| symbol | window | raw close return | adjusted return | gap |
|---|---|---|---|---|
| NVDA (10:1 split) | 2024-05-20 → 2024-06-20 | +43.05% | +43.06% | 0.01pp |
| SPY | 2024 full year | +24.45% | +26.05% | **1.60pp** |
| T (high yield) | 2024 full year | +31.07% | +39.19% | **8.12pp** |

Splits are already handled — the NVDA test rules out the catastrophic failure mode. Dividends
are not.

This is not a wash against the SPY benchmark, because the error is proportional to
(ticker yield − SPY yield) × horizon. Insider buying works best in small-cap value names,
which yield more than SPY. **We are systematically understating the excess return of exactly
the signals the system is built to find**, in proportion to holding period, and any factor
correlated with dividend yield has its lift mismeasured. One-line fix, material effect.

### 2.5 The weight-setting tool has its own defects

`scripts/analyze_factors.py` is what produced every weight in the table. Three problems:

- **Fuzzy join.** The backtest's `detail` rows carry no signal id and no signal date
  (`engine.py:262-271`), so the analysis matches a return to a signal by searching for
  `abs((exec_date − signal_date).days − 4) < 8` (`analyze_factors.py:100-111`). For a ticker
  with several signals in a fortnight this can attribute a return to the wrong one.
- **Stale rows in scope.** Its signal query has no date filter. 204 signals dated 2024-05-16
  to 2024-07-29 predate the 730-day backfill window and still carry breakdowns from the May
  2026 model — 156 of them reference factors that no longer exist (`value_500k_plus`,
  `holdings_increase_30pct`, `fast_filing_0_1d`, `near_52wk_low_*`). *Measured:* zero of
  these fall inside the current backtest window, and the nearest is more than 30 days from
  the earliest `exec_date`, so they are not contaminating today's output. It is a live
  landmine, not a live wound.
- **Univariate lift.** `avg(return | factor present) − avg(return | factor absent)` ignores
  every other factor. `cap_small` and `role_director` co-occur heavily; shared variance is
  credited to both.

Combined with a filtered sample, univariate lift is not a weak estimator of factor value.
It is a biased one, and the bias has the wrong sign for the factors that matter most.

### 2.6 No holdout, and the data is already burned

Five tuning rounds, roughly 27 candidate factors, one 730-day sample, no train/test split, no
standard errors, no multiple-comparison control. The 730-day window is also a single market
regime, and overlapping 180-day holding windows mean the 253 observations at that horizon
contain far fewer than 253 independent draws.

CLAUDE.md already carries the caveat that round 4/5 weights were derived while the
`transaction_date` type bug was active. That is correct and it is the smaller problem.

### 2.7 What is *not* wrong

Worth stating so effort does not go here:

- The ingest and parser are correct as of the 2026-08-29 audit.
- `purchase_rollup` is the right grain for a purchase and is shared by all three call sites.
- Cluster detection filters (identical-block, same-price-offering, direct-only, $25k floor)
  are sound and tested.
- Splits are handled.
- The `filed_date` windowing and the `exec_date = filed_date + 4` convention are
  point-in-time correct. No look-ahead.
- Signal dating is fine and documented.

---

## 3. Phase 1 — Build the research substrate

No scoring change in this phase. Nothing here alters a single stored score.

### 1A. A local daily price panel

**What.** Adjusted daily closes, raw closes, and volume for all 1,364 tickers that have ever
had a P transaction, plus SPY, IWM, and a handful of sector ETFs, over the full 730+ day
window. One Yahoo request per symbol for the whole range.

**Cost.** ~1,370 requests at the existing 0.5s throttle ≈ 12 minutes, once. ~690k rows.
Stored as parquet under `data/prices/` (gitignored, ~10–15MB compressed), not in Neon.

**Why it is the highest-leverage item.** It converts every downstream question from a 30-minute
network-bound job into a local join. Labelling all 9,477 purchase-days at four horizons
becomes seconds. That changes what experiments are affordable, which is the actual constraint
on this project.

**Why not in Neon.** The database is at 105MB of the 500MB free tier (transactions 57MB,
form4_filings 30MB). A 690k-row price table with its index would be 60–80MB. Affordable but
not free, and the pipeline does not need it — only research does.

### 1B. Point-in-time price context stored on the transaction

This is how the 52-week-low factor gets reinstated without violating the invariant that
killed it.

CLAUDE.md is explicit: *"Do not add a factor only one path can compute."* The old factor
broke that because backfill had no price history. The fix is not to give backfill live
prices; it is to **store the price context as of the transaction date on the transaction row**,
so the scorer stays a pure function of stored data.

```sql
ALTER TABLE transactions
  ADD COLUMN IF NOT EXISTS px_close_at_tx        NUMERIC,
  ADD COLUMN IF NOT EXISTS px_52wk_high_at_tx    NUMERIC,
  ADD COLUMN IF NOT EXISTS px_52wk_low_at_tx     NUMERIC,
  ADD COLUMN IF NOT EXISTS px_ret_21d_at_tx      NUMERIC,
  ADD COLUMN IF NOT EXISTS px_ret_63d_at_tx      NUMERIC,
  ADD COLUMN IF NOT EXISTS px_ret_252d_at_tx     NUMERIC,
  ADD COLUMN IF NOT EXISTS px_vol_21d_at_tx      NUMERIC,
  ADD COLUMN IF NOT EXISTS px_dollar_vol_21d     NUMERIC;
```

- **Live path** (`run_ingest.py`): one YF fetch per ticker per run, which it already does for
  `get_market_data`. Computes and stores.
- **History**: a one-time `scripts/backfill_price_context.py` computing the same values from
  the parquet panel.
- **Scorer**: reads the stored columns. Both paths agree by construction.

~13k rows × 8 numerics ≈ 1MB in Neon. Negligible.

Both writers must use one shared function so they cannot drift — the same discipline
`src/db/purchases.py` applies to the rollup. Put it in `src/market/context.py`.

### 1C. Persist the negative class

Add a `scored_purchases` table at the true grain — one row per (filing, insider,
transaction_date, is_direct) — holding the score, the full breakdown, and every candidate
feature, for **every** eligible P transaction including the ones that classify LOW.

`signals` stays exactly as it is: it is the alerting and dashboard surface and nothing about
it should change. `scored_purchases` is the research table. Roughly 9,477 rows per backfill,
a few MB.

This is the single change that makes model fitting possible.

### 1D. Label everything

`scripts/build_research_dataset.py` joins `scored_purchases` to the price panel and emits one
parquet with, per purchase:

- forward returns at 30/60/90/180d from `exec_date = filed_date + 4`
- excess vs SPY, vs IWM, vs the SIC-division sector ETF, and vs a size-matched bucket
- a delisting flag, and the `no_data` / `error` distinction the audit already established
- every feature from section 5

Reproducible from a committed script, re-runnable in seconds, the input to everything in
phases 2 and 3.

### 1E. Repairs

- Switch `get_price_change` to `adjclose` with a fallback to `close` when absent (2.4).
- Add `signal_id` and `signal_date` to backtest `detail` rows; delete the fuzzy join in
  `analyze_factors.py` (2.5).
- Delete or rescore the 204 pre-window stale signals (2.5).
- Add a date filter to `analyze_factors.py`'s signal query.

**Phase 1 exit criterion.** `build_research_dataset.py` produces ≥9,000 labelled purchases at
the 90-day horizon, and re-running the existing backtest against the price panel reproduces
the 2026-08-29 headline numbers within 0.5pp on raw closes. If it does not reproduce, the
panel is wrong and nothing downstream is trustworthy.

### Outcome, measured 2026-08-30

The reproduction half passed decisively. `scripts/verify_price_panel.py` recomputes every
signal in the last backtest from the panel and compares it against the value the network path
stored: **mean absolute difference 0.003pp, 100% of rows within 0.5pp, at all four horizons**.
The panel is equivalent to the path it replaces.

The ≥9,000 half was **missed because the target was wrong**, not because the build fell short.
9,000 came from the 9,477 insider-day count in section 2.3, which is a count before
eligibility and before horizon completion. The real funnel:

| stage | count |
|---|---|
| purchase-days at rollup grain, 730d | 10,264 |
| eligible (not 10b5-1, not routine, $2k–$1B) | 8,587 |
| 90-day exit already completed | 7,716 |
| **labelled at 90d** | **7,576** |

7,576 of 7,716 possible is 98.2% coverage; the 140 misses are 116 `no_entry` and 24 symbols
absent from the panel. Against the 331 signals the current backtest prices at 90d, this is a
**23× larger sample**. The corrected gate is ≥7,500 labelled at 90d, which is met.

Three findings fell out of the build.

**The "no model" baseline now exists.** Every eligible purchase, held 90 days from
`filed_date + 4`, averages **+2.83% excess over SPY with a median of −2.36%**. The current
model's selected signals average +7.12% at the same horizon. So the model does add value over
buying every insider purchase, which had never been measured. Both medians are negative.

**Yahoo has no usable history for 19 of 1,371 symbols (1.4%).** Six return nothing; thirteen
more — EQR, SEM, WSR, AVNS among them — return a few weeks, with `firstTradeDate` in mid-2026
and no `1y` or `5y` in `validRanges`. EQR is a decades-old REIT, so this is a gap in the
source, not a parsing error. Those purchases are classified `no_entry` rather than given an
invented return.

**Five tickers are unresolvable by any price API** because filers typed them by hand:
`(CALX)`, `N O G`, `NYSE/TRN`, `BFA, BFB`, `WLY, WLYB`. `_clean_ticker` now strips the
unambiguous noise and refuses the ambiguous cases, because taking the first of `BFA, BFB`
would file Brown-Forman's purchases under an unrelated ETF. The five stored rows still need a
repair; `audit_data.py` now flags them.

---

## 4. Phase 2 — The evaluation protocol

Fix this before fitting anything. It is what decides whether a change is real.

**Splits.** Strict time order, no shuffling. Roughly:

| split | window | approx purchases |
|---|---|---|
| train | 2024-08 → 2025-11 | ~5,500 |
| validation | 2025-12 → 2026-03 | ~1,500 |
| test | 2026-04 → 2026-08 | ~1,200 |

Test is touched **once**, at the end. The 180d horizon has almost no completed test-period
exits, so treat 60d/90d as primary and 180d as a directional check only.

**Purge and embargo.** A 90-day holding window straddles the boundary. Purge any training
observation whose exit date falls inside the validation window, and embargo a further
horizon-length gap. Without this the split leaks.

**Effective sample size.** Overlapping windows and clustering by ticker mean nominal n
overstates independence badly. Report the number of distinct (ticker, month) cells alongside
every n, and cluster standard errors by ticker and by calendar month. A result that survives
only under i.i.d. assumptions is not a result.

**Estimation.** Multivariate, not univariate. Cross-sectional regression of excess return on
standardised features, plus a logistic model of P(excess > 0), both with clustered SEs.
Report coefficient, SE, t-stat, and n for every factor. A factor with n < 100 in the training
split does not get a weight, it gets a note.

**Multiple comparisons.** Benjamini-Hochberg across the candidate set at each round, with the
false-discovery rate stated. Five prior rounds already spent degrees of freedom on this data;
treat borderline results with more suspicion than the p-value alone suggests.

**Baselines to beat.** Any proposal must beat all four out-of-sample:

1. Every eligible P purchase, equal weight (the "no model" baseline).
2. Every small-cap eligible purchase.
3. The current model as it stands today.
4. A random ranking with the same selection count.

**Reported metrics.** Mean and median excess return, hit rate, information ratio, decile
spread of the score (top decile minus bottom decile — this is the direct test of whether the
score ranks), max drawdown of an equal-weight portfolio, and the fraction of return coming
from the top 5% of trades. The 2026-08-29 run has negative median excess at 90d and 180d
against strongly positive means; any successor model must be judged on whether it improves
the median, not just the mean.

---

## 5. Phase 3 — Model form

Three candidates, evaluated head to head under the section 4 protocol.

**A. Recalibrated additive score.** Keep the integer point system, but fit the weights to
standardised multivariate coefficients and rescale so the realised distribution spans most of
0–100 instead of piling on two values. Preserves the dashboard's scoring explainer and the
evidence blob unchanged.

**B. Regularised logistic regression** on standardised features, predicting P(excess > 0) at
90d, with the output mapped to a 0–100 score by its in-sample percentile. Ridge or elastic
net, penalty chosen on validation. Coefficients remain readable, which matters because
`/how-it-works` explains the model to the user.

**C. Gradient-boosted trees.** The published microcap work
([arXiv 2602.06198](https://arxiv.org/html/2602.06198)) reports AUC 0.70 on 17,237 microcap
purchases with 11 features and a strict temporal split. That is roughly twice our sample with
a narrower universe.

**Recommendation: B for production, C as a ceiling check only.** At ~5,500 training
observations clustered into far fewer independent cells, a boosted model will fit noise and
the protocol above will not reliably catch it. Run C to learn what the achievable ceiling
looks like and which features carry it; ship B unless C beats it out-of-sample by a margin
that survives clustered standard errors.

**Separately, and possibly more important than the model: change what the threshold means.**
Replace "score ≥ 60" with "top K signals per week by score". A fixed score cutoff on a
distribution that shifts with the weight table is why every retune reshuffles the alert
volume. A rank cutoff makes alert volume a decision, not an accident, and makes successive
models directly comparable at equal selectivity.

---

## 6. Phase 4 — Variables

Ordered by expected value per unit of effort. Each entry says how to get it and how to use it.

### Tier 1 — already in the database, no new fetching

**Net insider demand at the firm.** We store **91,296 sale rows across 1,831 issuers** and
ignore every one of them. Construct, over the 90 days before the trade: dollar buys ÷ (buys +
sells), the count of distinct buyers minus distinct sellers, and a flag for whether *this*
insider also sold recently. Lakonishok & Lee's headline result is a buy-minus-sell spread,
and we are only using half of it. Cheapest large win available.

**Insider track record.** More than 1,400 insiders have ≥2 distinct purchase days (670 have
exactly 2, 285 have 3, 153 have 4). For each purchase, the mean excess return of that
insider's *prior* purchases, strictly point-in-time, shrunk toward zero by
`n / (n + k)` with k tuned on validation. Supported by
[Cline et al., *The Persistence of Opportunistic Insider Trading*](https://onlinelibrary.wiley.com/doi/10.1111/fima.12177)
(Financial Management 2017). Per-insider n is thin, so also compute it at the firm level,
where n is much larger.

**Averaging down.** Purchase price relative to this insider's own most recent prior purchase
price at the same issuer, and relative to the issuer's 90-day return. The worst outcome in
the entire history — LGF at −63.1% — is documented in CLAUDE.md as "insiders averaging down"
with no filter implemented. This is that filter, and it is computable from data we already
hold.

**Cluster intensity, continuous.** Replace the binary `cluster_flag` in scoring with: distinct
buyer count, total dollar value, number of distinct role categories represented, span in days,
and buyers as a fraction of the issuer's known insider roster (derivable from all filers ever
seen for that CIK). The current 3-insider/14-day rule stays as the *detection* definition for
`/clusters`; this is about what the scorer sees.

**Purchase size relative to the insider's own history.** Current transaction value ÷ that
insider's trailing mean. The arXiv model ranks this 9th of 11 by importance. Note that raw
dollar value was removed in round 4 for negative lift, which is a finding this normalised
version should be tested against rather than assumed to inherit.

**Sector.** `sic_description` now covers 2,042 of 2,142 companies. Use the SIC division as a
categorical and, more valuably, as the benchmark leg for a sector-relative label.

**Joint role.** Officers who also sit on the board, parsed from the raw title string. Cheap,
and the literature finds the effect concentrated in managers rather than large shareholders.

**Filing lag — as a rare-event flag, not a continuous factor.** *Measured:* 12,247 of 13,294
P transactions file within 0–4 days. There is almost no variance to exploit, which retro-
actively justifies disabling `fast_filing_0_1d` (though not the reasoning given at the time).
But **330 transactions file 30+ days late**, up to 3,075 days. Late disclosure is a different
animal and deserves its own flag. One row files *before* the transaction date; that is a data
error and should be caught by `audit_data.py`.

### Tier 2 — free, unlocked by the price panel (Phase 1A/1B)

**Distance from the 52-week high, as of the transaction date.** The arXiv microcap model puts
this at 0.360 of total feature importance, more than four times the next feature. Treat that
number with care — gain-based importance favours continuous features over binary ones, so it
overstates the gap against flags. But it is the strongest external evidence available that our
deliberate removal of price context cost real predictive power.

**Distance from the 52-week low, as of the transaction date.** The factor CLAUDE.md removed,
reinstated correctly this time: point-in-time, stored at ingest, computable by both paths.

**Momentum: 21d / 63d / 252d returns to the transaction date.** The same paper reports a
*monotonic* relationship in the direction opposite to naive contrarian intuition —
transactions disclosed after >10% appreciation returned a mean CAR of 6.3% versus 2.3% after
declines. Test both signs; do not assume ours will match a microcap-only sample.

**Realised volatility (21d) and dollar volume (21d).** Volume doubles as a liquidity screen.
The arXiv sample required ≥$200k average daily dollar volume; we impose no liquidity filter
at all, and thin names are where a backtest most overstates what is tradeable.

**Price deviation between transaction and disclosure.** What the stock did in the 0–4 days
between the trade and the filing. Ranked 8th of 11 in the reference model.

**Better labels.** Sector-relative and size-matched excess returns instead of SPY alone, and
a beta-adjusted variant. Some of what currently reads as insider alpha is a small-cap beta
loading that IWM already partly reveals.

### Tier 3 — free, new fetching, moderate effort

**Fundamentals from EDGAR XBRL bulk frames.** Exactly the mechanism `refresh_market_caps.py`
already uses for shares outstanding, pointed at `StockholdersEquity`, `Revenues`,
`NetIncomeLoss`, `AssetsCurrent`, `LiabilitiesCurrent`. Gives book-to-market, profitability,
and leverage. Lakonishok & Lee find insider predictive power concentrated in value stocks, so
book-to-market is the single most theoretically-motivated addition on this list. One bulk call
per concept per quarter. Point-in-time discipline is essential: use the period actually filed
and available as of the transaction date, never the latest.

**Earnings and filing proximity.** Days from the purchase to the nearest 10-Q/10-K, from the
EDGAR submissions API that `edgar.py` already caches. Distinguishes a buy in an open window
just after results from one at a quarter-end.

**Short interest.** FINRA publishes consolidated equity short interest free, bi-monthly, via
[its data catalogue](https://www.finra.org/finra-data/browse-catalog/equity-short-interest)
and an [API](https://api.finra.org/data/group/otcMarket/name/EquityShortInterest). Insider
buying against heavy short interest is a well-known setup. Ranked below fundamentals because
it is bi-monthly, needs its own symbol mapping, and the exchange-listed and OTC files differ.

### Tier 4 — explicitly not proposed

- **13F institutional ownership.** Heavy parsing, quarterly, stale by up to 45 days.
- **Table II derivative activity.** Requires a parser change plus a full 2-year re-bootstrap.
  Revisit only if Tier 1–3 stalls.
- **News and sentiment.** No free source with the coverage and point-in-time integrity this
  needs. Anything cheap enough to use here is look-ahead-contaminated.
- **A biotech/pharma dummy** as a standalone feature. The reference model includes one, but
  with `sic_description` we get the whole sector partition properly rather than one hand-picked
  industry.

---

## 7. Sequencing

Each phase ends in a falsifiable check. Do not start the next until the current one passes.

| # | Work | Gate |
|---|---|---|
| 1 | Adjusted closes (2.4); `signal_id` in backtest detail; purge stale signals | Backtest re-runs; measured delta from dividend adjustment reported per horizon. **Done 2026-08-29: +0.02 / +0.13 / +0.28 / +0.62pp at 30/60/90/180d** |
| 2 | Price panel + `build_research_dataset.py` | ~~≥9,000~~ ≥7,500 labelled purchases at 90d; existing backtest reproduced within 0.5pp. **Done 2026-08-30: 7,576 labelled, 0.003pp** |
| 3 | Score the negative class | ~~`scored_purchases` table~~ **Done 2026-08-30: no new table. The backfill's scoring loop moved to `src/signals/batch.py` and the research builder calls it, so there is one definition and no second writer. 9,336 scored; parity with the pipeline 99.08%** |
| 4 | Evaluation protocol as a committed script | **Done 2026-08-30: splits 2,925 / 1,536 / 762 with purge and embargo; all four baselines computed** |
| 5 | Tier 1 features + multivariate estimation on train/validation | **Done 2026-08-30: 6 of 38 candidates survive FDR 5%, two of them the same finding at r=+0.92** |
| 6 | Tier 2 features | **Done 2026-08-30 in the research dataset. The `price_context` columns on `transactions` were NOT added, because no model shipped that reads them. Add them only alongside a model that does** |
| 7 | Fit A / B / C; select on validation | **Done 2026-08-30: B selected at +23.83% against the current score's +5.13%. C not fitted, sample too small. A stability guard was added mid-phase; see 7a** |
| 8 | Single test-set evaluation | **Done 2026-08-30: fails 1 of 6 pre-registered bars, ranks on the median. Null result** |
| 9 | Ship | **Nothing shipped that changes a score. Five corrections and the whole apparatus landed. See 7a** |

Phases 1–4 change no scores and can land incrementally without invalidating the database.
Phase 6 onward triggers the golden rule.

## 7a. Outcomes, 2026-08-30

All nine phases ran. **No scoring change shipped, and that is the finding.**

### What the negative class showed (Phase 3)

Scoring every purchase instead of only the ones the model selects turned 331
priced signals into 8,306 labelled ones at 90 days. On that sample the score's
deciles are flat and non-monotone: the bottom decile (scores −30 to 0) returns
+3.90% mean and +1.25% median excess, the top decile (46–61) returns about +5%
mean and a *negative* median. Deciles 5 and 6 beat deciles 7, 8 and 9. Section
2.1 argued from the weight table that the score was a four-factor conjunction
rather than a ranking. This measures it.

### What the factor estimates showed (Phase 5)

Multivariate, clustered on ticker, Benjamini-Hochberg across 38 candidates, on
the training split (n=3,655 over 819 tickers):

| feature | beta per sd | verdict |
|---|---|---|
| net insider demand (`demand_buy_ratio`) | +6.08pp | survives FDR 5% |
| `demand_net_dollars` | −6.09pp | same finding, r = +0.92 with the above |
| `tx_pct_above_52wk_low` | +5.83pp | survives |
| `tx_ret_21d` | −5.52pp | survives, short-term reversal |
| `f_cap_small` | −4.75pp | survives, **opposite sign to its +15 weight** |
| `f_role_director` (+16) | −0.31pp | indistinguishable from zero |
| `f_holdings_increase_5pct` (+15) | +0.61pp | indistinguishable from zero |
| `f_prior_purchase_31_365d` (+15) | −0.22pp | indistinguishable from zero |

Three of the four load-bearing factors have no measurable effect and the fourth
has the wrong sign, on the split where the model should look its best.

### The stability guard the plan did not anticipate (Phase 7)

The first fit returned +32% mean excess against a +10% baseline, on weights of
+20.9 and +19.2 for timing factors. That was not a model, it was a clock.
`f_first_purchase_unverifiable` fires on **62.2% of training entries and 2.4% of
validation ones**; `f_first_purchase_12mo` **never fires in training and fires on
45.9% of validation**. Both rates are functions of how far back the database
reaches on a given date. CLAUDE.md already records the same failure in the
original factor, firing on 87% of pre-2025-04 signals against 32% after.

`protocol.stable_features` now drops any candidate whose mean moves more than
half a training standard deviation across a split boundary. Five go: both
first-purchase factors, `tx_ret_63d`, `track_n_prior` and `filing_lag_days`.
**This guard belongs in the plan permanently.** Any feature derived from what the
database can see drifts as coverage accumulates, and no amount of correct
cross-validation saves a model fitted on one coverage regime and deployed into
another.

### The pre-registered test evaluation (Phase 8)

Model B, ridge logistic at alpha=100 on the 33 stable candidates, selected on
validation where it returned +23.83% mean at top 153 against the current score's
+5.13%. On the test split, entries 2026-04-03 to 2026-06-01, top 76 of 762:

| | mean | median | hit | t | decile spread mean / median |
|---|---|---|---|---|---|
| Model B | +16.62% | +9.38% | 73.7% | +3.40 | +4.49pp / **−0.59pp** |
| current score | +12.83% | +9.71% | 75.0% | +3.77 | +4.24pp / +2.68pp |
| all eligible | +8.26% | +6.24% | 65.0% | +5.12 | — |
| small-cap only | +10.40% | +8.31% | 69.6% | +4.25 | — |
| random, 76 | +15.78% | +12.89% | 78.9% | +5.29 | — |

Six pre-registered bars, five passed. It fails **ranks on the median**, which is
the one the whole exercise exists to fix. It also loses to the current score on
median and hit rate, and a random selection of 76 beat both models on median and
hit rate. The validation advantage did not survive.

**Verdict: do not ship.** The correct action on this evidence is to change
nothing about the scoring model.

### Why the answer was "not yet" rather than "never"

The obstacle looked like sample size and coverage. The stable candidate set is
33 features against 3,655 training rows clustered into 819 tickers and 11 months,
inside a single market regime, with the drift guard removing exactly the features
a longer history would make usable.

Half of that was right and half was wrong. Section 7b is what happened when the
evaluation itself was rebuilt on the same data.

## 7b. The ruler was the problem, 2026-08-30

The null result above rested on a test split of 762 rows across three months,
590 of them in a single month whose mean excess return was +10.7%. Every bar it
applied was mostly a measurement of May 2026. Beside it sat a random baseline
drawn once from a fixed seed, which landed at +15.78% against a distribution
whose median is +8.2%, and that single draw was reported as the floor a
challenger had to clear.

A ruler that cannot separate a model from a lucky draw returns a null result
whatever is put in front of it. `src/research/walkforward.py` replaces it, and
`scripts/hillclimb.py` is the one frozen command that reads it.

### What the new ruler does

**Rolls the origin.** Refit every month on every hold that had already closed,
predict that month, move on. 18 predictable months and 6,690 out-of-sample rows
at 90d, against 762 before.

**Judges inside the month.** 97.5% of the variance in excess-vs-SPY is
within-month, but the 2.5% between months is what a pooled top-k harvests by
accident. Tilt the picks toward months that went up and the mean rises with no
ranking skill at all.

**Charges each pick against its own risk.** Ranking purchases by prior 21-day
realised volatility alone scored +11.9pp with t=3.13 on the first version of the
metric. That is leverage on a rising market. Each pick is now charged against the
mean of its own volatility quintile inside its own month.

**Tests the median.** A fat right tail lifts a mean without any pick being
reliably good, which is the gap the previous candidate died in.

**Prices the search itself.** `permutation_alpha` shuffles labels within month
and re-runs the entire walk-forward fit, so every draw pays the same price in
model search that the real run paid.

`tests/test_walkforward.py` is the sensitivity proof and runs before any number
counts. A perfect ranking scores IC 1.0, a planted signal scores +0.2 under a
month effect twice its size, noise stays inside ±0.1, a ranking that knows only
which months went up scores zero, a book where volatility buys return and
nothing else does is cut by 60%, and a lottery book scores positive on the mean
and negative on the median.

### The shape of the signal

Within-month deciles of how far below its 52-week high a stock sat when the
insider bought:

| decile | discount range | mean | median | hit rate |
|---|---|---|---|---|
| 1 to 9 | 0% to 72% | +0.8% to +2.9% | −5.3% to −0.1% | 41% to 49% |
| 10 | 42% to 99% | **+17.5%** | **+6.6%** | **57.7%** |

Nine flat deciles with negative medians, then a jump. It is a threshold, not a
slope, which is why every rank-transformed linear model scores zero: rank IC on
the discount is −0.02, because there is nothing to order across the bulk.

### The result

Out of sample, walk-forward, top decile of each month, charged against its own
volatility quintile inside its own month, 18 months and 6,690 rows at 90d:

| ranking | alpha | t | median | vs chance |
|---|---|---|---|---|
| distance below 52-week high | **+11.13pp** | **+2.29** | **+7.39pp** | p < 1/5000 |
| ridge on all price context | +9.86pp | +2.33 | +4.54pp | p < 1/300 |
| tier-1 insider features | +1.68pp | +0.52 | −0.15pp | p = 0.10 |
| **the shipped score** | **+0.78pp** | **+0.40** | **+0.47pp** | **p = 0.27** |

The shipped score is a coin flip, now measured over eighteen months rather than
three, under the strongest test available.

The winner is one raw feature with no fitted parameters, so its permutation test
reduces exactly to the random-selection null, which is why the p-value is
sharper than the fitted model's.

### Everything that failed to break it

- **Horizon.** +2.60 at 30d, +6.54 at 60d, +9.86 at 90d, +8.99 at 180d, t from
  1.99 to 2.33, median positive at all four.
- **Selectivity.** t = 2.33, 2.45, 2.51 at the top 10%, 20% and 30% of a month.
- **Subperiod.** +11.25 over the first nine months, +8.48 over the last nine.
- **Survivorship.** All 806 rows whose price series has no exit bar are
  unfinished holds, not delistings, and their rate runs 6.1% to 8.2% across the
  ten discount deciles with no gradient. There is nothing in the evaluable set
  to patch to a total loss.
- **Look-ahead.** Price context is dated strictly before the trade; the minimum
  bar count preceding a purchase is 1.
- **One vote per name.** Collapsing to one row per ticker-month leaves +8.10,
  t=+2.35, median +3.86.
- **Ticker concentration.** Cutting the twenty biggest contributors of 182 names
  takes it to −0.10, which looks fatal until random rankings are cut the same way
  and fall from −0.07 to −2.37. Against that matched null the real curve holds
  the 100th percentile through ten drops and the 97th at twenty.
  `amputation_curve` is that control.

### Everything that failed to improve it

Gating at the training 80th, 90th or 95th percentile lands within noise of the
raw feature, so the gate earns nothing. Ranking by the current score inside the
gate drops it to +7.62 and fails t≥2. Ranking by tier-1 insider features inside
the gate drops it to +6.80. Restricting to small caps drops it to +6.08.
Restricting to anything but large caps drops it to +6.94. Adding trend and
liquidity to a fitted model destroys it, +0.45 with a negative median.

**Insider detail actively degrades the price screen.** Inside the most
discounted third, the number of cluster buyers points the wrong way at −4.53,
t=−1.85, against the CLUSTER_BUY thesis the model is built on.

### The placebo control: the Form 4 is doing the work

Distance below the 52-week high is a known equity effect, so the screen had to
be run on stocks nobody bought. `scripts/insider_control.py` takes every real
purchase and draws placebo observations on a different ticker, the same
transaction date, the same exec date and the same horizon. Calendar, month
structure and holding windows are identical; the only difference is the filing.

| top decile of discount | mean | median | hit rate |
|---|---|---|---|
| insider purchases | +11.13pp | **+7.39pp** | **57.7%** |
| placebo, same dates | +5.55pp | **−1.30pp** | 49.3% |

Half the mean is the discount alone. **All of the median is the insider.** A
deeply discounted stock nobody bought is a lottery ticket, its mean carried by a
fat right tail while the typical one loses money at a 49.3% hit rate. A deeply
discounted stock an insider bought has a positive median and wins 57.7% of the
time.

On the mean alone the honest reading would have been "mostly a value effect".
The median says the opposite. This is what the median statistic was added for.

Two limits. The placebo universe is the 1,371 symbols in the price panel, which
are stocks that had an insider purchase somewhere in the window rather than the
whole market, so the contrast is "no Form 4 on this date" and not "no Form 4
ever". And placebos are drawn at random rather than matched on size or sector,
so the calendar and the discount are controlled but the industry mix is not.

### What this claims

Among insider purchases, the ones in deeply beaten-down stocks outperform their
month-and-risk-matched peers out of sample over eighteen months at p below one
in five thousand, and the same screen without the filing has a negative median.

**The Form 4 is the gate and the discount is the ranker.** Insider attributes do
not rank inside the discounted set; the filing itself is most of the effect.

### The cutoff had to be relative, and measuring after shipping is how that was found

The first cut scored a purchase against a fixed CDF built on the whole research
sample, and classified BUY at 90. That is not the rule the research validated.
The research measured the **top decile of each month**; a fixed threshold on a
distribution the whole market moves together does not select a fixed fraction.

It fired on 2.0% of one month's purchases and 23.7% of another's. In the heavy
months it reached well past the top decile into the nine flat ones, where the
median return is negative. On identical rows and months, top decile, risk
matched:

| rule | mean | t | median | t |
|---|---|---|---|---|
| fixed table, `score >= 90` | +4.19pp | +1.17 | **−2.33pp** | −0.56 |
| trailing 30d, `score >= 90` | +9.92pp | +2.24 | +5.77pp | +1.49 |
| top 10% of the month (ceiling) | +11.10pp | +2.28 | +7.38pp | +1.33 |

More than half the effect was being given away, and the median went negative.
The `discount.py` docstring had called this "alert volume moves with the market"
and treated it as a cost worth paying. That framing was wrong: it is not a volume
problem, it is the selection reaching into the part of the distribution that
carries nothing.

Ranking against the purchases disclosed in the preceding 30 days recovers most of
it. It stays point-in-time because the window holds only filings that already
existed.

**Window length was chosen on a mechanism, not on a maximum.** The rule being
approximated is "the top decile of the current cross-section", so there is a test
that does not look at returns at all: what share of each month clears the cutoff?
It should be a tenth.

| window | refs | mean | t | median | t | share of month |
|---|---|---|---|---|---|---|
| 14 | 210 | +13.60 | +2.39 | +7.95 | +1.47 | 0.0% to 12.6% |
| 21 | 306 | +11.37 | +2.51 | +8.75 | +1.68 | 0.0% to 13.7% |
| **30** | **424** | **+11.05** | **+2.61** | **+7.42** | **+1.87** | **0.8% to 15.6%** |
| 45 | 518 | +8.98 | +2.15 | +2.73 | +0.56 | 2.5% to 18.1% |
| 60 | 687 | +8.02 | +2.05 | +1.09 | +0.24 | 2.0% to 20.0% |
| 90 | 1060 | +9.29 | +2.21 | +1.16 | +0.24 | 2.5% to 20.4% |
| 180 | 2073 | +9.29 | +2.08 | +1.93 | +0.42 | 3.0% to 20.4% |
| 400 | 4323 | +7.07 | +1.79 | +3.30 | +0.75 | 1.5% to 22.2% |
| month | — | +11.10 | +2.28 | +7.38 | +1.33 | 9.1% to 10.1% |

The spread narrows monotonically as the window shortens and the returns follow
it, which is a mechanism and an outcome agreeing rather than a maximum picked out
of a sweep. 30 days rather than 14 or 21 because it rests on 424 reference
purchases against their 210 and 306, and because it is the shortest window that
never leaves a month with no signals at all.

An earlier draft of this section argued for 60 days and dismissed the short end
as a spike. That was measured against a reference inflated 1.23x by counting
broker fills as separate purchases, and without the share-of-month column that
shows the trend is monotone.

### It shipped, 2026-08-30

`src/signals/discount.py` is the model, `src/market/context.py` fetches the input
once at ingest, and four columns on `transactions` store it so the live path and
`backfill_signals.py` read the same number. 14,327 purchase rows backfilled,
92.7% of them rankable from the local panel; all signals rescored.

Verified against the real artifact rather than by inspection. `"SHIPPED scorer"`
is a candidate in the hillclimb registry that calls the production module, and on
the frozen ruler it returns **+11.005, t=+2.25, median +7.413** against the
research feature's +11.132. The 0.13 gap is the integer rounding of the
percentile map.

Then the production backtest, which asks a different question. It measures pooled
excess return against SPY rather than within-month risk-matched selection, and the
old model happened to run the same script one day earlier, so the comparison is
clean: same lookback, same prices, same market period, only the model differs.

| Horizon | old avg | new avg | old median | new median | old hit | new hit | old IR | new IR |
|---|---|---|---|---|---|---|---|---|
| 30d | +4.48% | +4.50% | +1.10% | **+1.91%** | 55.0% | 54.6% | 0.57 | 0.61 |
| 60d | +7.04% | +8.90% | +1.10% | **+2.03%** | 52.6% | 52.9% | 0.35 | 0.53 |
| 90d | +7.12% | **+15.29%** | **−0.90%** | **+2.62%** | 48.3% | **52.5%** | 0.32 | 0.52 |
| 180d | +16.57% | **+34.96%** | +0.44% | **+13.84%** | 50.6% | **57.7%** | 0.31 | 0.58 |

Under the old model the typical BUY alert at 90 days lost to SPY. It is now
positive on the median at every horizon, gains 4pp of hit rate at 90d and 7pp at
180d, and roughly doubles the information ratio from 60d out. The 30d hit rate
fell 0.4pp, which is the only metric that moved the wrong way.

Thresholds moved with the scale: BUY 60 to 90, WATCH 45 to 70, cluster average 22
to 80 and max 30 to 85. CLUSTER_BUY dropped from 136 signals to 60, which is the
intended effect of requiring the group to have been buying weakness.

### What shipping it required

`tx_pct_below_52wk_high` is computed from the price panel at the transaction
date. The live path has a Yahoo quote and the backfill path does not, which is
the exact reason section 2 gives for deleting the old 52-week factors: the same
purchase scored up to 12 points apart depending on which entry point saw it, and
they compared against *today's* low rather than the low as of the trade.

So the rule in CLAUDE.md stands, and the way to satisfy it is to move the fetch
rather than to drop the factor. Phase 1B, the point-in-time price context stored
on the transaction row at ingest, was deliberately skipped in the first round
because no model read it. A model reads it now, so it earned its place, and it
went in first: store the context at ingest, backfill it for stored transactions,
then add the factor, then `backfill_signals.py --days 730 --force`, then
`run_backtest.py`. `score_transaction` remains a pure function of stored data.

The open question this leaves is the one the placebo control could not answer.
The control universe is the 1,371 symbols in the price panel, which are stocks
that had an insider purchase somewhere in the window, and the placebos are drawn
at random rather than matched on size or sector. A control matched on both would
say how much of the +5.55pp non-insider mean is industry mix.

### What did ship from the first round

Nothing that changes a score. What landed is the apparatus and five corrections:

- returns measured on dividend-adjusted closes (Phase 1)
- `run_label` on `backtest_runs`, so a re-run cannot destroy its own baseline
- a local price panel proven equivalent to the network path to 0.003pp
- the negative class scored, with `verify_scoring_parity.py` holding the research
  path and the pipeline to 99% agreement
- `_clean_ticker` refusing tickers no price API can resolve

## 8. Risks

**Overfitting is the primary risk and it is already partly realised.** Five rounds of tuning
have been spent on this data. Even a correct protocol applied now inherits contaminated
priors. Mitigation: prefer the simplest model that works, pre-register the test evaluation,
and treat a null result as publishable.

**One regime.** 730 days. Nothing here can distinguish a factor that works from one that
worked since 2024. Mitigation: report performance by calendar half-year and refuse to ship a
factor whose sign flips across sub-periods.

**Thin cells persist.** Adding features to a 9,477-row dataset with heavy ticker clustering
does not make chairman purchases numerous. Enforce the n ≥ 100 floor honestly.

**Yahoo Finance is unofficial.** The whole label depends on it. The price panel makes this
worse in one way — a single bad fetch poisons every experiment — and better in another, since
a cached panel is auditable and reproducible where per-run fetches are not. Snapshot the
panel with a checksum and never silently refetch.

**Neon 0.5GB.** At 105MB now. `scored_purchases` and the price-context columns add a few MB.
Keeping the price panel local is what makes this safe; do not move it into Neon without
re-checking headroom.

**Survivorship in the price panel.** Fetching by current ticker misses renames and
acquisitions. The existing −50% delisting convention is a blunt instrument that the panel will
make easier to examine but does not by itself fix.

## 9. What this plan deliberately does not do

- It does not change any weight, threshold, or classification rule before Phase 7.
- It does not touch ingest, the parser, cluster detection, or the alerting path.
- It does not add a factor the backfill cannot compute. The price-context columns exist
  specifically to preserve that invariant.
- It does not replace `signals`. The dashboard and Telegram contract is unchanged.

## 10. References

- Lakonishok & Lee (2001), *Are Insider Trades Informative?*
- Cohen, Malloy & Pomorski (2012), [*Decoding Inside Information*](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.2012.01740.x), Journal of Finance 67(3)
- Jeng, Metrick & Zeckhauser (2003), *Estimating the Returns to Insider Trading*
- Cline, Gokkaya & Liu (2017), [*The Persistence of Opportunistic Insider Trading*](https://onlinelibrary.wiley.com/doi/10.1111/fima.12177), Financial Management
- [*Insider Purchase Signals in Microcap Equities: Gradient Boosting Detection of Abnormal Returns*](https://arxiv.org/html/2602.06198) (arXiv 2602.06198) — preprint, not peer reviewed; treat the feature-importance figures as indicative
- [FINRA Equity Short Interest](https://www.finra.org/finra-data/browse-catalog/equity-short-interest)
