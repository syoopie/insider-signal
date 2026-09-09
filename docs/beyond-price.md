# Beyond Price

The plan for the next scoring iteration.

Written 2026-09-05, executed from 2026-09-08. The successor to
[`scoring-improvement-plan.md`](scoring-improvement-plan.md), which holds the history and
ends where this begins: a working price screen, a trustworthy ruler, and every
insider-derived variable measuring zero.

---

## 0. The verdict

**The search for non-price metrics has not failed. It was underpowered by roughly twice,
and no amount of feature engineering can fix that.**

The frozen ruler resolves about **3.4 to 3.9pp** of selection alpha for a ranking. Every
insider feature tested has landed between +0.3pp and +3.5pp. Those are not null results.
They are results below the instrument's resolution, and they would look identical whether
the true effect were zero or three points.

Seven rounds were therefore asking a question the apparatus could not answer. The move was
not another feature sweep but to buy statistical power along the axes that are free, then
re-run the *existing* candidate set before adding anything new.

**Phase A is done, 2026-09-08 and 09-09.** Section 0a is what it found. Two of those
findings correct this document, and one changes the priority order.

> The paragraph above used to claim the resolution was 5pp for an insider candidate and
> 13pp for a price one. **That was wrong**, and A1 was the item that proved it: those
> figures came from dividing each candidate's alpha by its t, which is the spread of its
> own observed monthly series and carries the effect's month-to-month variation as well as
> the noise. The permutation null is the right denominator. The argument survives and
> tightens; the numbers were out by a factor of three. Section 2.1 still carries the
> original table, marked.

---

## 0a. What the build found, 2026-09-08

### The resolution is about 3pp, not 5 to 14, and section 2.1 was wrong

`minimum_detectable_effect` in `src/research/walkforward.py` permutes labels inside each
month, refits the whole walk-forward, and reports 2.80 times the spread of the result.
`scripts/hillclimb.py --mde` prints it. Measured at 90d:

| candidate | alpha | t | permutation null sd | **MDE at 80% power** | observed sd of alpha/t |
|---|---|---|---|---|---|
| below 52wk high | +12.84 | +2.39 | 1.35 | **3.79** | 5.37 |
| SHIPPED scorer | +12.59 | +2.32 | 1.34 | **3.75** | 5.43 |
| gate p90 + insiders | +8.06 | +2.27 | 1.40 | **3.93** | 3.55 |
| ridge tier1 | +4.47 | +1.77 | 1.21 | **3.40** | 2.53 |
| noise | +2.19 | +2.15 | 1.35 | **3.79** | 1.02 |

Section 2.1 derived 5.3pp and 13.6pp by dividing each candidate's alpha by its t. That is the
spread of its *observed* monthly series, which carries the effect's own month-to-month
variation as well as the noise. The two coincide when there is no effect, and `noise` is the
check: 1.02 observed against a 1.35 null. They diverge when there is one, and the discount
screen is the check on that: 5.37 observed against 1.07 to 1.35.

So the ruler is sharper than this document claimed. The conclusion survives and gets tighter.
Every insider feature ever tested scored between +0.3pp and +3.5pp, and the resolution is
about 3.4 to 3.9pp, so those runs still could not distinguish a real effect from zero. The
underpowering was roughly twofold, not threefold.

`hillclimb.py` now reports such a candidate as `BELOW RESOLUTION (3.8pp)` rather than as a
failure. `noise` scoring +2.19 at t=+2.15 in the same run is why that matters: the t-bar alone
would have passed a coin flip.

### The gate estimand is worth four times the precision, and it was free

`class_alpha` charges a class against the whole month it came from. `scripts/gates.py` races
the exclusions the way `hillclimb.py` races the rankings. Its resolutions:

| gate | kept | dropped | alpha | t | **MDE** | verdict |
|---|---|---|---|---|---|---|
| firm not net selling | 6,681 | 952 | +0.07 | +0.37 | 0.40 | below resolution |
| filed 30+ days late, dropped | 7,455 | 178 | +0.03 | +0.37 | 0.20 | below resolution |
| direct only | 5,145 | 2,488 | +0.64 | +0.98 | 0.80 | below resolution |
| buyers are 25% of the roster | 5,431 | 2,202 | +0.17 | +0.34 | 0.70 | below resolution |
| not averaging down | 5,780 | 1,853 | +0.09 | +0.55 | 0.62 | below resolution |
| an officer, not a plain director | 2,341 | 5,292 | +0.12 | +0.12 | 1.87 | below resolution |
| coin flip, the control | 6,125 | 1,508 | +0.16 | +1.19 | 0.59 | below resolution |
| keep everything, the control | 7,633 | 0 | 0.00 | n/a | 0.00 | exact zero, as it must be |

A gate resolves 0.2 to 0.8pp where a ranking needs 3.4 to 3.9. **These are measured zeros.**
Net insider demand, the item section 6 called "the cheapest large win available", is worth
+0.07pp against a resolution of 0.40. Averaging down, the filter CLAUDE.md blames for the
worst outcome in the history, is worth +0.09 against 0.62. Both are now settled rather than
untested.

### The three shipped disqualifiers, measured on the rows they discard

Never done before, because `evaluable` keeps only what the pipeline already scored.

| rule | kept | dropped | alpha | t | MDE | percentile vs chance |
|---|---|---|---|---|---|---|
| not a 10b5-1 plan trade | 8,562 | 577 | **+0.37** | +1.33 | 0.28 | **100** |
| not a routine buyer | 8,850 | 289 | **−0.19** | −1.34 | 0.16 | **0** |
| at least $2,000 | 8,370 | 769 | −0.22 | −0.87 | 0.33 | 2 |

Excluding 10b5-1 trades earns its place directionally, at the 100th percentile of the null and
above its own resolution, though it does not clear Benjamini-Hochberg at 5% (q=0.28).

**Excluding routine buyers points the wrong way.** It is above its resolution, at the 0th
percentile of the null, with t=−1.34. Nothing here is conclusive at q=0.28, but the sign is
the opposite of what Cohen, Malloy and Pomorski support and the rule is currently deleting
289 purchases on that authority. This is the first rule to re-test when the sample grows.

### Four labels, and the coin flip only reads as a coin flip under one of them

`excess_sector_{h}d` and `excess_vol_{h}d` now exist alongside SPY and IWM;
`--label` selects one on either script.

| candidate | spy | vol | sector | iwm |
|---|---|---|---|---|
| below 52wk high | +12.84, t=2.39 | +0.131, t=1.95 | +12.48, t=2.05 | +12.75, t=2.38 |
| ridge tier1 | +4.47, t=1.77 | +0.163, t=1.94 | +3.03, t=1.45 | +5.41, t=2.13 |
| gate p90 + insiders | +8.06, t=2.27 | +0.074, t=1.54 | +8.28, t=2.32 | +8.38, t=2.45 |
| **noise** | **+2.19, t=2.15** | **−0.008, t=−0.43** | −0.74, t=−0.41 | +2.12, t=2.13 |

Two things fall out. No candidate flips sign across labels, so nothing here is disqualified by
the section 3 rule. And the discount screen survives industry matching with a *higher* median
than against SPY, +11.82 against +10.29, which is the closest thing yet to an answer for the
placebo control's open question: the effect is not an industry tilt.

The uncomfortable one is the last row. Under SPY and IWM a random ranking scores +2.19 with
t=+2.15; under the vol label it scores −0.008 with t=−0.43. Risk-quintile matching does not
fully neutralise a label whose standard deviation runs from 11.8 to 58.4 across those
quintiles, and the residue is large enough for chance to clear the t-bar. **The vol label is
the better-behaved ruler and it shrinks every headline number, including the shipped model's.**
Under it, the price screen at t=+1.95 and tier-1 insider features at t=+1.94 are
indistinguishable from each other. That is worth taking seriously rather than reporting once
and dropping.

### The correction that changes the priority order: the sample cannot grow

The database's earliest filing is **2024-09-03**, exactly 24 months before the day this was
run. `prune_old_data(months=24)` runs inside the daily ingest. Every month, ingest adds one
month at the back and pruning deletes one at the front.

The 2026-08-30 run had 18 predictable months and 6,690 out-of-sample rows. The same command
on 2026-09-08 has **16 predictable months and 6,333 rows**, and it will have about 16 forever.

Section 2.3 called more history "the largest single lever available". It is stronger than
that. The standard error falls as 1/sqrt(months), the sample is pinned at 24 months of
filings by a scheduled job, and **the history that would break the deadlock is being deleted
daily.** A4 is not an accelerator, it is the only way any of this improves, and every day it
is deferred costs a day of the archive it is meant to build.

`scripts/build_form4_archive.py` is written for exactly this: resumable parquet under
`data/form4/`, a manifest so an interrupted build costs one quarter, and nothing in Neon. It
also stores `is_director`, `is_officer` and `is_ten_percent`, which the parser has always read
and `write_filing` has always discarded.

**Done, 2026-09-09.** 901,760 filings and 1,513,233 transactions, 2016-01-04 to 2026-03-31,
119,072 of them open-market purchases against the database's ~13,000. The deadlock this
section describes is no longer the binding constraint.

It got there by abandoning the per-filing fetch. That path was measured at three requests per
historical filing and was rate-limited by EDGAR twenty-two minutes into its first real run,
on a projected nine hours for two years. **SEC DERA publishes the same filings already
parsed**, one zip per quarter, and the whole decade took 41 requests and 50 seconds.
`src/ingest/dera.py` checks equal to EDGAR's daily index on distinct accessions, and
`verify_form4_archive.py` passes against the database on the overlap.

**The rollup gate is closed too.** `src/research/archive.py` presents the parquet as the
three tables `PURCHASE_ROLLUP_SQL` reads and runs that query unmodified in DuckDB, so there
is still one definition of "an insider's purchase on a day" rather than a second pandas one.
**71,929 rolled-up purchases in 1.1 seconds**, against roughly 8,000 in the database over the
same filing window.

`scripts/verify_archive_rollup.py` is the proof, and it took four rounds to make the
comparison mean anything. On the purchases both sides hold, the two engines agree to
**0.0018% on shares and 0.0027% on value**. Three things that looked like archive gaps and
were not:

- **757 keys differ only in which reporting owner was kept.** A joint Form 4 names several
  and both sides keep the first, as `parse_form4` does, but they order them differently.
  BROADWOOD PARTNERS, L.P. on one side is BRADSHER NEAL C on the other. **Insider name is
  not a join key between these two sources.**
- **160 more are index membership measured at two different moments.** CIK 1755953 filed as
  Gryphon Digital Mining (GRYP) in January 2025 and is American Bitcoin Corp (ABTC) in
  `companies` today. The archive filters on the ticker the filing carried, which is the
  point-in-time correct choice; filtering a decade of history by today's index membership is
  survivorship bias.
- **81 are decided by a tie-break the archive cannot reproduce.** Where one insider files two
  accessions on one day for different tranches, the rollup keeps the newest by
  `filed_date DESC, filing_id DESC`, and on a tie that is the Postgres serial, meaning
  insertion order. Arbitrary on the database's side, not the archive's.

What the archive still cannot supply is `is_routine`, `cap_tier` and `pct_below_52wk_high`,
all computed at ingest. They come back NULL, which means **these rows cannot be scored yet**:
`discount_score` returns None without the discount and every purchase would score 0. Joining
the price panel is the next step, and it will also catch the 13 filer data-entry errors the
archive inherits, where a reported price of $5,000,000 a share for KYN is plainly the trade's
total rather than its price.

`scripts/verify_form4_archive.py` is the gate and the pilot passes it. Over 2026-08-04 to
2026-08-06: the archive is missing **0.00%** of comparable stored filings, **0 of 870** shared
filings disagree on transaction count, and purchase value matches the database **exactly**.

**Measured fetch rate: 4m27s for three days of filings**, 2,264 index records down to 1,169
document fetches. That is roughly 25 minutes per calendar month of history, so ten years is on
the order of 50 hours, in line with the estimate in A4 but worth re-timing rather than
trusting, because EDGAR throttles hard under sustained load and a later pilot ran at less than
half this rate.

### One more coverage hole, found by the verifier

Comparing accession sets between the archive and the database failed at first, in both
directions. All 76 database-only filings were tickers that have since left
`data/tickers.txt`; all 99 archive-only ones were tickers in it. The difference is the ticker
universe drifting under two years of ingest runs, not data loss, and the verifier now compares
on today's universe.

The finding is worth more than the fix. **The archive holds 110 filings the database never
ingested, 11.22% of its own rows over three days**, because those issuers entered the universe
after they filed. Every research number ever computed here has been missing them, and the
missingness is not random: it is concentrated in companies that grew into the index, which is
exactly the population a small-cap insider effect would live in.

### The full candidate re-run

16 months, 6,333 out-of-sample rows at 90d, on the rebuilt panel and dataset:

| candidate | matched alpha | t | median | verdict |
|---|---|---|---|---|
| current score | +13.68 | +2.33 | +12.61 | beats chance |
| below 52wk high | +12.84 | +2.39 | +10.29 | beats chance |
| SHIPPED scorer | +12.59 | +2.32 | +10.09 | beats chance |
| gate p90 + insiders | +8.06 | +2.27 | +7.45 | beats chance |
| ridge tier1 | **+4.47** | +1.77 | +3.90 | **above resolution for the first time**, fails t≥2 |
| ridge all | +3.15 | +0.89 | +2.45 | fails t≥2 |
| noise | +2.19 | +2.15 | +0.25 | below resolution |
| ridge current factors | −2.63 | −1.80 | −1.64 | wrong sign |

`ridge tier1` was +1.68 at t=+0.88 on 2026-08-30 and is +4.47 at t=+1.77 now. It has crossed
its own resolution of 3.40 for the first time. **Do not read that as a result.** The dataset
underneath it changed: a fresh panel, a fresh rollup, two fewer months and a different window,
and the whole table moved up with it. What it does establish is that this candidate is no
longer invisible, which is the precondition section 7 requires before the ranker question can
be closed either way.

### What this leaves

Nothing new ships. The scoring model is unchanged, and B1 has retired six free hypotheses on
evidence rather than on silence. The one item that would move the result is A4, and the
pruning finding makes it urgent rather than merely valuable.

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

**Lever 1: more months.** SE falls as `1/sqrt(months)`. The dataset has 16 predictable months.
Going to 64 halves the SE and takes the insider-family MDE from 3.4pp to 1.7pp. EDGAR serves
Form 4 back to 2003. **This is the largest single lever available and it costs nothing but
fetch time.**

It is also the only lever that can ever fire, which section 0a establishes and this section
originally missed. `prune_old_data(months=24)` runs inside the daily ingest, so the database
holds exactly two years and no more. The sample does not slowly accumulate: it slides. 18
predictable months on 2026-08-30, 16 on 2026-09-08, and about 16 in perpetuity. A local
archive is not an optimisation of this lever, it is the lever.

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

## 3. Phase A. Buy power. Done, 2026-09-08 and 09-09.

**All seven items shipped.** The specifications that used to sit here have been cut; what
each one turned out to be worth is in section 0a, which is the part worth reading.

| | What | Where it landed |
|---|---|---|
| A1 | Publish the minimum detectable effect | `walkforward.minimum_detectable_effect`, `hillclimb.py --mde`. **It disproved this document's own section 2.1**, which divided alpha by t and got 5 to 14pp where the permutation null says 3.4 to 3.9 |
| A2 | Change the estimand from ranker to gate | `walkforward.class_alpha`, `scripts/gates.py`, `src/research/gates.py`. Resolves 0.2 to 0.8pp against a ranking's 3.4, because it spends every row rather than a 37-row decile |
| A3 | A homoscedastic second label | `LABEL_FAMILIES` in `src/research/protocol.py`, `--label` on both rulers. Under the vol label `noise` finally reads as noise, t=−0.43 against t=+2.15 on SPY |
| A4 | More history, in a local research archive | `data/form4/`, 901,760 filings and 1,513,233 transactions from 2016. Built from SEC DERA quarterly datasets in 50 seconds after the per-filing fetch was rate-limited |
| A5 | Re-run the existing candidate set at the new power | Done. `ridge tier1` crosses its resolution for the first time, +4.47 at t=+1.77 |
| A6 | Roll the archive up through the shared SQL | `src/research/archive.py` runs `PURCHASE_ROLLUP_SQL` unmodified in DuckDB. 71,929 purchases; the two engines agree to 0.003%. Added after the fact: the archive is inert without it |
| A7 | Price context for the archive rows | `archive.with_price_context()` calls `market.context.context_from_series`, the ingest path's own function, over a panel widened to 2,260 symbols and 2014-12-01. **65,201 of 71,930 purchases (90.6%) carry a 52-week discount**, and on the 8,909 that overlap the database, 8,907 agree with what ingest stored to 0.01pp |

**Phase A exit, met.** The ruler now reports what it can resolve, measures exclusions as
well as rankings, offers a label whose variance does not swamp the effect, and reads a
decade instead of sixteen months. The sample is no longer the binding constraint, which is
the one thing section 2.3 said had to change.

**The scoreable research sample is now 65,201 purchases against the database's ~10,300.**
Two columns are still NULL out of the archive and both are facts about the ingest window
rather than the filing: `cap_tier`, refreshed weekly from EDGAR, and `is_routine`, computed
against whatever history the database held at the time. `is_routine` is the more
interesting of the two, because section 0a found the routine disqualifier pointing the
wrong way and blamed the 24-month window for it. A decade of archive can decide that rule
properly for the first time.

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

**B1.4 Joint officer-and-director role.** ~~Parsed from the raw title string, which is already
stored.~~ **Wrong, and moved to B2. 2026-09-08.** The raw title cannot express it.
`parse_form4` reads `isDirector`, `isOfficer` and `isTenPercentOwner` off
`reportingOwnerRelationship`, `write_filing` stores none of the three, and the parser fills
`officerTitle` with the literal "Director" when there is no title. A CFO on the board and a
CFO who is not are the same stored row. Searching the title for both words matches 11 rows out
of 7,633, which is noise. The nearest computable cut, officers against plain directors, was
run instead: +0.12pp against a 1.87pp resolution. `scripts/build_form4_archive.py` stores all
three flags, so the real cut arrives with A4 at no extra fetch.

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

**The archive fetch was named here as the biggest new failure mode**, on an estimate of
fifty hours of EDGAR requests against an unofficial rate ceiling. That risk is retired and
the estimate was wrong by three orders of magnitude: SEC publishes the same filings as
quarterly datasets and the decade took 41 requests and 50 seconds. What the entry got right
is the mitigation, and it was applied. `verify_form4_archive.py` reproduces the database on
the overlap and `verify_archive_rollup.py` proves the two engines roll purchases up alike.
**Both caught real bugs that would otherwise have shipped silently**, including a debt
filter that let $144 trillion through.

**Survivorship gets materially worse over ten years.** Partly handled: the archive filters
the universe on the ticker each filing actually carried, not today's index membership, so
the point-in-time question is at least asked. A delisted issuer that never rejoined is
still absent.

**Vol-scaling changes the objective, not just the noise.** Measured now, not predicted: the
discount screen runs t=+2.39 against SPY and t=+1.95 under the vol label, where tier-1
insider features sit at t=+1.94. Report both, and never select on whichever is kinder.

**More power finds more spurious effects.** The registry grows and the multiple-comparison
budget grows with it. Benjamini-Hochberg across the whole registry per run, with the false
discovery rate printed, and `permutation_alpha` paid by every fitted candidate.

**Ten years is four regimes, and a factor can work in one.** Report every survivor by
regime and refuse to ship one whose sign flips. Newly possible, and not yet done.

**The apparatus can absorb unlimited effort.** Phase A was meant to take days and did.

---

## 9. Debts this plan names but does not fix

- ~~**`docs/scoring.md` and `docs/research.md` are stale.**~~ **Rewritten 2026-09-08.**
  `scoring.md` now describes the single-factor model and the thresholds that shipped;
  `research.md` splits its citations into what is still load-bearing, what this system
  measured and kept, and what was implemented as a weight and then measured away. The
  entries in the last group are kept rather than deleted so nobody adds them back.
- ~~**The price panel and research dataset are dated 2026-08-30.**~~ **Rebuilt 2026-09-08.**
  The panel covers 1,375 symbols to 2026-09-04 and the dataset holds 10,338 rows. Every
  number in section 0a is on that snapshot. Note that rebuilding *lost* two predictable
  months to pruning, which is the finding in section 0a.
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

| # | Work | Gate | State |
|---|---|---|---|
| A1 | Print the MDE from `hillclimb.py` | Reproduces the claimed resolution | **Done 2026-09-08. It did not: the answer is ~3.8pp, not 5.3 and 13.6, and section 0a corrects this document** |
| A2 | A gate estimand with sensitivity tests | Random mask centres on zero; a gate over the top decile reproduces the ranking metric exactly | **Done 2026-09-08. `class_alpha`, one core with two selectors, 12 new tests** |
| A3 | Vol-scaled and sector-relative labels; `--label` | Every candidate reported at every label; sign flips disqualify | **Done 2026-09-08. No sign flips. `noise` clears t≥2 on SPY and IWM and not on vol** |
| **A4** | **Form 4 archive in parquet, panel extended, survivorship handled** | Reproduces the DB on the overlap within 2%; ≥60 predictable months; insider MDE ≤ 2.5pp | **Written, pilot run, two bugs found and fixed. The long fetch has not been run. Now the only item that can move the result** |
| A5 | Re-run the existing candidate set at the new power | The table is published | **Done 2026-09-08. `ridge tier1` crosses its resolution for the first time at +4.47, t=+1.77** |
| B1 | Six free gate tests, plus the disqualifier validation | Each retires or survives on its own kill criterion | **Done 2026-09-08. All six retired below resolution. The routine-buyer rule points the wrong way** |
| B2 | Table II, all reporting owners, and the three relationship flags | Tier-1 features re-run against corrected insider identity | The archive already stores the flags. Table II and multiple owners still need a parser change |
| B3 | Book-to-market, then earnings proximity, then short interest | Each stored at ingest, both paths agreeing by construction | Not started. Blocked behind A4 on power |
| C | Compose survivors as gates ahead of the discount ranker | Beats the shipped screen on the frozen ruler at the new power | No survivors yet |
| D | Stops, signal exits, sizing, concentration limits | Median and left-tail reported alongside the mean, always | Not started. `p10` and `loss20` exist for it |

**The next gate, and it is not a fetch.** The archive stores raw filings and transactions.
Turning them into research rows needs the purchase rollup, which today is Postgres SQL in
`src/db/purchases.py` and is shared by three call sites precisely so there is one definition.
Writing a pandas rollup for the archive would create the second one, which is the mistake
`purchase_rollup` exists to prevent. Run the same SQL over the parquet instead, in DuckDB,
which supports `DISTINCT ON` and the window function it uses, and prove it by comparing both
paths on the overlap before any number is read off the archive.

Rows A1 through A6 and every B1 item change no stored score. Anything from B3 onward that
touches `src/signals/` triggers the golden rule in CLAUDE.md: `pytest`, then
`backfill_signals.py --days 730 --force`, then `run_backtest.py`.

---

## 11. What this plan deliberately does not do

- It does not propose a new weight, threshold or classification rule. Not one.
- It does not add a feature before the re-run says whether features are visible at all.
- It does not touch the alerting path, the dashboard contract, or the `signals` table.
- It does not move research data into Neon. The price panel precedent holds.
- It does not treat the shipped discount screen as settled science; the re-run includes it.

---

## 12. References

Carried forward from [`scoring-improvement-plan.md`](scoring-improvement-plan.md) section 10.
The two that bear directly on this document:

- Lakonishok & Lee (2001), *Are Insider Trades Informative?* The buy-minus-sell spread that
  B1.1 exists to test, and the value-stock concentration that B3.1 exists to test.
- Cline, Gokkaya & Liu (2017), [*The Persistence of Opportunistic Insider Trading*](https://onlinelibrary.wiley.com/doi/10.1111/fima.12177).
  The per-insider track record, which needs B2.2's identity fix before it can be measured
  honestly.
