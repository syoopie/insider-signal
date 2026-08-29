"""
Signal-classification thresholds — the score cutoffs that turn a number into a
BUY / WATCH / CLUSTER_BUY label.

Changing any value here invalidates every signal already in the database. The
golden rule applies: rerun `scripts/ops/backfill_signals.py --days 730 --force`
then `scripts/pipeline/run_backtest.py`.

Cluster *detection* parameters (window length, minimum insiders, minimum value)
live in `cluster.py` — they define what a cluster is, not how a score maps to a
label.
"""

# A non-cluster signal scoring at or above this is a BUY; at or above
# WATCH_SCORE (but below this) it is a WATCH; below WATCH_SCORE it is LOW.
BUY_SCORE = 60
WATCH_SCORE = 45

# A cluster qualifies as CLUSTER_BUY when the mean participant score is at least
# CLUSTER_MIN_AVG_SCORE and either the window is tight or one participant scored
# at least CLUSTER_MIN_MAX_SCORE. Otherwise it is surfaced as WATCH.
CLUSTER_MIN_AVG_SCORE = 22
CLUSTER_MIN_MAX_SCORE = 30
