# Findings

What the measurements say as of 2026-09-13. Each entry names where its run is recorded. The
dated working notes those runs came from are in [`history/`](history/), and nothing there is
current unless this page repeats it.

A finding lives here. A rule lives in [`CLAUDE.md`](../CLAUDE.md). Row counts live in
neither, because `scripts/audit_data.py` prints them on demand.

---

## How to read a number here

There are two rulers. Both are walk-forward with a monthly refit, and both charge each pick
against the other purchases of its own month. "Risk-matched" means against its own volatility
quintile inside that month as well.

- `research/scripts/hillclimb.py` measures a **ranking**: the selection alpha of each month's
  top decile. It resolves about 3.4 to 3.9pp.
- `research/scripts/gates.py` measures an **exclusion**: what dropping a class does to what is
  left. It spends every row instead of a decile, so it resolves 0.2 to 0.8pp.

A result under its resolution is reported as `BELOW RESOLUTION`. That is an unmeasured effect,
not a zero. In the run that established these resolutions, a random ranking scored +2.19pp at
t=+2.15, so the t-bar alone would have passed a coin flip. On a gate, read `dropped` before
`MDE`: the null spread grows with the size of the class being dropped, so a gate's resolution
barely improves with extra months.
([history/beyond-price.md](history/beyond-price.md), sections 0a and 0b.)

`scripts/run_backtest.py` is neither ruler. It reports the product the dashboard shows, pooled
against SPY over 730 days, and it must not be used to choose between models.

---

## The score

**How far below its 52-week high the stock sat on the trade date is the only thing that
ranks.** It is expressed as a percentile of the purchases disclosed in the preceding 30 days.
Over 18 months and 6,690 out-of-sample purchases at 90 days, the top decile returns +11.13pp of
risk-matched selection alpha at t=+2.29, with a median of +7.39pp, above all 5,000 random
rankings. It survives all four horizons, both subperiods, one vote per ticker, a survivorship
patch, a ticker-amputation control, and matching against the issuer's own sector fund, where
its median is higher than against SPY.
([history/scoring-improvement-plan.md](history/scoring-improvement-plan.md), section 7b;
[history/beyond-price.md](history/beyond-price.md), section 0a.)

**It is a threshold, not a slope.** Within-month deciles 1 to 9 are flat, with a negative median
in every one. Decile 10 returns +17.5% mean and +6.6% median at a 57.7% hit rate. The BUY cutoff
at 90 sits on that jump.

**The reference has to be relative.** A fixed cutoff does not select a fixed fraction, because
the market moves every stock's discount together. The first version fired on 2.0% of one
month's purchases and 23.7% of another's, and scored +4.19pp mean and −2.33pp median against
the trailing window's +9.92pp and +5.77pp.

**An unrankable purchase scores zero rather than falling back.** Over 18 months, the four picks
that came from a fixed-table fallback averaged −34.07pp against the ranked picks' +15.59pp.

**The filing is the gate.** The same screen on stocks nobody bought, on the same dates and
holding windows (`research/scripts/insider_control.py`), returns +5.55pp mean, −1.30pp median
and a 49.3% hit rate. Half the mean is the discount alone. All of the median is the Form 4.

**Under the volatility-scaled label every headline shrinks.** The discount screen reads
t=+1.95 there, which is indistinguishable from tier-1 insider features at t=+1.94.
([history/beyond-price.md](history/beyond-price.md), section 0a.)

---

## What does not rank

Every insider attribute tested as a ranking lands between +0.3pp and +3.5pp, under the
ruler's resolution. The specific measurements:

| Hypothesis | Measured |
|---|---|
| The retired additive table: role, cap tier, holdings increase, timing | +0.78pp, permutation p=0.27 |
| The old score as a tiebreak inside the discount gate | lowers +11.13pp to +7.62pp |
| Tier-1 insider features inside the gate | lowers it to +6.80pp |
| `cap_small`, once weighted +15 on Lakonishok & Lee | −4.75pp per standard deviation, the opposite sign |
| `role_director`, once the largest role weight | −0.31pp per standard deviation |
| `holdings_increase_5pct` | +0.61pp per standard deviation |
| Cluster buyer count inside the most discounted third | −4.53pp at t=−1.85, the wrong way |

These attributes are still recorded on every buyer in `evidence.insiders[]`. They are not in
the score.

---

## Exclusions

Measured with `gates.py` on the database (22 months) and on the archive (124 months).

| Gate | Result |
|---|---|
| Firm not net selling | +0.07pp against a resolution of 0.40, on the database |
| Filed within 30 days | +0.03pp against 0.20, on the database |
| Not averaging down | +0.09pp against 0.62, on the database |
| Buyers are 25% of the insider roster | +1.02pp against 0.60 on the archive, 100th percentile under both the SPY and volatility labels, q=0.41. The strongest open lead |
| An officer, not a plain director | the wrong way under the volatility label, t=−2.02 |
| Not a routine buyer | +0.039pp against 0.146 on the archive, a measured zero. Kept on the literature |
| Not a 10b5-1 plan trade | not yet readable on the archive, which lacks the footnote branch of the production flag |
| Not a large-cap cluster | below resolution, see below |
| Not large cap at all | below resolution, see below |

([history/beyond-price.md](history/beyond-price.md), sections 0a and 0b.)

### The large-cap cluster downgrade, 2026-09-13

Production turns a large-cap CLUSTER_BUY into a WATCH. The hit rate that rule was set on was
measured under the retired factor model, so it was re-measured.

On the database sample, 62 purchases are large-cap members of a cluster of 3 or more buyers.
Nine of them sit in the score ≥ 80 zone, where the downgrade can bind. Their raw 90-day excess
returns are poor, a mean of −22.1pp and a median of −28.8pp against +8.1pp and −2.4pp for the
rest of that zone. Excluding them measures +0.095pp at t=1.59 against a resolution of 0.20pp.
The archive cannot add power, because it has no point-in-time cap tier.

The rule stays, and `research/gates.py` carries it as `not a large-cap cluster`.

### Hiding large caps on the dashboard, 2026-09-13

The dashboard's cap filter leaves large caps out by default. The reason it cited, a 0% hit rate
and −16% average excess return at 90 days, was measured under the retired factor model.

`research/gates.py` now carries `not large cap`. On the database sample it drops 623 of 7,677
eligible purchases and measures +0.013pp on the mean against a resolution of 0.31pp. On the
median it moves the result by −0.02pp from keeping everything. The volatility label agrees on
both. Excluding large caps is unmeasured, not a measured gain.

The two narrower readings point in opposite directions. The 26 large-cap purchases scoring 90 or
more returned a 90-day mean of −9.4pp, a median of −7.1pp and a 31% hit rate, against +14.2pp,
+1.1pp and 50% for small caps. But restricting the discount ranking to purchases that are not
large cap lowers its matched alpha from +12.84pp to +8.46pp (`hillclimb.py`, 2026-09-08). The
cap tier is today's, not the tier at the trade.

---

## Holding period and exits

**90 days is the right hold.** On 4,996 top-decile purchases over 124 months, with months
weighted equally, the edge peaks at 90 days on both significance (monthly t=4.29) and median
(+1.35pp). At 180 days the mean keeps rising while the median turns negative. The dashboard
backtest's −1.68% median at 90 days comes from 386 trades over 22 months and does not survive
the decade.

**Stop-losses make every number worse.** Every level from −10% to −30% lowers the mean, the
median and the hit rate, and a tighter stop is worse than a looser one. The screen buys volatile
stocks near their lows by construction, so a stop books the drawdown just before the recovery
the purchase is betting on.
([history/beyond-price.md](history/beyond-price.md), section 0c.)

---

## The stored data

- **`is_10b51` and `is_routine` were fossils, and were repaired on 2026-09-12.** Both are
  written once at ingest. `scripts/repair_transaction_flags.py` corrected 94 plan flags and
  2,851 routine flags. Stored signals fell from 1,292 to 1,241, and the backtest moved within
  noise. The routine flag can go stale again as `prune_old_data` deletes the history it was
  decided on.
- **Retention is 48 months**, because the routine check needs three years of history to decide
  anything at all.
- **The archive holds filings the database never ingested**, 11.22% of its rows over a
  three-day sample, because those issuers joined the ticker universe after they filed.
  Filtering history by today's universe is survivorship bias.

---

## Open questions

- The roster-share gate clears its resolution under both labels and misses FDR at 5%.
- The 10b5-1 gate cannot be read until the archive implements the footnote branch.
- The large-cap downgrade needs more large-cap cluster rows than the database holds.
- Officers who also sit on the board: the parser reads `isDirector` and `isOfficer`, and
  `write_filing` stores neither. The archive keeps both.
- Position sizing and concentration limits are untested.
