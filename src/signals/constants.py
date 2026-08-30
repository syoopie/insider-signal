"""
Signal-classification thresholds — the score cutoffs that turn a number into a
BUY / WATCH / CLUSTER_BUY label.

Changing any value here invalidates every signal already in the database. The
golden rule applies: rerun `scripts/backfill_signals.py --days 730 --force`
then `scripts/run_backtest.py`.

Cluster *detection* parameters (window length, minimum insiders, minimum value)
live in `cluster.py` — they define what a cluster is, not how a score maps to a
label.
"""

# A non-cluster signal scoring at or above this is a BUY; at or above
# WATCH_SCORE (but below this) it is a WATCH; below WATCH_SCORE it is LOW.
#
# The score is now a percentile of how far below its 52-week high the stock sat
# when the insider bought, so 90 is the top decile and 70 the top three. The
# top decile is where the whole measured effect lives: within-month deciles 1
# through 9 return between -0.8% and +2.9% with a negative median in every one,
# and the tenth returns +17.5% mean and +6.6% median. See
# `docs/scoring-improvement-plan.md` section 7b.
#
# The previous cutoffs were 60 and 45 against a score whose theoretical maximum
# was 61, so BUY was a four-factor conjunction rather than a rank. That score
# measured +0.78pp of selection alpha with a permutation p of 0.27.
BUY_SCORE = 90
WATCH_SCORE = 70

# A cluster qualifies as CLUSTER_BUY when the mean participant score is at least
# CLUSTER_MIN_AVG_SCORE and either the window is tight or one participant scored
# at least CLUSTER_MIN_MAX_SCORE. Otherwise it is surfaced as WATCH.
#
# Both raised onto the new scale, and deliberately not to the BUY cutoff. Three
# insiders buying the same beaten-down name inside a fortnight is worth
# surfacing one decile earlier than a single purchase, which is what the cluster
# literature claims and the only part of it this data does not contradict.
#
# What the data does contradict is promoting a cluster on cluster size alone.
# Inside the most discounted third, the number of cluster buyers points the
# wrong way at -4.53 with t=-1.85, so a cluster in a stock near its 52-week high
# is now a WATCH rather than an alert. It used to be a CLUSTER_BUY.
CLUSTER_MIN_AVG_SCORE = 80
CLUSTER_MIN_MAX_SCORE = 85
