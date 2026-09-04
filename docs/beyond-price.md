# Beyond Price

The plan for the next scoring iteration.

Written 2026-09-05. The successor to [`scoring-improvement-plan.md`](scoring-improvement-plan.md),
which should be read first for the history. That document ends where this one begins: with a
working price screen, a trustworthy ruler, and every insider-derived variable measuring zero.

Numbers marked *(measured here)* were computed on 2026-09-05 against
`data/prices/research_dataset.parquet` and `data/prices/hillclimb_results.csv` as they stood
that day. Everything else is quoted from the prior plan and carries its date.

---

## 0. The verdict

**The search for non-price metrics has not failed. It has been underpowered by roughly three
times, and no amount of feature engineering can fix that.**

The frozen ruler resolves a selection alpha of about 5pp for an insider-family candidate and
about 13pp for a price-family one *(measured here)*. Every insider feature tested has landed
between +0.3pp and +3.5pp. Those are not null results. They are results below the instrument's
resolution, and they would look identical whether the true effect were zero or four points.

Seven rounds have therefore been asking a question the apparatus cannot answer. The correct
next move is not another feature sweep. It is to buy statistical power along the three axes
that are free, then re-run the *existing* candidate set before adding anything new. If the
existing candidates still measure zero at three times the resolution, that is a real finding
and section 7 says what to do with it.

---

## 1. The scoreboard

What each round actually established, stripped of narrative.

| round | what was tried | outcome |
|---|---|---|
| 1 to 5 | weight tuning by univariate lift | void. Measured on a sample the model selected, no holdout |
| 6 | rebuild the substrate: price panel, negative class, splits, clustered SEs, FDR | apparatus landed. Model B fitted, then failed its pre-registered test |
| 7a | post-mortem on the null | the test split was 762 rows across three months, 77% of them in one month |
| 7b | rebuild the ruler: walk-forward, month-neutral, risk-matched, permutation-priced | **distance below the 52-week high, +11.13pp, t=+2.29, p<1/5000** |
| 7b | the same screen on stocks nobody bought | placebo median −1.30pp against the real +7.39pp. The filing carries the median |
| 7b | fixed-cutoff to trailing-30d reference | shipped 2026-08-30. Recovered more than half the effect |
| all | every insider-derived variable | between +0.3pp and +3.5pp, none clearing t≥2 |

Two things are established beyond reasonable doubt.

**The Form 4 works as a gate.** The placebo control is the strongest result in the file. Take
the same discount screen, the same dates, the same holding windows, and remove only the
filing: the median goes from +7.39pp to −1.30pp and the hit rate from 57.7% to 49.3%. A
deeply discounted stock nobody bought is a lottery ticket. A deeply discounted stock an
insider bought has a positive median.

**The Form 4 does not work as a ranker.** Role, cap tier, holdings increase, purchase timing,
cluster size, net firm demand, insider track record, averaging down and purchase size relative
to the insider's own history have all been measured. None of them orders the discounted set.
Inside the most discounted third, cluster buyer count points the *wrong* way at −4.53, t=−1.85.

That asymmetry is the whole problem this document exists to attack.

---

## 2. The diagnosis

### 2.1 What the ruler can actually see

`scripts/hillclimb.py` computes, for each of 18 out-of-sample months, the risk-matched
selection alpha of a ranking, then t-tests the 18 monthly numbers. Its pre-registered bar is
t ≥ 2 with a positive median.

Divide each stored result's alpha by its t-statistic and you get that candidate's standard
error across months. From the results file *(measured here, 90d, top 10% of each month)*:

| candidate family | alpha | t | SE across months | effect needed for t≥2 | for 80% power |
|---|---|---|---|---|---|
| discount, ungated | +11.13 | +2.29 | 4.86 | 9.7pp | **13.6pp** |
| discount, gated or capped | +6.08 to +6.94 | +2.37 to +2.68 | 2.57 to 2.59 | 5.2pp | **7.2pp** |
| tier-1 insider features | +1.68 | +0.88 | 1.90 | 3.8pp | **5.3pp** |
| current score factors | +0.65 | +0.31 | 2.08 | 4.2pp | **5.8pp** |
| noise | +0.53 | +0.34 | 1.55 | 3.1pp | 4.3pp |

Read the last column. **To be seen at all, an insider feature must produce about five points
of monthly selection alpha.** Tier 1 produced 1.68. The current factor set produced 0.65.

The shipped discount screen clears its own bar by less than it looks: it needs 13.6pp for 80%
power and delivered 11.1pp. It was detected because it is enormous, not because the instrument
is sharp. A second effect one third that size is invisible by construction.

### 2.2 Every past result is consistent with a real effect we cannot see

This is the part that reframes seven iterations.

`ridge tier1` returned +1.68pp at the 91st percentile of random rankings. That was reported as
a failure. It is equally consistent with a genuine 1.7pp effect: the observed t of 0.88 is
exactly what a true 1.7pp effect against a 1.9pp standard error produces. The experiment does
not distinguish the two hypotheses, and no re-run at the same n ever will.

The same applies to `demand_buy_ratio`, which survived Benjamini-Hochberg at 5% in the Phase 5
multivariate estimation with a +6.08pp coefficient per standard deviation, then measured
nothing in the walk-forward ranking. Those two results are not in conflict. The regression had
3,655 rows and estimated a per-sd slope. The ranking has 18 monthly observations and estimates
a top-decile mean. The second has a fraction of the power of the first, and it is the second
that decides what ships.

**Nothing in the archive licenses the sentence "insider attributes carry no signal".** What
the archive licenses is "no insider attribute carries five points of monthly selection alpha",
which is a much weaker claim and one that most of the published literature would also fail.

### 2.3 Three levers, and what each is worth

Standard error across months scales as `label_noise / (pick_count × sqrt(months))`. Every lever
below is free and they multiply.

**Lever 1: more months.** SE falls as `1/sqrt(months)`. The dataset has 18 predictable months.
Going to 72 halves the SE and takes the insider-family MDE from 5.3pp to 2.7pp. Going to 120
takes it to 2.1pp. The database starts 2024-04-03. EDGAR serves Form 4 back to 2003. **This is
the largest single lever available and it costs nothing but fetch time.**

**Lever 2: a wider estimand.** The current metric spends the whole sample on the top decile,
about 37 rows per month. A *veto* estimand asks whether excluding a class raises the median of
everything retained, and uses all 6,690 rows. That is roughly a threefold SE improvement for
zero new data. It is also the estimand that matches what the evidence says the Form 4 is: a
gate, not a ranker. **This is the cheapest lever and it should go first.**

**Lever 3: a less noisy label.** `excess_spy_90d` has a standard deviation of 35.7pp, and that
noise is wildly heteroscedastic *(measured here)*:

| volatility quintile | n | mean excess | median excess | **sd** |
|---|---|---|---|---|
| 1 (calmest) | 1,635 | −0.09% | −0.70% | **11.8** |
| 2 | 1,620 | +1.50% | −0.92% | 17.6 |
| 3 | 1,625 | +2.29% | −2.34% | 29.8 |
| 4 | 1,620 | +2.92% | −4.63% | 39.9 |
| 5 (wildest) | 1,632 | +10.97% | +1.30% | **58.4** |

The pooled 35.7 is an average of 11.8 and 58.4. A monthly mean built on that is dominated by
whichever picks happened to land in quintile 5. Dividing the label by the trade-date realised
volatility flattens it almost perfectly, to a range of 0.52 to 0.62 across all five quintiles
*(measured here)*.

One honest caveat, measured rather than assumed. On an in-sample version of the statistic the
discount screen scores t=+3.78 on the raw label and t=+2.59 on the vol-scaled one *(measured
here, 24 months, no walk-forward, not comparable to the harness's +2.29)*. Part of the
discount's raw alpha *is* a volatility tilt, and vol-scaling removes it. So this lever is not
free power for the price screen. It is a precondition for the monthly t-statistic to be
well-behaved at all, and it plausibly helps most for features whose effect is not a leverage
tilt, which is every insider feature on the list. Run it as a second label, not a replacement.

---

## 3. Phase A. Buy power. No scoring change.

Nothing in this phase alters a stored score, a threshold, or an alert. All four items are
additive and independently landable.

### A1. Publish the minimum detectable effect

`scripts/hillclimb.py` prints alpha, t and a percentile against random rankings. It does not
print what it can see. Add it.

Inject a synthetic ranking of known strength into the walk-forward loop, sweep the strength,
and report the smallest effect that clears all four pre-registered bars in 80% of draws.
`src/research/walkforward.py` already has the machinery: `permutation_alpha` re-runs the whole
fit under shuffled labels, and `tests/test_walkforward.py` already plants signals of known
size against a month effect. This is a new candidate family plus about forty lines.

Print one line per run: `MDE at 90d, top 10%, 18 months: X.Xpp`. Every future null result then
arrives with the range it was blind to attached, which is the difference between "we measured
zero" and "we could not have seen it".

**Gate.** The printed MDE reproduces the 5.3pp and 13.6pp figures in section 2.1 within 1pp.
If it does not, section 2.1 is wrong and this whole plan needs re-deriving.

### A2. Change the estimand from ranker to gate

Three new metrics in `walkforward.py`, each pre-registered before any feature is run through
them.

**`veto_alpha(scored, mask)`.** Excluding the rows where `mask` is true, what happens to the
mean and median of everything retained, month by month, risk-matched? This is the natural test
for "insiders are net sellers at this firm", "the filing is 30 days late", "the buyer is an
LLC", and every other exclusion candidate. It uses the full sample rather than a decile, so its
standard error is roughly a third of the ranking metric's.

**`tail_alpha(scored, column, rate)`.** Among the picks a ranking already made, does `column`
predict the *left* tail? The strategy's shape is fat in both directions, and the archive
records the same ticker as both the best and the worst 180-day outcome. A feature that cannot
rank the winners but can drop the −60% names is worth more to a portfolio than one that adds a
point of mean. Report the 10th percentile of retained outcomes and the fraction below −20%.

**`gate_lift(scored, mask)`.** For a *promotion* rather than an exclusion, what does the class
return against the rest of its month? This is the estimand the placebo control used and it is
the one the Form 4 itself passes.

**Gate.** Re-run the shipped discount screen through all three. `veto_alpha` on a random mask
must centre on zero, `gate_lift` on the top decile must reproduce the ranking metric's +11.0
within 0.5pp, and the sensitivity tests in `tests/test_walkforward.py` must be extended to
cover the three new statistics before any of them is used on a real candidate.

### A3. A homoscedastic second label

Add `excess_vol_scaled_{h}d = excess_spy_{h}d / tx_vol_21d` to
`scripts/build_research_dataset.py`, and a `--label` flag to `scripts/hillclimb.py`.

Also add the sector-relative label Phase 1D of the prior plan specified and never built. The
dataset carries `excess_spy` and `excess_iwm` and nothing else. `companies.sic_description`
covers 2,042 of 2,142 companies, and the SIC division maps to a liquid sector ETF that the
price panel can hold. Industry is the largest uncontrolled confound left in the placebo
control, which drew its placebos at random and could not say how much of the +5.55pp non-insider
mean was industry mix.

**Gate.** Report every candidate at all three labels. A candidate whose sign flips between raw
and vol-scaled excess is a leverage tilt, not a signal, and is disqualified. Say so in the
verdict block.

### A4. More history, in a local research archive

The largest lever, and the one with a real design decision inside it.

**Target: Form 4 purchases back to 2016-01-01.** Ten years spans the 2018 selloff, the 2020
crash and recovery, the 2022 bear market and the 2023 to 2026 run. The current 730-day window
is one regime, which the prior plan already lists as a top risk and which is why
`protocol.stable_features` had to delete five features whose prevalence tracked how far back
ingest reached rather than anything an insider did. A ten-year window retires that guard for
every feature except the ones genuinely defined against coverage.

**It does not go into Neon.** Transactions occupy 57MB for two years and `form4_filings`
another 30MB. Ten years is roughly 285MB and 150MB against a 500MB free tier that is already
at 105MB. The precedent is already set and already validated: the price panel lives in
`data/prices/panel.parquet` and `scripts/verify_price_panel.py` proved it equivalent to the
network path to 0.003pp. Do the same here.

- `scripts/build_form4_archive.py` writes `data/form4/filings.parquet` and
  `data/form4/transactions.parquet` at the same grain `src/db/purchases.py` produces, reusing
  `src/ingest/edgar.py` and `src/ingest/parser.py` unchanged.
- Neon keeps its two-year window and its two-year pruning. The product does not change.
- `scripts/build_research_dataset.py` grows a `--source {db,archive}` flag. The archive path
  must reproduce the database path on the overlapping window.

**Cost.** Roughly 750k Form 4s at the existing 8 req/sec ceiling, two requests each, is on the
order of 50 hours of wall clock. Run it in chunks with resumable state, the way `bootstrap.py`
already handles windows. The price panel must extend to match, which is 1,371 symbols plus
whatever ten years of history adds, at one request per symbol.

**The survivorship problem gets worse and must be handled, not noted.** Fetching by current
ticker over ten years misses renames, acquisitions and delistings on a scale two years does
not. The existing −50% convention is a blunt instrument. At minimum, resolve tickers through
the EDGAR CIK-to-ticker map *as of the filing date* rather than today, and report the
unresolved rate per year. If the unresolved rate rises materially in the early years, cap the
archive at the first year where it stays flat and say so.

**Gate.** Three checks, all falsifiable.

1. On the 2024-08 to 2026-08 overlap, the archive reproduces the database's eligible purchase
   count within 2% and the shipped screen's selection alpha within 0.5pp.
2. Predictable months rise from 18 to at least 60 at the 90-day horizon.
3. The printed MDE from A1 falls below 2.5pp for the insider family.

If check 3 fails, more history alone was not enough and A2 and A3 have to carry the rest.

### Phase A exit

**Re-run the existing `CANDIDATES` dict unchanged, at the new power, and publish the table.**
No new features. This is the single most important experiment in the document, because it is
the one that separates "insider attributes carry nothing" from "we were never able to see
them". Everything in Phase B is contingent on its result.

---

## 4. Phase B. The non-price metric inventory

Every entry below is specified the same way, and an entry that cannot be specified this way
does not go on the list.

- **Hypothesis.** What effect, in what direction, and why it should exist.
- **Data path.** Where the values come from, and whether both the live and backfill paths can
  compute them. A factor only one path can compute is banned by CLAUDE.md and the ban stands.
- **Estimand.** Ranker, veto, tail filter or gate. Chosen before the run.
- **Kill criterion.** What result retires the idea rather than prompting a re-parameterisation.

Ordered by expected value per unit of effort.

### B1. Free, in the database now, and never tested as gates

Everything here has either never been computed or has only been tested as a ranker, which
section 2.3 argues is the wrong estimand and the weakest test.

**B1.1 Net firm selling as a veto.** 91,296 stored sale rows across 1,831 issuers. Tested once
as `demand_buy_ratio`, a continuous ranker, where it measured nothing. *Hypothesis:* a purchase
at a firm whose other insiders are net sellers over the trailing 90 days is a different animal
from one where they are not, and Lakonishok and Lee's headline result is a buy-minus-sell
spread of which we use one half. *Estimand:* veto. Exclude the bottom quintile of
`demand_buy_ratio` and measure the median of what remains. *Kill:* the retained median does not
rise by 1pp with t ≥ 2 at the Phase A power.

**B1.2 Late disclosure as its own class.** 12,247 of 13,294 purchases file within four days,
which is why filing lag has no exploitable variance as a continuous feature and why
`fast_filing_0_1d` was correctly retired. But 330 file 30 or more days late, up to 3,075 days.
*Hypothesis:* a filing that is a month late is either a compliance failure or a deliberately
buried disclosure, and neither is the same event as a timely one. *Estimand:* gate lift on the
late class, then veto. *Kill:* the late class is indistinguishable from its month at n ≥ 300.

**B1.3 Roster share.** Every filer ever seen for a CIK gives an approximate insider roster.
*Hypothesis:* three buyers out of five insiders is a stronger statement than three out of
forty, and raw cluster count conflates them, which may be why cluster count points the wrong
way inside the discounted set. *Estimand:* ranker on the discounted subset, and veto on the
bottom of the distribution. *Kill:* it does not beat raw `cluster_n_buyers`, which itself
measures −4.53 there.

**B1.4 Joint officer-and-director role.** Parsed from the raw title string, which is already
stored. *Hypothesis:* the literature places the effect in managers rather than large
shareholders, and the current seven-way `role_category` split puts an officer who sits on the
board into one bucket or the other arbitrarily. *Estimand:* gate lift. *Kill:* n < 300 in the
extended archive, or no lift.

**B1.5 Amendments.** A 4/A restates a transaction under a new accession number.
`purchase_rollup()` already picks the newest filing per key, so amendments are handled
correctly for value, and the fact that one was filed is currently discarded. *Hypothesis:* an
amended purchase is a weak signal of a disorganised or opaque filer. *Estimand:* veto. *Kill:*
no effect, which is the likeliest outcome. Cheap enough to test anyway.

**B1.6 Indirect purchases, revisited as a gate.** `is_direct = FALSE` currently costs points
in the retired weight table and excludes a buyer from cluster counting. It has never been
measured as an exclusion on the full sample. *Estimand:* veto. *Kill:* excluding indirect
purchases does not raise the retained median.

**B1.7 The disqualifiers themselves.** `is_10b51`, `is_routine` and the $2,000 floor are gates
that have never been measured *as* gates. They are inherited from Cohen, Malloy and Pomorski
and from common sense, and both are good reasons, but the system now has the apparatus to
check them on its own data. *Estimand:* gate lift on each excluded class, computed on the
purchases the pipeline currently throws away. *Kill:* none. This is a validation, not a
proposal. If a disqualified class turns out to have positive lift, that is a finding worth more
than any feature on this list.

### B2. Free, one parser change each

**B2.1 Table II derivative activity.** Currently ignored entirely. *Hypothesis:* an option
exercise followed by an immediate sale is the opposite signal from an exercise followed by a
hold, and the second is a strong published buy indicator that the system is blind to. It also
supplies a much better routine detector than the same-month heuristic. *Data path:* a parser
change plus a re-bootstrap, which the archive in A4 makes cheap because it is a re-parse of
XML that is being fetched anyway. **Sequence this into the A4 fetch rather than paying the
fetch cost twice.** *Estimand:* veto on exercise-and-sell, gate lift on exercise-and-hold.
*Kill:* neither class separates from its month.

**B2.2 All reporting owners, not just the first.** The parser records only the first
`<reportingOwner>`. Joint Form 4s report one decision under several names, so collapsing them
is right for cluster counting and wrong for everything keyed on a person. Every per-insider
feature, the track record and the averaging-down measure among them, is currently computed
against a name that may be an arbitrary one of several. *Data path:* parser change, additive
column, same re-parse as B2.1. *Estimand:* not a feature. This is a correctness fix that
raises the quality of features that already exist, and it should be measured as a re-run of
B1 and of the tier-1 set rather than as a candidate of its own. *Kill:* not applicable.

### B3. Free, new fetching

**B3.1 Book-to-market from EDGAR XBRL bulk frames.** The single most theory-backed variable
missing from the system. Lakonishok and Lee locate insider predictive power in value stocks,
and the discount screen that works is arguably a crude, noisy proxy for exactly that. *Data
path:* the mechanism `scripts/refresh_market_caps.py` already uses, pointed at
`StockholdersEquity`, and market cap is already stored. One bulk call per concept per quarter.
*Point-in-time discipline is essential and is the whole risk:* use the period actually filed
and available as of the transaction date, never the latest. Store it on the transaction row at
ingest the way `src/market/context.py` stores price context, so both paths agree by
construction. *Estimand:* ranker on the full set, then ranker inside the discount gate. *Kill:*
it does not beat the discount screen it is meant to explain, and does not add to it.

**B3.2 Earnings and 8-K proximity.** Days from the purchase to the nearest 10-Q, 10-K or 8-K,
from the EDGAR submissions API that `src/ingest/edgar.py` already caches. *Hypothesis:* a
purchase in an open window three days after results is a different act from one at a quarter
end, and a purchase immediately before an 8-K is a different act again. *Estimand:* gate lift
per bucket. *Kill:* no bucket separates from its month at n ≥ 300.

**B3.3 Short interest from FINRA.** Bi-monthly, free, with its own symbol mapping and separate
exchange-listed and OTC files. *Hypothesis:* insider buying against heavy short interest is a
recognised setup, and it is a genuinely orthogonal information source rather than another cut
of the Form 4. *Estimand:* ranker inside the discount gate. *Kill:* the mapping loses more than
20% of the universe, or no effect. Ranked below the two above because of the mapping cost and
the bi-monthly grain.

**B3.4 Profitability and leverage, same mechanism as B3.1.** `Revenues`, `NetIncomeLoss`,
`AssetsCurrent`, `LiabilitiesCurrent`. Ranked last within B3 only because book-to-market
carries the theoretical argument and these ride along on the same plumbing once it exists.

### B4. Rejected, with reasons

- **13F institutional ownership.** Quarterly, stale by up to 45 days, heavy parsing. The grain
  cannot resolve a 90-day hold.
- **News and sentiment.** No free source with the coverage and the point-in-time integrity this
  needs. Anything cheap enough to use is look-ahead contaminated, and there is no way to prove
  otherwise after the fact.
- **A hand-picked biotech dummy.** `sic_description` gives the whole partition properly.
- **Any feature derived from what the database can see.** Prevalence tracks ingest coverage,
  not behaviour. `protocol.stable_features` exists for this and the guard stays even after A4
  widens the window.
- **Anything only the live path can compute.** The ban in CLAUDE.md stands. The way to satisfy
  it is to move the fetch to ingest and store the value, which is what `src/market/context.py`
  did for the discount and what B3.1 must do for book-to-market.

---

## 5. Phase C. Model form

**Only if Phase A or B produces a survivor.** Fitting a model over a candidate set that
measures zero is what produced the Phase 7 null and the +32% clock artifact before it.

The shape of the data constrains this more than the model menu does. The discount effect is a
threshold and not a slope: within-month deciles 1 through 9 are flat with negative medians and
decile 10 jumps to +17.5% mean and +6.6% median. Rank IC on the discount is −0.02. Any
rank-transformed linear model spends its capacity on the nine deciles where there is nothing to
order, which is exactly what every fitted candidate in the registry did.

So the default composition is **a gate, then a gate, then the discount as the ranker inside**.
Survivors from B1 and B2 compose as vetoes on the eligible set before the discount percentile
is taken, not as terms added to a score. That preserves the one thing that works, it matches
the estimand each survivor was measured under, and it keeps the dashboard's scoring explainer
truthful.

Reconsider a fitted model only if Phase A's re-run shows the insider family clearing its MDE
as a *ranker*. In that case, ridge logistic on standardised features stays the recommendation
over boosted trees, for the reason the prior plan gives and which more history does not fully
retire: readable coefficients, and a sample that is far less independent than its row count.

---

## 6. Phase D. The axis nobody has touched

Everything above tries to improve *which* purchases get selected. Nothing in seven rounds has
touched *what happens after*, and the evidence says that is where the remaining money is.

The backtest assumes a fixed-horizon, equal-weight, buy-and-hold basket. The strategy's own
archive records the same ticker as both the best and the worst 180-day outcome, +541% and
−91% on different entry dates. Under a fat-tailed threshold effect, exit and sizing rules are
first-order, and the price panel already holds every daily bar needed to test them offline in
seconds.

Four experiments, all cheap, none requiring a new feature or a new fetch.

1. **A stop.** Sweep a fixed and a trailing stop over the panel. Report the effect on median,
   on the fraction below −20%, and on the mean, because a stop that lifts the median while
   cutting the mean is a real tradeoff and not a failure.
2. **Time-based exit versus signal-based.** Does exiting on the first insider *sale* at the
   same issuer beat holding to 90 days? The 91,296 sale rows make this free.
3. **Position sizing.** Equal-weight against volatility-scaled. Section 2.3's quintile table
   says the top volatility quintile carries five times the dispersion of the bottom, so
   equal-weight is implicitly a large bet on the wildest names.
4. **Concentration limits.** Maximum positions per name, per sector, per month. The amputation
   control already showed the result is carried by a modest number of names.

These belong in `src/backtest/engine.py` behind a flag, and their results belong in the same
`hillclimb_results.csv` discipline as everything else: one frozen ruler, hypotheses registered
separately.

**This section is not a consolation prize.** If Phase A's re-run confirms that insider
attributes carry nothing detectable, Phase D becomes the main line of work, and it is the only
line that does not depend on finding a signal that may not exist.

---

## 7. The stopping rule

Pre-registered, because seven rounds without one is how a search becomes a habit.

**Declare the ranker question closed** when all three of the following hold.

1. Phase A lands, and the printed MDE for the insider family is at or below 2.5pp.
2. The full existing `CANDIDATES` set is re-run at that power, under all three estimands and
   all three labels, and no insider-derived candidate clears t ≥ 2 with a positive median.
3. Every B1 item, which is free and requires no fetching, has been run through `veto_alpha`
   and none raises the retained median by 1pp.

On that evidence the honest conclusion is that **the Form 4 is a binary gate and the discount
is the ranker**, which is what the placebo control already says, and the system should be
documented as such. Work then moves permanently to Phase D and to gate quality: better
disqualifiers, better coverage, better data hygiene.

That is a legitimate and publishable outcome. It is also a better product than a score nobody
can defend. What is not legitimate is a ninth round of feature sweeps at an unchanged
resolution.

**Do not stop early.** Failing to clear a bar at 5.3pp resolution is not evidence of absence
and section 2.2 shows why. The stopping rule requires the power to have been bought first.

---

## 8. Risks

**The archive fetch is the biggest new failure mode.** Fifty hours of EDGAR requests against
an unofficial rate ceiling, feeding a parquet file that every downstream number then depends
on. Mitigate the way the price panel was mitigated: resumable chunks, a checksum, a committed
verification script that reproduces the database on the overlap, and no silent refetch.

**Survivorship gets materially worse over ten years** and A4 names it as a gate rather than a
note. If it cannot be handled, a shorter archive with an honest unresolved rate beats a longer
one with an invented one.

**Vol-scaling changes the objective, not just the noise.** Section 2.3 measures the discount
screen losing a point of t under it. Run it as a second label, report both, and never select on
whichever is kinder.

**More power finds more spurious effects.** The candidate registry will grow and the
multiple-comparison budget grows with it. Benjamini-Hochberg across the whole registry per run,
with the false discovery rate printed, and the `permutation_alpha` price for model search paid
by every fitted candidate.

**Ten years is four regimes, and a factor can work in one.** Report every survivor by
regime and refuse to ship one whose sign flips. The current plan cannot do this at all, which
is itself an argument for A4.

**The apparatus can absorb unlimited effort.** Phase A is four items with four gates and it
should take days, not weeks. If it starts growing, cut A3 and A4 to the archive alone and run
the re-run.

---

## 9. Debts this plan names but does not fix

- **`docs/scoring.md` and `docs/research.md` are stale.** Both still describe the retired
  weight table, the 60 and 45 thresholds, and `cap_small = +15`, a factor the archive measures
  with the opposite sign. Agents read these. Fix them before the next round starts, or delete
  them and point at CLAUDE.md.
- **The price panel and research dataset are dated 2026-08-30.** Every number in this document
  and in section 7b of the prior plan rests on that snapshot. Rebuild before running anything.
- **Five tickers are unresolvable** because filers typed them by hand: `(CALX)`, `N O G`,
  `NYSE/TRN`, `BFA, BFB`, `WLY, WLYB`. `audit_data.py` flags them and the rows still need a
  repair.
- **`price_context` columns on `transactions` were partially skipped.** The four columns the
  discount model reads went in. The momentum, volatility and dollar-volume columns from Phase
  1B did not, because no shipped model reads them. B3.1 and Phase D will need some of them, and
  the rule stands that they go in alongside the model that reads them, not before.
- **The placebo universe is not industry-matched.** A3's sector-relative label is the input to
  fixing that, and until it exists the +5.55pp non-insider mean cannot be decomposed.

---

## 10. Sequencing

Each row ends in a falsifiable check. Do not start a row until the one above it passes.

| # | Work | Gate |
|---|---|---|
| A1 | Print the MDE from `hillclimb.py` | Reproduces 5.3pp and 13.6pp within 1pp |
| A2 | `veto_alpha`, `tail_alpha`, `gate_lift`, with sensitivity tests | Random mask centres on zero; `gate_lift` reproduces +11.0 within 0.5pp |
| A3 | Vol-scaled and sector-relative labels; `--label` flag | Every candidate reported at three labels; sign flips disqualify |
| A4 | Form 4 archive to 2016 in parquet, panel extended, survivorship handled | Reproduces the DB on the overlap within 2%; ≥60 predictable months; insider MDE ≤ 2.5pp |
| **A5** | **Re-run the existing candidate set unchanged at the new power** | **The table is published. This decides whether Phase B happens at all** |
| B1 | Six free veto and gate tests, plus the disqualifier validation | Each retires or survives on its own kill criterion |
| B2 | Table II and all-reporting-owners, folded into the A4 re-parse | Tier-1 features re-run against corrected insider identity |
| B3 | Book-to-market, then earnings proximity, then short interest | Each stored at ingest, both paths agreeing by construction |
| C | Compose survivors as gates ahead of the discount ranker | Beats the shipped screen on the frozen ruler at the new power |
| D | Stops, signal exits, sizing, concentration limits | Median and left-tail reported alongside the mean, always |

Rows A1 through A5 and every B1 item change no stored score. Anything from B3 onward that
touches `src/signals/` triggers the golden rule in CLAUDE.md: `pytest`, then
`backfill_signals.py --days 730 --force`, then `run_backtest.py`.

---

## 11. What this plan deliberately does not do

- It does not propose a new weight, threshold or classification rule. Not one.
- It does not add a feature before A5 has said whether features are visible at all.
- It does not touch the alerting path, the dashboard contract, or the `signals` table.
- It does not move research data into Neon. The price panel precedent holds.
- It does not treat the shipped discount screen as settled science. A5 re-runs it too.

---

## 12. References

Carried forward from [`scoring-improvement-plan.md`](scoring-improvement-plan.md) section 10.
The two that bear directly on this document:

- Lakonishok & Lee (2001), *Are Insider Trades Informative?* The buy-minus-sell spread that
  B1.1 exists to test, and the value-stock concentration that B3.1 exists to test.
- Cline, Gokkaya & Liu (2017), [*The Persistence of Opportunistic Insider Trading*](https://onlinelibrary.wiley.com/doi/10.1111/fima.12177).
  The per-insider track record, which needs B2.2's identity fix before it can be measured
  honestly.
