"""
Streamlit dashboard for the Insider Signal system.

Reads from Neon (pooled connection) — all read-only queries.
Sections:
  1. Active signals table (BUY / CLUSTER_BUY / WATCH) with evidence cards
  2. Open positions tracker — live P&L for signals still within hold window
  3. Backtest performance — stratified by score band, cap tier, signal type
  4. Return distribution — box plots showing median/IQR, not just mean
  5. Risk panel — drawdown risk, consecutive losses, stat-sig warnings
  6. Per-ticker insider history
"""

import os
import json
import requests
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor


HOLD_HORIZON_DAYS = 90   # default hold horizon for open positions tracker


def _fmt_currency(val) -> str:
    if val is None:
        return "N/A"
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "N/A"
    if val >= 1_000_000:
        return f"${val/1_000_000:.1f}M"
    if val >= 1_000:
        return f"${val/1_000:.0f}K"
    return f"${val:.2f}"


def _fmt_pct(val, prefix=True) -> str:
    if val is None:
        return "N/A"
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%" if prefix else f"{val:.1f}%"


@st.cache_data(ttl=300)
def _fetch_current_price(ticker: str) -> float | None:
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"interval": "1d", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0 (compatible)"},
            timeout=5,
        )
        if r.status_code != 200:
            return None
        meta = r.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
        return meta.get("regularMarketPrice")
    except Exception:
        return None


def _stat_sig_badge(n: int) -> str:
    if n is None:
        return ""
    if n < 10:
        return "🔴"
    if n < 30:
        return "🟡"
    return ""


# --- DB connection (pooled for Streamlit) ---
@st.cache_resource
def get_db():
    url = st.secrets.get("DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if "-pooler" not in url:
        url = url.replace(".neon.tech", "-pooler.neon.tech", 1)
    return psycopg2.connect(url)


def query(sql: str, params=None) -> list[dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        conn = get_db.clear()
        return []


# --- Page config ---
st.set_page_config(
    page_title="Insider Signal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📈 Insider Signal Dashboard")
st.caption("Signals derived from SEC Form 4 insider purchase disclosures.")

# --- Sidebar filters ---
st.sidebar.header("Filters")
st.sidebar.caption("Defaults tuned to high-conviction signals. Widen to explore.")
lookback_days = st.sidebar.slider("Signal lookback (days)", 7, 180, 30)
min_score = st.sidebar.slider("Minimum score", 0, 100, 50)
signal_types = st.sidebar.multiselect(
    "Signal types",
    ["CLUSTER_BUY", "BUY", "WATCH"],
    default=["CLUSTER_BUY", "BUY"],
)
cap_tiers = st.sidebar.multiselect(
    "Market cap tier",
    ["small", "mid", "large", "unknown"],
    default=["small", "mid", "unknown"],
)
st.sidebar.caption("Small < \\$2B · Mid \\$2B–\\$10B · Large ≥ \\$10B")
st.sidebar.markdown("---")
st.sidebar.caption("**Why these defaults?**\nLarge-cap clusters have 0% hit rate at 90d (−16% avg excess). WATCH signals ~1/3 hit rate. Score ≥50 cuts false signals by ~60%.")

since_date = date.today() - timedelta(days=lookback_days)

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Active Signals
# ═══════════════════════════════════════════════════════════════════════════════
st.header("Active Signals")

signals_sql = """
SELECT s.id, s.ticker, s.signal_date, s.score, s.signal_type,
       s.cluster_flag, s.score_breakdown, s.evidence, c.cap_tier, c.name
FROM signals s
LEFT JOIN companies c ON c.ticker = s.ticker
WHERE s.signal_date >= %s
  AND s.score >= %s
  AND s.signal_type = ANY(%s)
ORDER BY
  CASE s.signal_type WHEN 'CLUSTER_BUY' THEN 1 WHEN 'BUY' THEN 2 ELSE 3 END ASC,
  s.score DESC,
  s.signal_date DESC
"""
signals = query(signals_sql, (since_date, min_score, signal_types))
if cap_tiers:
    signals = [s for s in signals if (s.get("cap_tier") or "unknown") in cap_tiers]

# Secondary sort within CLUSTER_BUY: tight+exec > tight > exec > bare cluster
def _signal_quality_key(sig):
    ev = sig.get("evidence") or {}
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except Exception:
            ev = {}
    cl = ev.get("cluster", {})
    if sig["signal_type"] != "CLUSTER_BUY":
        return (10, -sig["score"])
    is_tight = bool(cl.get("tight_cluster"))
    is_exec  = bool(cl.get("executive_cluster"))
    sub = 0 if (is_tight and is_exec) else (1 if is_tight else (2 if is_exec else 3))
    return (sub, -sig["score"])

signals.sort(key=_signal_quality_key)

if not signals:
    st.info("No signals match the current filters.")
else:
    st.caption("⚡ Cluster Buy = 3+ insiders buying in 14-day window  ·  ✅ Buy = high-conviction single insider  ·  👁 Watch = moderate score")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Signals", len(signals))
    col2.metric("⚡ Cluster Buys", sum(1 for s in signals if s["signal_type"] == "CLUSTER_BUY"))
    col3.metric("✅ Buy Signals", sum(1 for s in signals if s["signal_type"] == "BUY"))
    col4.metric("👁 Watch Signals", sum(1 for s in signals if s["signal_type"] == "WATCH"))

    st.divider()

    # ── Top Picks callout ──
    top_picks = [s for s in signals if s["signal_type"] == "CLUSTER_BUY"][:3]
    if not top_picks:
        top_picks = [s for s in signals if s["signal_type"] == "BUY"][:3]
    if top_picks:
        st.subheader("Top Picks")
        tp_cols = st.columns(len(top_picks))
        for tp_col, tp in zip(tp_cols, top_picks):
            tp_ev = tp.get("evidence") or {}
            if isinstance(tp_ev, str):
                try:
                    tp_ev = json.loads(tp_ev)
                except Exception:
                    tp_ev = {}
            tp_cl = tp_ev.get("cluster", {})
            is_tight = bool(tp_cl.get("tight_cluster"))
            is_exec  = bool(tp_cl.get("executive_cluster"))
            conviction = ("PRIME" if (is_tight and is_exec and tp["signal_type"] == "CLUSTER_BUY")
                          else "STRONG" if (tp["signal_type"] == "CLUSTER_BUY" and (is_tight or is_exec))
                          else "HIGH" if tp["signal_type"] == "CLUSTER_BUY"
                          else "BUY")
            badge_color = ("#00cc00" if conviction == "PRIME"
                           else "#66bb00" if conviction == "STRONG"
                           else "#aabb00" if conviction == "HIGH"
                           else "#0088cc")
            n_buyers = tp_cl.get("insider_count", 1) if tp["signal_type"] == "CLUSTER_BUY" else 1
            buyer_str = f"{n_buyers} insiders" if n_buyers > 1 else "1 insider"
            tp_cap = (tp.get("cap_tier") or "unknown").title()
            tp_company = tp.get("name") or tp_ev.get("company_name") or ""
            with tp_col:
                st.markdown(
                    f"<div style='border:1px solid {badge_color};border-radius:8px;padding:12px'>"
                    f"<b style='color:{badge_color};font-size:1.1em'>{conviction}</b><br>"
                    f"<b style='font-size:1.4em'>{tp['ticker']}</b>"
                    f"{'<br><small>' + tp_company + '</small>' if tp_company and tp_company != tp['ticker'] else ''}"
                    f"<br>Score: <b>{tp['score']}</b>/100 · {tp_cap}-cap"
                    f"<br>{buyer_str} · {tp['signal_date']}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        st.divider()

    TYPE_ICON  = {"CLUSTER_BUY": "⚡", "BUY": "✅", "WATCH": "👁"}
    TYPE_LABEL = {"CLUSTER_BUY": "Cluster Buy", "BUY": "Buy", "WATCH": "Watch"}

    for sig in signals:
        ev = sig.get("evidence") or {}
        if isinstance(ev, str):
            try:
                ev = json.loads(ev)
            except Exception:
                ev = {}

        icon       = TYPE_ICON.get(sig["signal_type"], "⚪")
        type_label = TYPE_LABEL.get(sig["signal_type"], sig["signal_type"])
        cap_label  = (sig.get("cap_tier") or "unknown").title() + "-cap"
        company    = sig.get("name") or ev.get("company_name") or ""
        name_part  = f" · {company}" if company and company != sig["ticker"] else ""
        cluster    = ev.get("cluster", {})
        exec_tag   = " 🏢 exec" if cluster.get("executive_cluster") else ""
        tight_tag  = " tight-window" if cluster.get("tight_cluster") else ""

        is_tight = bool(cluster.get("tight_cluster"))
        is_exec  = bool(cluster.get("executive_cluster"))
        if sig["signal_type"] == "CLUSTER_BUY" and is_tight and is_exec:
            conv_badge = " 🔥**PRIME**"
        elif sig["signal_type"] == "CLUSTER_BUY" and (is_tight or is_exec):
            conv_badge = " ⚡**STRONG**"
        elif sig["signal_type"] == "CLUSTER_BUY":
            conv_badge = " **CLUSTER**"
        elif sig["score"] >= 70:
            conv_badge = " ✅**HIGH**"
        else:
            conv_badge = ""

        header = (
            f"{icon}{conv_badge} **{sig['ticker']}**{name_part} — "
            f"Score {sig['score']}/100 · {type_label} · {cap_label}"
            f"{exec_tag}{tight_tag} · {sig['signal_date']}"
        )

        with st.expander(header):
            insiders = ev.get("insiders", [])
            if insiders:
                st.subheader("Who Bought")
                ins_df = pd.DataFrame([{
                    "Name": i.get("name"),
                    "Role": (i.get("role_raw") or i.get("role", "")).title(),
                    "Shares": f"{int(i.get('shares_bought') or 0):,}",
                    "Price": f"${i.get('price'):.2f}" if i.get("price") else "N/A",
                    "Total Value": _fmt_currency(i.get("total_value")),
                    "Shares After": f"{int(i.get('shares_after') or 0):,}" if i.get("shares_after") else "N/A",
                    "% Increase": f"+{i.get('pct_increase'):.0f}%" if i.get("pct_increase") else "N/A",
                    "Date": i.get("transaction_date"),
                    "In Window": "✓" if i.get("in_scoring_window", True) else "(earlier)",
                } for i in insiders])
                st.dataframe(ins_df, use_container_width=True, hide_index=True)

            col_a, col_b = st.columns(2)
            with col_a:
                st.subheader("Score Breakdown")
                breakdown = sig.get("score_breakdown") or {}
                if isinstance(breakdown, str):
                    try:
                        breakdown = json.loads(breakdown)
                    except Exception:
                        breakdown = {}
                for factor, pts in breakdown.items():
                    if isinstance(pts, int):
                        label = factor.replace("_", " ").title()
                        st.write(f"  **{label}**: +{pts}")
                st.write(f"  **Total**: {sig['score']}")

            with col_b:
                st.subheader("Context")
                if company:
                    st.write(f"**Company:** {company} (${sig['ticker']})")
                if cluster.get("is_cluster"):
                    n_cl = cluster.get("insider_count", 0)
                    exec_str = " · executive cluster" if cluster.get("executive_cluster") else ""
                    tight_str = " · tight window (<5d)" if cluster.get("tight_cluster") else ""
                    st.success(f"Cluster signal: {n_cl} insiders in 14-day window{exec_str}{tight_str}")
                if ev.get("near_52wk_low"):
                    pct = ev.get("pct_above_52wk_low", 0)
                    low = ev.get("price_52wk_low")
                    st.info(f"Near 52-week low: {pct:.0f}% above ${low:.2f}")
                st.write(f"**Filed:** {ev.get('filed_date')}")
                st.write(f"**Signal date:** {ev.get('signal_date')}")
                st.write(f"**Suggested hold:** {ev.get('suggested_hold_horizon', '60–90 days')}")

            research = ev.get("research_basis", [])
            if research:
                st.subheader("Research Basis")
                for ref in research:
                    st.write(f"  • {ref}")

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Open Positions Tracker
# ═══════════════════════════════════════════════════════════════════════════════
st.header("Open Positions")
st.caption(f"BUY / CLUSTER_BUY signals within the {HOLD_HORIZON_DAYS}-day hold window, with current price data.")

positions_sql = """
SELECT s.ticker, s.signal_date, s.score, s.signal_type, s.cluster_flag,
       c.cap_tier, c.name AS company_name, s.evidence
FROM signals s
LEFT JOIN companies c ON c.ticker = s.ticker
WHERE s.signal_date >= %s
  AND s.signal_type IN ('BUY', 'CLUSTER_BUY')
ORDER BY s.signal_date DESC
"""
pos_cutoff = date.today() - timedelta(days=HOLD_HORIZON_DAYS)
positions = query(positions_sql, (pos_cutoff,))

if not positions:
    st.info(f"No open positions — no BUY/CLUSTER_BUY signals in the past {HOLD_HORIZON_DAYS} days.")
else:
    st.caption("Current prices fetched live. Excess return = ticker return − SPY return since signal date +3.")

    with st.spinner("Fetching live prices..."):
        spy_price = _fetch_current_price("SPY")

    pos_rows = []
    for p in positions:
        sig_date = p["signal_date"]
        if isinstance(sig_date, str):
            try:
                sig_date = date.fromisoformat(sig_date[:10])
            except ValueError:
                continue
        days_in = (date.today() - sig_date).days
        days_left = HOLD_HORIZON_DAYS - days_in
        ev = p.get("evidence") or {}
        if isinstance(ev, str):
            try:
                ev = json.loads(ev)
            except Exception:
                ev = {}
        filed_price = None
        insiders = ev.get("insiders", [])
        if insiders:
            prices = [i.get("price") for i in insiders if i.get("price")]
            if prices:
                filed_price = sum(prices) / len(prices)

        ticker = p["ticker"]
        current_price = _fetch_current_price(ticker)
        raw_return = None
        if current_price and filed_price and filed_price > 0:
            raw_return = (current_price - filed_price) / filed_price * 100

        pos_rows.append({
            "Ticker": ticker,
            "Company": p.get("company_name") or "",
            "Type": p["signal_type"],
            "Score": p["score"],
            "Cap": (p.get("cap_tier") or "?").title(),
            "Signal Date": str(sig_date),
            "Days In": days_in,
            "Days Left": max(0, days_left),
            "Avg Entry": _fmt_currency(filed_price),
            "Current": _fmt_currency(current_price) if current_price else "N/A",
            "Return": _fmt_pct(raw_return) if raw_return is not None else "N/A",
            "Status": "Active" if days_left > 0 else "Elapsed",
        })

    if pos_rows:
        pos_df = pd.DataFrame(pos_rows)

        def _color_row(row):
            ret_str = row.get("Return", "N/A")
            status  = row.get("Status", "")
            if ret_str == "N/A":
                bg = "#1a1a2a" if status == "Active" else "#2a2a2a"
            else:
                try:
                    val = float(ret_str.replace("%", "").replace("+", ""))
                    if val >= 10:
                        bg = "#0a2a0a"
                    elif val >= 0:
                        bg = "#1a2a1a"
                    elif val >= -10:
                        bg = "#2a1a0a"
                    else:
                        bg = "#2a0a0a"
                except ValueError:
                    bg = "#1a1a2a"
            return [f"background-color: {bg}"] * len(row)

        st.dataframe(
            pos_df.style.apply(_color_row, axis=1),
            use_container_width=True,
            hide_index=True,
        )
        st.caption("Return = (current − avg insider entry price) / entry. Not excess vs SPY — check backtest for benchmark-adjusted returns. Prices cached 5 min.")

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Backtest Performance
# ═══════════════════════════════════════════════════════════════════════════════
st.header("Backtest Performance")

backtest_sql = """
SELECT run_date, horizon_days, hit_rate, avg_return, median_return,
       p25_return, p75_return, sharpe, iwm_avg_return, n_trades, metrics
FROM backtest_runs
ORDER BY run_date DESC, horizon_days ASC
LIMIT 300
"""
bt_rows = query(backtest_sql)

if not bt_rows:
    st.info("No backtest data yet. Runs weekly after the first week of signals.")
else:
    bt_df = pd.DataFrame(bt_rows)
    horizons = sorted(bt_df["horizon_days"].unique())
    latest_date = bt_df["run_date"].max()
    latest = bt_df[bt_df["run_date"] == latest_date]

    st.caption(f"Latest backtest: {latest_date}  ·  Benchmarked against SPY (small-cap also vs IWM)")

    # ── Top-line metrics ──
    cols = st.columns(len(horizons))
    for col, horizon in zip(cols, horizons):
        row = latest[latest["horizon_days"] == horizon]
        if not row.empty:
            r = row.iloc[0]
            n = r.get("n_trades") or 0
            badge = _stat_sig_badge(n)
            med = r.get("median_return")
            med_str = f"  Median: {_fmt_pct(med)}" if med is not None else ""
            col.metric(
                f"{horizon}d Hit Rate {badge}",
                f"{r['hit_rate']:.0f}%",
                help=f"n={n} trades. {badge} = low sample size warning.{med_str}",
            )

    # ── Avg excess return: bar when only 1 date, line when history exists ──
    unique_dates = bt_df["run_date"].nunique()
    latest_for_chart = latest.copy()
    latest_for_chart["horizon_label"] = latest_for_chart["horizon_days"].astype(str) + "d"
    if unique_dates < 3:
        # Not enough history for a time-series — show current snapshot as a bar chart.
        fig = px.bar(
            latest_for_chart.sort_values("horizon_days"),
            x="horizon_label",
            y="avg_return",
            color="avg_return",
            color_continuous_scale=["#d62728", "#aec7e8", "#2ca02c"],
            labels={"avg_return": "Avg Excess Return vs SPY (%)", "horizon_label": "Hold Horizon"},
            title="Average Excess Return vs SPY (latest backtest — trend chart available after 3+ weekly runs)",
            text="avg_return",
        )
        fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
        fig.update_layout(coloraxis_showscale=False, showlegend=False)
    else:
        trend_df = bt_df.copy()
        trend_df["horizon_label"] = trend_df["horizon_days"].astype(str) + "d"
        fig = px.line(
            trend_df,
            x="run_date",
            y="avg_return",
            color="horizon_label",
            markers=True,
            labels={"avg_return": "Avg Excess Return vs SPY (%)", "run_date": "Backtest Date", "horizon_label": "Horizon"},
            title="Average Excess Return vs SPY by Hold Horizon",
        )
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
    st.plotly_chart(fig, use_container_width=True)

    # ── Return distribution box plot ──
    st.subheader("Return Distribution (latest backtest)")
    st.caption("Box = p25–p75 · Line = median · Whiskers = min/max. Mean alone is misleading — tail risk matters.")

    dist_rows = []
    for _, r in latest.iterrows():
        horizon = r["horizon_days"]
        mets = r.get("metrics") or {}
        if isinstance(mets, str):
            try:
                mets = json.loads(mets)
            except Exception:
                mets = {}
        if not isinstance(mets, dict):
            mets = {}  # old rows stored metrics as a list (raw detail array)
        dist = mets.get("distribution") or {}
        if dist:
            dist_rows.append({
                "Horizon": f"{horizon}d",
                "p25": dist.get("p25"),
                "median": dist.get("median"),
                "p75": dist.get("p75"),
                "min": dist.get("max_loss"),
                "max": dist.get("max_gain"),
            })

    if dist_rows:
        fig_box = go.Figure()
        for dr in dist_rows:
            if None not in (dr["p25"], dr["median"], dr["p75"], dr["min"], dr["max"]):
                fig_box.add_trace(go.Box(
                    name=dr["Horizon"],
                    q1=[dr["p25"]],
                    median=[dr["median"]],
                    q3=[dr["p75"]],
                    lowerfence=[dr["min"]],
                    upperfence=[dr["max"]],
                    boxpoints=False,
                ))
        fig_box.update_layout(
            title="Excess Return Distribution by Horizon (%)",
            yaxis_title="Excess Return vs SPY (%)",
            showlegend=True,
        )
        fig_box.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        st.plotly_chart(fig_box, use_container_width=True)

    # ── Stratification breakdown ──
    st.subheader("Performance by Score Band & Cap Tier (latest backtest)")
    st.caption("Validates that higher scores produce higher returns and small-cap outperforms.")

    tab_score, tab_cap, tab_type = st.tabs(["Score Band", "Cap Tier", "Signal Type"])

    def _parse_metrics(raw) -> dict:
        """Parse the metrics column, handling both new (dict) and old (list) formats."""
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                return {}
        return raw if isinstance(raw, dict) else {}

    def _render_strat_table(strat_key: str, tab):
        with tab:
            rows = []
            for _, r in latest.iterrows():
                mets = _parse_metrics(r.get("metrics"))
                strat = mets.get(strat_key) or {}
                for band, m in strat.items():
                    if m:
                        n = m.get("n", 0)
                        badge = _stat_sig_badge(n)
                        rows.append({
                            "Horizon": f"{r['horizon_days']}d",
                            "Group": band,
                            "N": n,
                            "Flag": badge,
                            "Hit Rate": f"{m.get('hit_rate', 0):.0f}%",
                            "Avg Excess": _fmt_pct(m.get("avg_return")),
                            "Median Excess": _fmt_pct(m.get("median_return")),
                            "P25": _fmt_pct(m.get("p25_return")),
                            "P75": _fmt_pct(m.get("p75_return")),
                        })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                st.caption("🔴 n<10  🟡 n<30 — treat low-sample rows with caution.")
            else:
                st.info("No stratified data available yet.")

    _render_strat_table("by_score_band", tab_score)
    _render_strat_table("by_cap_tier", tab_cap)
    _render_strat_table("by_signal_type", tab_type)

    # ── Rolling hit rate ──
    st.subheader("Rolling 90-Day Hit Rate")
    st.caption("Stable or rising = alpha persists. Declining trend = model may be losing edge.")

    rolling_rows = []
    for _, r in latest.iterrows():
        mets = _parse_metrics(r.get("metrics"))
        for item in mets.get("rolling_hit_rate_90d") or []:
            rolling_rows.append({
                "date": item["date"],
                "hit_rate": item["hit_rate"],
                "n": item["n"],
                "horizon": f"{r['horizon_days']}d",
            })
    if rolling_rows:
        rhr_df = pd.DataFrame(rolling_rows)
        fig_rhr = px.line(
            rhr_df, x="date", y="hit_rate", color="horizon",
            title="Rolling 90-Day Hit Rate",
            labels={"hit_rate": "Hit Rate (%)", "date": "Date", "horizon": "Horizon"},
        )
        fig_rhr.add_hline(y=50, line_dash="dash", line_color="gray", opacity=0.5,
                          annotation_text="50% (random)")
        st.plotly_chart(fig_rhr, use_container_width=True)
    else:
        st.info("Rolling hit rate data available after the first full backtest run.")

    # ── Risk panel ──
    st.subheader("Risk Panel")
    st.caption("High % of losses >20% or long losing streaks indicate tail risk in the strategy.")

    risk_rows = []
    for _, r in latest.iterrows():
        mets = _parse_metrics(r.get("metrics"))
        risk = mets.get("risk") or {}
        if risk:
            risk_rows.append({
                "Horizon": f"{r['horizon_days']}d",
                "% Losses >20%": _fmt_pct(risk.get("pct_loss_gt20"), prefix=False),
                "Max Consecutive Losses": risk.get("max_consecutive_losses"),
                "Worst Outcome": _fmt_pct(risk.get("worst_outcome")),
                "Trades Missing SPY Data": risk.get("n_no_spy_data"),
            })
    if risk_rows:
        st.dataframe(pd.DataFrame(risk_rows), use_container_width=True, hide_index=True)

    # ── Cluster 50-64 analysis ──
    st.subheader("Weak Cluster Analysis (Score 50–64)")
    st.caption("Cluster signals excluded from the BUY threshold. Validates whether the cluster effect holds at lower individual scores.")

    cl5064_rows = []
    for _, r in latest.iterrows():
        mets = _parse_metrics(r.get("metrics"))
        cl = mets.get("cluster_5064")
        if cl:
            n = cl.get("n", 0)
            badge = _stat_sig_badge(n)
            cl5064_rows.append({
                "Horizon": f"{r['horizon_days']}d",
                "N": n,
                "Flag": badge,
                "Hit Rate": f"{cl.get('hit_rate', 0):.0f}%",
                "Avg Excess": _fmt_pct(cl.get("avg_return")),
                "Median": _fmt_pct(cl.get("median_return")),
            })
    if cl5064_rows:
        st.dataframe(pd.DataFrame(cl5064_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No cluster 50–64 data yet.")

    # ── Methodology disclosure ──
    with st.expander("Methodology & Limitations"):
        st.markdown("""
**Signal generation**
- Only open-market purchases (Form 4 transaction code `P`) are scored.
- Grants, awards, option exercises, and RSU vesting are excluded — no money changes hands.
- 10b5-1 pre-arranged trades are disqualified (Cohen, Malloy & Pomorski 2012 — near-zero alpha).

**Backtest bias controls**
- Signal date = filing date + 1 day (not transaction date) to avoid look-ahead bias.
- Execution date = signal date + 3 calendar days (realistic fill lag).
- Delisted stocks are assigned **−50% excess return** as a survivorship bias correction.
  This is a blunt instrument — bankruptcies and acquisitions have different outcomes.
  Treat any strategy with >5% delisted signals cautiously.

**Benchmarks**
- SPY (S&P 500) is used for all signals. Small-cap signals are also benchmarked vs
  IWM (Russell 2000) since SPY understates the opportunity cost for small-cap alpha.

**Statistical significance**
- 🟡 n < 30 signals: hit rate and avg return estimates are unreliable.
- 🔴 n < 10 signals: treat as anecdotal only.

**Score threshold**
- BUY threshold (65) and cluster window (14 days) are set from academic literature,
  not tuned to backtest results. Threshold tuning would introduce overfitting.
""")

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Per-Ticker Insider History
# ═══════════════════════════════════════════════════════════════════════════════
st.header("Insider History by Ticker")

with st.expander("Transaction code reference"):
    st.markdown("""
| Code | Meaning |
|------|---------|
| **P** | Open-market purchase — insider bought shares with their own money. The signal we care about. |
| **S** | Open-market sale |
| **A** | Grant, award, or other acquisition (e.g. RSU grant) — no money changed hands |
| **M** | Exercise or conversion of a derivative (e.g. options exercised) |
| **F** | Shares withheld to cover tax on vesting/exercise |
| **D** | Disposition back to the issuer (e.g. shares returned to company) |
| **G** | Gift of securities |
| **X** | Exercise of in-the-money derivative |
| **C** | Conversion of derivative security |
| **W** | Inheritance (will or descent) |
| **J** | Other acquisition or disposition |
""")

ticker_input = st.text_input("Enter ticker (e.g. AAPL)", "").upper().strip()

if ticker_input:
    ticker_sql = """
    SELECT t.insider_name, t.role_category, t.transaction_date, t.transaction_code,
           t.shares, t.price_per_share, t.total_value, t.is_10b51, t.is_routine
    FROM transactions t
    JOIN form4_filings f ON f.id = t.filing_id
    JOIN companies c ON c.cik = f.cik
    WHERE c.ticker = %s
    ORDER BY t.transaction_date DESC
    LIMIT 100
    """
    ticker_rows = query(ticker_sql, (ticker_input,))
    if not ticker_rows:
        st.warning(f"No transactions found for {ticker_input}")
    else:
        df = pd.DataFrame(ticker_rows)
        df["total_value"] = df["total_value"].apply(_fmt_currency)
        df["price_per_share"] = df["price_per_share"].apply(
            lambda x: f"${float(x):.2f}" if x else "N/A"
        )
        df["is_routine"] = df["is_routine"].apply(
            lambda x: "routine" if x is True else ("opportunistic" if x is False else "unknown")
        )
        df.columns = [c.replace("_", " ").title() for c in df.columns]
        st.dataframe(df, use_container_width=True, hide_index=True)

        buy_rows = [r for r in ticker_rows if r["transaction_code"] == "P"]
        if buy_rows:
            buy_df = pd.DataFrame(buy_rows)
            buy_df["price_per_share"] = pd.to_numeric(buy_df["price_per_share"], errors="coerce")
            buy_df["shares"] = pd.to_numeric(buy_df["shares"], errors="coerce").fillna(0)
            buy_df["total_value"] = pd.to_numeric(buy_df["total_value"], errors="coerce")
            buy_df = buy_df[buy_df["price_per_share"].notna()]
            # Colour-code routine vs opportunistic
            buy_df["routine_label"] = buy_df["is_routine"].apply(
                lambda x: "routine" if x else "opportunistic"
            )
            fig2 = px.scatter(
                buy_df,
                x="transaction_date",
                y="price_per_share",
                size="shares",
                color="routine_label",
                color_discrete_map={"opportunistic": "#00cc66", "routine": "#888888"},
                hover_data=["insider_name", "role_category", "total_value"],
                title=f"{ticker_input} — Insider Purchases (green = opportunistic, grey = routine)",
                labels={"price_per_share": "Price ($)", "transaction_date": "Date"},
            )
            st.plotly_chart(fig2, use_container_width=True)

    # Also show any signals for this ticker
    sig_ticker_sql = """
    SELECT signal_date, score, signal_type, cluster_flag
    FROM signals
    WHERE ticker = %s
    ORDER BY signal_date DESC
    LIMIT 20
    """
    sig_ticker = query(sig_ticker_sql, (ticker_input,))
    if sig_ticker:
        st.subheader(f"Signal History for {ticker_input}")
        st.dataframe(pd.DataFrame(sig_ticker), use_container_width=True, hide_index=True)
