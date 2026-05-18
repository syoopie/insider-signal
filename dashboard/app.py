"""
Streamlit dashboard for the Insider Signal system.

Reads from Neon (pooled connection) — all read-only queries.
Sections:
  1. Active signals table (BUY / CLUSTER_BUY / WATCH)
  2. Evidence panel (expandable per signal)
  3. Backtest performance chart
  4. Per-ticker insider history
"""

import os
import json
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor


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
lookback_days = st.sidebar.slider("Signal lookback (days)", 7, 90, 14)
min_score = st.sidebar.slider("Minimum score", 0, 100, 60)
signal_types = st.sidebar.multiselect(
    "Signal types",
    ["CLUSTER_BUY", "BUY", "WATCH"],
    default=["CLUSTER_BUY", "BUY", "WATCH"],
)
cap_tiers = st.sidebar.multiselect(
    "Market cap tier",
    ["small", "mid", "large", "unknown"],
    default=["small", "mid", "large", "unknown"],
)
st.sidebar.caption("Small < $2B · Mid $2B–$10B · Large ≥ $10B")

since_date = date.today() - timedelta(days=lookback_days)

# --- Active Signals ---
st.header("Active Signals")

signals_sql = """
SELECT s.id, s.ticker, s.signal_date, s.score, s.signal_type,
       s.cluster_flag, s.score_breakdown, s.evidence, c.cap_tier, c.name
FROM signals s
LEFT JOIN companies c ON c.ticker = s.ticker
WHERE s.signal_date >= %s
  AND s.score >= %s
  AND s.signal_type = ANY(%s)
ORDER BY s.score DESC, s.signal_date DESC
"""

signals = query(signals_sql, (since_date, min_score, signal_types))

# Filter by cap tier
if cap_tiers:
    signals = [s for s in signals if (s.get("cap_tier") or "unknown") in cap_tiers]

if not signals:
    st.info("No signals match the current filters.")
else:
    # Legend
    st.caption("⚡ Cluster Buy = 3+ insiders buying in 14-day window  ·  ✅ Buy = high-conviction single insider  ·  👁 Watch = moderate score, worth monitoring")

    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Signals", len(signals))
    col2.metric("⚡ Cluster Buys", sum(1 for s in signals if s["signal_type"] == "CLUSTER_BUY"))
    col3.metric("✅ Buy Signals", sum(1 for s in signals if s["signal_type"] == "BUY"))
    col4.metric("👁 Watch Signals", sum(1 for s in signals if s["signal_type"] == "WATCH"))

    st.divider()

    TYPE_ICON  = {"CLUSTER_BUY": "⚡", "BUY": "✅", "WATCH": "👁"}
    TYPE_LABEL = {"CLUSTER_BUY": "Cluster Buy", "BUY": "Buy", "WATCH": "Watch"}

    # Signals table
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

        header = (
            f"{icon} **{sig['ticker']}**{name_part} — "
            f"Score {sig['score']}/100 · {type_label} · {cap_label} · {sig['signal_date']}"
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
                    "Shares After": f"{int(i.get('shares_after') or 0):,}",
                    "% Increase": f"+{i.get('pct_increase'):.0f}%" if i.get("pct_increase") else "N/A",
                    "Date": i.get("transaction_date"),
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
                cluster = ev.get("cluster", {})
                if cluster.get("is_cluster"):
                    st.success(f"Cluster signal: {cluster.get('insider_count')} insiders bought in 14-day window")
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

# --- Backtest Performance ---
st.header("Backtest Performance")

backtest_sql = """
SELECT run_date, horizon_days, hit_rate, avg_return, sharpe, n_trades
FROM backtest_runs
ORDER BY run_date DESC, horizon_days ASC
LIMIT 200
"""
bt_rows = query(backtest_sql)

if not bt_rows:
    st.info("No backtest data yet. Runs weekly after the first week of signals.")
else:
    bt_df = pd.DataFrame(bt_rows)
    horizons = sorted(bt_df["horizon_days"].unique())
    latest_date = bt_df["run_date"].max()
    latest = bt_df[bt_df["run_date"] == latest_date]

    st.caption(f"Latest backtest: {latest_date}")

    mc1, mc2, mc3 = st.columns(3)
    for col, horizon in zip([mc1, mc2, mc3], horizons[:3]):
        row = latest[latest["horizon_days"] == horizon]
        if not row.empty:
            r = row.iloc[0]
            col.metric(
                f"{horizon}d Hit Rate",
                f"{r['hit_rate']:.0f}%",
                help="% of BUY signals with positive excess return vs SPY",
            )

    fig = px.line(
        bt_df,
        x="run_date",
        y="avg_return",
        color="horizon_days",
        labels={"avg_return": "Avg Excess Return (%)", "run_date": "Backtest Date", "horizon_days": "Days"},
        title="Average Excess Return vs SPY by Hold Horizon",
    )
    st.plotly_chart(fig, use_container_width=True)

# --- Per-Ticker Insider History ---
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
           t.shares, t.price_per_share, t.total_value, t.is_10b51
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
            lambda x: f"${x:.2f}" if x else "N/A"
        )
        df.columns = [c.replace("_", " ").title() for c in df.columns]
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Mini chart of purchases vs price
        buy_rows = [r for r in ticker_rows if r["transaction_code"] == "P"]
        if buy_rows:
            buy_df = pd.DataFrame(buy_rows)
            # psycopg2 returns NUMERIC columns as Decimal — cast to float for Plotly
            buy_df["price_per_share"] = pd.to_numeric(buy_df["price_per_share"], errors="coerce")
            buy_df["shares"] = pd.to_numeric(buy_df["shares"], errors="coerce").fillna(0)
            buy_df["total_value"] = pd.to_numeric(buy_df["total_value"], errors="coerce")
            buy_df = buy_df[buy_df["price_per_share"].notna()]
            fig2 = px.scatter(
                buy_df,
                x="transaction_date",
                y="price_per_share",
                size="shares",
                hover_data=["insider_name", "role_category", "total_value"],
                title=f"{ticker_input} — Insider Purchases Over Time",
                labels={"price_per_share": "Price ($)", "transaction_date": "Date"},
            )
            st.plotly_chart(fig2, use_container_width=True)
