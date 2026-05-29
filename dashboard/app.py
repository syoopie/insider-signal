"""
Insider Signal — Streamlit dashboard.

Tabs:
  1. Signals   — Top Picks + filterable signal list with evidence cards
  2. Positions — Live P&L for BUY/CLUSTER_BUY within the hold window
  3. Backtest  — Hit rate, excess return, distribution, stratification, risk
  4. History   — Per-ticker transaction timeline
  5. About     — Data sources, scoring model, methodology, limitations
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


HOLD_HORIZON_DAYS = 90


# ── Helpers ──────────────────────────────────────────────────────────────────

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


def _stat_sig_badge(n: int) -> str:
    if n is None:
        return ""
    if n < 10:
        return "🔴"
    if n < 30:
        return "🟡"
    return ""


def _parse_ev(raw) -> dict:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


def _parse_metrics(raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


def _conviction(sig: dict, ev: dict) -> str:
    cl = ev.get("cluster", {})
    is_tight = bool(cl.get("tight_cluster"))
    is_exec  = bool(cl.get("executive_cluster"))
    if sig["signal_type"] == "CLUSTER_BUY":
        if is_tight and is_exec:
            return "PRIME"
        if is_tight or is_exec:
            return "STRONG"
        return "CLUSTER"
    if sig["score"] >= 70:
        return "HIGH"
    return "BUY"


_CONVICTION_COLOR = {
    "PRIME":   "#00cc66",
    "STRONG":  "#66cc00",
    "CLUSTER": "#ccaa00",
    "HIGH":    "#0099cc",
    "BUY":     "#5588cc",
}

_TYPE_ICON = {"CLUSTER_BUY": "⚡", "BUY": "✅", "WATCH": "👁"}


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


# ── DB ────────────────────────────────────────────────────────────────────────

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
        get_db.clear()
        return []


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Insider Signal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.title("📈 Insider Signal")
st.caption(f"SEC Form 4 insider purchase signals · updated daily (weekdays) · {date.today()}")

tab_signals, tab_positions, tab_backtest, tab_history, tab_about = st.tabs([
    "📊 Signals", "💼 Positions", "📈 Backtest", "🔍 History", "ℹ️ About",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — SIGNALS
# ══════════════════════════════════════════════════════════════════════════════
with tab_signals:

    # ── Filters ──
    with st.expander("Filters", expanded=True):
        fc1, fc2, fc3, fc4 = st.columns(4)

        lookback_days = fc1.slider("Lookback (days)", 1, 30, 14)
        min_score     = fc2.slider("Min score", 0, 100, 50)

        with fc3:
            st.caption("Signal types")
            t_cluster = st.checkbox("⚡ Cluster Buy", value=True)
            t_buy     = st.checkbox("✅ Buy",         value=True)
            t_watch   = st.checkbox("👁 Watch",       value=False)
        signal_types = (
            (["CLUSTER_BUY"] if t_cluster else []) +
            (["BUY"]         if t_buy     else []) +
            (["WATCH"]       if t_watch   else [])
        )

        with fc4:
            st.caption("Cap tier")
            c_small   = st.checkbox("Small  (<\\$2B)",   value=True)
            c_mid     = st.checkbox("Mid  (\\$2B–\\$10B)", value=True)
            c_large   = st.checkbox("Large  (>\\$10B)",  value=False)
            c_unknown = st.checkbox("Unknown",         value=True)
        cap_tiers = (
            (["small"]   if c_small   else []) +
            (["mid"]     if c_mid     else []) +
            (["large"]   if c_large   else []) +
            (["unknown"] if c_unknown else [])
        )

        st.caption(
            "Large-cap clusters: 0% hit rate at 90d (−16% avg excess). "
            "WATCH: ~35% hit rate vs 55%+ for BUY/CLUSTER_BUY. "
            "Score ≥50 cuts noise by ~60%."
        )

    since_date = date.today() - timedelta(days=lookback_days)

    signals = query("""
        SELECT s.id, s.ticker, s.signal_date, s.score, s.signal_type,
               s.cluster_flag, s.score_breakdown, s.evidence, c.cap_tier, c.name
        FROM signals s
        LEFT JOIN companies c ON c.ticker = s.ticker
        WHERE s.signal_date >= %s
          AND s.score >= %s
          AND s.signal_type = ANY(%s)
        ORDER BY
          CASE s.signal_type WHEN 'CLUSTER_BUY' THEN 1 WHEN 'BUY' THEN 2 ELSE 3 END,
          s.score DESC, s.signal_date DESC
    """, (since_date, min_score, signal_types))

    if cap_tiers:
        signals = [s for s in signals if (s.get("cap_tier") or "unknown") in cap_tiers]

    # secondary quality sort within CLUSTER_BUY
    def _qkey(sig):
        ev = _parse_ev(sig.get("evidence"))
        cl = ev.get("cluster", {})
        if sig["signal_type"] != "CLUSTER_BUY":
            return (10, -sig["score"])
        t, e = bool(cl.get("tight_cluster")), bool(cl.get("executive_cluster"))
        sub = 0 if (t and e) else (1 if t else (2 if e else 3))
        return (sub, -sig["score"])

    signals.sort(key=_qkey)

    if not signals:
        st.info("No signals match the current filters. Try widening lookback or lowering min score.")
    else:
        # ── Summary metrics ──
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Signals", len(signals))
        m2.metric("⚡ Cluster Buy", sum(1 for s in signals if s["signal_type"] == "CLUSTER_BUY"))
        m3.metric("✅ Buy", sum(1 for s in signals if s["signal_type"] == "BUY"))
        m4.metric("👁 Watch", sum(1 for s in signals if s["signal_type"] == "WATCH"))
        m5.metric("Avg Score", f"{sum(s['score'] for s in signals) / len(signals):.0f}")

        st.divider()

        # ── Top Picks ──
        top = [s for s in signals if s["signal_type"] == "CLUSTER_BUY"][:3]
        if not top:
            top = [s for s in signals if s["signal_type"] == "BUY"][:3]
        if top:
            st.subheader("Top Picks")
            for col, sig in zip(st.columns(len(top)), top):
                ev  = _parse_ev(sig.get("evidence"))
                cl  = ev.get("cluster", {})
                cv  = _conviction(sig, ev)
                clr = _CONVICTION_COLOR[cv]
                n_b = cl.get("insider_count", 1) if sig["signal_type"] == "CLUSTER_BUY" else 1
                cap = (sig.get("cap_tier") or "unknown").title()
                cmp = sig.get("name") or ev.get("company_name") or ""
                sub = f"<br><small style='color:#aaa'>{cmp}</small>" if cmp and cmp != sig["ticker"] else ""
                tags = []
                if cl.get("tight_cluster"):
                    tags.append("tight window")
                if cl.get("executive_cluster"):
                    tags.append("exec cluster")
                tag_str = f"<br><small style='color:{clr}'>{' · '.join(tags)}</small>" if tags else ""
                with col:
                    st.markdown(
                        f"<div style='border:1px solid {clr};border-radius:10px;padding:14px 16px;margin-bottom:4px'>"
                        f"<span style='color:{clr};font-weight:700;font-size:0.85em;letter-spacing:1px'>{cv}</span>"
                        f"<br><span style='font-size:1.5em;font-weight:700'>{sig['ticker']}</span>{sub}"
                        f"<br><span style='color:#ccc'>Score <b style='color:#fff'>{sig['score']}</b>/100 · {cap}-cap</span>"
                        f"<br><span style='color:#aaa;font-size:0.9em'>{n_b} insider{'s' if n_b > 1 else ''} · {sig['signal_date']}</span>"
                        f"{tag_str}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
            st.divider()

        # ── Signal list ──
        st.subheader(f"All Signals ({len(signals)})")
        for sig in signals:
            ev      = _parse_ev(sig.get("evidence"))
            cl      = ev.get("cluster", {})
            cv      = _conviction(sig, ev)
            clr     = _CONVICTION_COLOR[cv]
            icon    = _TYPE_ICON.get(sig["signal_type"], "")
            cap     = (sig.get("cap_tier") or "unknown").title()
            company = sig.get("name") or ev.get("company_name") or sig["ticker"]
            tags    = []
            if cl.get("tight_cluster"):
                tags.append("tight")
            if cl.get("executive_cluster"):
                tags.append("exec")
            tag_part = f" · {' · '.join(tags)}" if tags else ""
            n_ins = cl.get("insider_count", 1) if sig["signal_type"] == "CLUSTER_BUY" else 1
            ins_part = f" · {n_ins} insiders" if n_ins > 1 else ""

            header = (
                f"{icon} **{sig['ticker']}** — {company} &nbsp;|&nbsp; "
                f"<span style='color:{clr}'>{cv}</span> &nbsp;·&nbsp; "
                f"Score **{sig['score']}**/100 &nbsp;·&nbsp; {cap}-cap{ins_part}{tag_part} &nbsp;·&nbsp; {sig['signal_date']}"
            )

            with st.expander(f"{icon} {sig['ticker']} — {company}  |  {cv}  ·  Score {sig['score']}/100  ·  {cap}-cap{ins_part}{tag_part}  ·  {sig['signal_date']}"):

                left, right = st.columns([3, 2])

                with left:
                    # Who bought
                    insiders = ev.get("insiders", [])
                    if insiders:
                        st.markdown("**Who Bought**")
                        ins_df = pd.DataFrame([{
                            "Name":       i.get("name"),
                            "Role":       (i.get("role_raw") or i.get("role", "")).title(),
                            "Date":       i.get("transaction_date"),
                            "Shares":     f"{int(i.get('shares_bought') or 0):,}",
                            "Price":      f"${i.get('price'):.2f}" if i.get("price") else "N/A",
                            "Value":      _fmt_currency(i.get("total_value")),
                            "% Increase": f"+{i.get('pct_increase'):.0f}%" if i.get("pct_increase") else "N/A",
                        } for i in insiders])
                        st.dataframe(ins_df, use_container_width=True, hide_index=True)

                    # Context badges
                    if cl.get("is_cluster"):
                        n_cl = cl.get("insider_count", 0)
                        extras = []
                        if cl.get("executive_cluster"):
                            extras.append("includes CFO/CEO/COO/Chairman")
                        if cl.get("tight_cluster"):
                            extras.append("≥3 buyers within 5 days")
                        extra_str = " — " + ", ".join(extras) if extras else ""
                        st.success(f"Cluster: {n_cl} insiders in 14-day window{extra_str}")

                    if ev.get("near_52wk_low"):
                        pct = ev.get("pct_above_52wk_low", 0)
                        low = ev.get("price_52wk_low")
                        st.info(f"Near 52-week low — {pct:.0f}% above ${low:.2f}" if low else "Near 52-week low")

                    c1, c2 = st.columns(2)
                    c1.caption(f"Filed: {ev.get('filed_date')}")
                    c2.caption(f"Signal date: {ev.get('signal_date')}")

                with right:
                    # Score breakdown bar chart
                    breakdown = _parse_ev(sig.get("score_breakdown"))
                    if breakdown:
                        bd_data = [
                            (k.replace("_", " ").title(), v)
                            for k, v in breakdown.items()
                            if isinstance(v, (int, float)) and v != 0
                        ]
                        if bd_data:
                            bd_df = pd.DataFrame(bd_data, columns=["Factor", "Pts"])
                            bd_df = bd_df.sort_values("Pts")
                            colors = ["#d62728" if v < 0 else "#2ca02c" for v in bd_df["Pts"]]
                            fig_bd = go.Figure(go.Bar(
                                x=bd_df["Pts"],
                                y=bd_df["Factor"],
                                orientation="h",
                                marker_color=colors,
                                text=bd_df["Pts"].apply(lambda v: f"+{v}" if v > 0 else str(v)),
                                textposition="outside",
                            ))
                            fig_bd.update_layout(
                                title=f"Score: {sig['score']}/100",
                                margin=dict(l=10, r=30, t=40, b=10),
                                height=max(180, len(bd_data) * 28 + 60),
                                xaxis_title=None,
                                yaxis_title=None,
                                plot_bgcolor="rgba(0,0,0,0)",
                                paper_bgcolor="rgba(0,0,0,0)",
                                font_color="#ccc",
                            )
                            fig_bd.add_vline(x=0, line_color="gray", line_width=1)
                            st.plotly_chart(fig_bd, use_container_width=True, key=f"bd_{sig['id']}")

                    research = ev.get("research_basis", [])
                    if research:
                        with st.expander("Research basis"):
                            for ref in research:
                                st.caption(f"• {ref}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — POSITIONS
# ══════════════════════════════════════════════════════════════════════════════
with tab_positions:
    st.subheader(f"Open Positions — BUY & CLUSTER_BUY within {HOLD_HORIZON_DAYS}-day hold window")
    st.caption("Return = (current price − avg insider entry) / entry. Raw return, not excess vs SPY.")

    positions = query("""
        SELECT s.ticker, s.signal_date, s.score, s.signal_type, s.cluster_flag,
               c.cap_tier, c.name AS company_name, s.evidence
        FROM signals s
        LEFT JOIN companies c ON c.ticker = s.ticker
        WHERE s.signal_date >= %s
          AND s.signal_type IN ('BUY', 'CLUSTER_BUY')
        ORDER BY s.signal_date DESC
    """, (date.today() - timedelta(days=HOLD_HORIZON_DAYS),))

    if not positions:
        st.info(f"No BUY or CLUSTER_BUY signals in the past {HOLD_HORIZON_DAYS} days.")
    else:
        with st.spinner("Fetching live prices..."):
            pass  # spinner just for UX — prices fetched below with @st.cache_data

        pos_rows = []
        for p in positions:
            sig_date = p["signal_date"]
            if isinstance(sig_date, str):
                try:
                    sig_date = date.fromisoformat(sig_date[:10])
                except ValueError:
                    continue
            days_in   = (date.today() - sig_date).days
            days_left = max(0, HOLD_HORIZON_DAYS - days_in)
            ev        = _parse_ev(p.get("evidence"))
            insiders  = ev.get("insiders", [])
            total_val    = sum(float(i["total_value"])   for i in insiders if i.get("total_value"))
            total_shares = sum(float(i["shares_bought"]) for i in insiders if i.get("shares_bought"))
            entry        = total_val / total_shares if total_shares > 0 else None
            current   = _fetch_current_price(p["ticker"])
            ret       = (current - entry) / entry * 100 if (current and entry and entry > 0) else None

            pos_rows.append({
                "Ticker":      p["ticker"],
                "Company":     p.get("company_name") or "",
                "Type":        p["signal_type"],
                "Score":       p["score"],
                "Cap":         (p.get("cap_tier") or "?").title(),
                "Signal Date": str(sig_date),
                "Days In":     days_in,
                "Days Left":   days_left,
                "Entry":       _fmt_currency(entry),
                "Current":     _fmt_currency(current) if current else "—",
                "Return":      _fmt_pct(ret) if ret is not None else "—",
                "Status":      "Active" if days_left > 0 else "Elapsed",
            })

        def _row_color(row):
            r = row.get("Return", "—")
            if r == "—":
                bg = "#1a1a2e" if row.get("Status") == "Active" else "#1a1a1a"
            else:
                try:
                    v = float(r.replace("%", "").replace("+", ""))
                    bg = "#0a2a0a" if v >= 10 else "#162a16" if v >= 0 else "#2a1208" if v >= -10 else "#2a0808"
                except ValueError:
                    bg = "#1a1a1a"
            return [f"background-color:{bg}"] * len(row)

        st.dataframe(
            pd.DataFrame(pos_rows).style.apply(_row_color, axis=1),
            use_container_width=True,
            hide_index=True,
        )
        st.caption("Prices cached 5 min. Entry = avg purchase price across all insiders in the signal.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — BACKTEST
# ══════════════════════════════════════════════════════════════════════════════
with tab_backtest:

    bt_rows = query("""
        SELECT run_date, horizon_days, hit_rate, avg_return, median_return,
               p25_return, p75_return, sharpe, iwm_avg_return, n_trades, metrics
        FROM backtest_runs
        ORDER BY run_date DESC, horizon_days ASC
        LIMIT 300
    """)

    if not bt_rows:
        st.info("No backtest data yet — runs weekly on Sundays.")
    else:
        bt_df       = pd.DataFrame(bt_rows)
        horizons    = sorted(bt_df["horizon_days"].unique())
        latest_date = bt_df["run_date"].max()
        latest      = bt_df[bt_df["run_date"] == latest_date]

        st.caption(f"Latest run: **{latest_date}** · Benchmark: SPY for all signals, IWM also shown for small-cap")

        # ── Hit rate metrics ──
        cols = st.columns(len(horizons))
        for col, h in zip(cols, horizons):
            row = latest[latest["horizon_days"] == h]
            if not row.empty:
                r   = row.iloc[0]
                n      = int(r.get("n_trades") or 0)
                med    = r.get("median_return")
                vs_50  = round(r["hit_rate"] - 50, 1)
                col.metric(
                    f"{h}d Hit Rate",
                    f"{r['hit_rate']:.0f}%",
                    delta=vs_50,
                    help=(
                        f"n={n} signals · delta = pp above/below 50% baseline"
                        + (f" · median excess {_fmt_pct(med)}" if med is not None else "")
                    ),
                )

        # ── Avg excess return chart ──
        # Use per-signal exec_date from detail so the x-axis spans the full
        # signal history (up to LOOKBACK_DAYS) rather than just the number of
        # weekly backtest runs recorded in backtest_runs.
        detail_rows = []
        for _, r in latest.iterrows():
            h = r["horizon_days"]
            for d in (_parse_metrics(r.get("metrics")).get("detail") or []):
                if d.get("exec_date"):
                    detail_rows.append({
                        "exec_date": d["exec_date"][:10],
                        "excess_return": d["excess_return"],
                        "h": f"{h}d",
                    })

        if detail_rows:
            det_df = pd.DataFrame(detail_rows)
            det_df["exec_date"] = pd.to_datetime(det_df["exec_date"])
            det_df["month"] = det_df["exec_date"].dt.to_period("M").dt.start_time
            monthly = det_df.groupby(["month", "h"], as_index=False)["excess_return"].mean()
            fig = px.line(
                monthly, x="month", y="excess_return", color="h", markers=True,
                labels={"excess_return": "Avg Excess Return vs SPY (%)", "month": "Signal Month", "h": "Horizon"},
                title="Avg Excess Return vs SPY by Hold Horizon (monthly avg of individual signals)",
            )
        else:
            chart_df = latest.copy()
            chart_df["h"] = chart_df["horizon_days"].astype(str) + "d"
            fig = px.bar(
                chart_df.sort_values("horizon_days"),
                x="h", y="avg_return",
                color="avg_return",
                color_continuous_scale=["#d62728", "#aec7e8", "#2ca02c"],
                labels={"avg_return": "Avg Excess Return vs SPY (%)", "h": "Hold Horizon"},
                title="Avg Excess Return vs SPY by Hold Horizon",
                text="avg_return",
            )
            fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
            fig.update_layout(coloraxis_showscale=False, showlegend=False)
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        st.plotly_chart(fig, use_container_width=True)

        # ── Distribution + stratification in tabs ──
        dt_dist, dt_score, dt_cap, dt_type, dt_risk, dt_cluster = st.tabs([
            "Distribution", "By Score Band", "By Cap Tier", "By Signal Type", "Risk", "Cluster 50–64",
        ])

        with dt_dist:
            st.caption("Box = p25–p75 · Line = median · Whiskers = min/max. Mean alone hides tail risk.")
            dist_rows = []
            for _, r in latest.iterrows():
                dist = _parse_metrics(r.get("metrics")).get("distribution") or {}
                if dist:
                    dist_rows.append({
                        "Horizon": f"{r['horizon_days']}d",
                        "p25": dist.get("p25"), "median": dist.get("median"),
                        "p75": dist.get("p75"), "min": dist.get("max_loss"), "max": dist.get("max_gain"),
                    })
            if dist_rows:
                fig_box = go.Figure()
                for dr in dist_rows:
                    if None not in (dr["p25"], dr["median"], dr["p75"], dr["min"], dr["max"]):
                        fig_box.add_trace(go.Box(
                            name=dr["Horizon"], q1=[dr["p25"]], median=[dr["median"]],
                            q3=[dr["p75"]], lowerfence=[dr["min"]], upperfence=[dr["max"]],
                            boxpoints=False,
                        ))
                fig_box.update_layout(
                    title="Excess Return Distribution by Horizon (%)",
                    yaxis_title="Excess Return vs SPY (%)",
                )
                fig_box.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
                st.plotly_chart(fig_box, use_container_width=True)
            else:
                st.info("No distribution data yet.")

        def _render_pivot(key, tab, dim_col):
            with tab:
                data = {}
                for _, r in latest.iterrows():
                    h = f"{r['horizon_days']}d"
                    strat = _parse_metrics(r.get("metrics")).get(key) or {}
                    for grp, m in (strat or {}).items():
                        if m:
                            data.setdefault(grp, {})[h] = m
                if not data:
                    st.info("No stratified data yet.")
                    return

                def _n_label(n: int) -> str:
                    if not n:
                        return "—"
                    if n < 10:
                        return f"{n} ⚠"
                    if n < 30:
                        return f"{n} ~"
                    return str(n)

                h_cols = [f"{h}d" for h in horizons]
                rows = []
                for grp in sorted(data):
                    for h in h_cols:
                        m = data[grp].get(h) or {}
                        n = m.get("n") or 0
                        rows.append({
                            dim_col:      grp,
                            "Horizon":    h,
                            "N":          _n_label(n),
                            "Hit Rate":   f"{m['hit_rate']:.0f}%" if m.get("hit_rate") is not None else "—",
                            "Avg Return": _fmt_pct(m.get("avg_return"))    if m else "—",
                            "Median":     _fmt_pct(m.get("median_return")) if m else "—",
                        })

                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                st.caption("N ⚠ = fewer than 10 signals (ignore). N ~ = fewer than 30 (directional only).")

        _render_pivot("by_score_band",  dt_score, "Score Band")
        _render_pivot("by_cap_tier",    dt_cap,   "Cap Tier")
        _render_pivot("by_signal_type", dt_type,  "Signal Type")

        with dt_risk:
            st.caption("High % losses >20% or long losing streaks signal tail risk.")
            risk_rows = []
            for _, r in latest.iterrows():
                risk = _parse_metrics(r.get("metrics")).get("risk") or {}
                if risk:
                    risk_rows.append({
                        "Horizon": f"{r['horizon_days']}d",
                        "% Losses >20%": _fmt_pct(risk.get("pct_loss_gt20"), prefix=False),
                        "Max Consec. Losses": risk.get("max_consecutive_losses"),
                        "Worst Outcome": _fmt_pct(risk.get("worst_outcome")),
                        "Missing SPY Data": risk.get("n_no_spy_data"),
                    })
            if risk_rows:
                st.dataframe(pd.DataFrame(risk_rows), use_container_width=True, hide_index=True)
            else:
                st.info("No risk data yet.")

        with dt_cluster:
            st.caption("Cluster signals with score 50–64 — below the single-BUY threshold but with cluster conviction.")
            cl_rows = []
            for _, r in latest.iterrows():
                cl = _parse_metrics(r.get("metrics")).get("cluster_5064")
                if cl:
                    n = cl.get("n", 0)
                    n_label = f"{n} ⚠" if n < 10 else (f"{n} ~" if n < 30 else str(n))
                    cl_rows.append({
                        "Horizon":   f"{r['horizon_days']}d",
                        "N":         n_label,
                        "Hit Rate":  f"{cl.get('hit_rate', 0):.0f}%",
                        "Avg Excess": _fmt_pct(cl.get("avg_return")),
                        "Median":    _fmt_pct(cl.get("median_return")),
                    })
            if cl_rows:
                st.dataframe(pd.DataFrame(cl_rows), use_container_width=True, hide_index=True)
            else:
                st.info("No cluster 50–64 data yet.")

        # ── Rolling hit rate ──
        st.subheader("Rolling 90-Day Hit Rate")
        st.caption("Stable or rising = alpha persists. Sharp decline = model losing edge or regime change.")
        rolling_rows = []
        for _, r in latest.iterrows():
            for item in (_parse_metrics(r.get("metrics")).get("rolling_hit_rate_90d") or []):
                rolling_rows.append({
                    "date": item["date"], "hit_rate": item["hit_rate"],
                    "horizon": f"{r['horizon_days']}d",
                })
        if rolling_rows:
            rhr_df = pd.DataFrame(rolling_rows)
            fig_rhr = px.line(
                rhr_df, x="date", y="hit_rate", color="horizon", markers=True,
                labels={"hit_rate": "Hit Rate (%)", "date": "Date", "horizon": "Horizon"},
                title="Rolling 90-Day Hit Rate",
            )
            fig_rhr.add_hline(y=50, line_dash="dash", line_color="gray", opacity=0.5,
                              annotation_text="50% (coin-flip baseline)")
            st.plotly_chart(fig_rhr, use_container_width=True)
        else:
            st.info("Rolling data available after the first full backtest run.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — HISTORY
# ══════════════════════════════════════════════════════════════════════════════
with tab_history:
    st.subheader("Insider Transaction History")

    ticker_input = st.text_input("Ticker", placeholder="e.g. NVDA").upper().strip()

    with st.expander("Transaction code reference"):
        st.markdown("""
| Code | Meaning |
|------|---------|
| **P** | Open-market purchase — insider used their own money. The only code we score for buy signals. |
| **S** | Open-market sale |
| **A** | Award / grant (RSU, options) — no cash, excluded from scoring |
| **M** | Exercise or conversion of derivative (e.g. options exercised) |
| **F** | Shares withheld to cover taxes on vesting |
| **D** | Disposition back to issuer |
| **G** | Gift |
| **X** | Exercise of in-the-money derivative |
""")

    if ticker_input:
        ticker_rows = query("""
            SELECT t.insider_name, t.role_category, t.transaction_date, t.transaction_code,
                   t.shares, t.price_per_share, t.total_value, t.is_10b51, t.is_routine
            FROM transactions t
            JOIN form4_filings f ON f.id = t.filing_id
            JOIN companies c ON c.cik = f.cik
            WHERE c.ticker = %s
            ORDER BY t.transaction_date DESC
            LIMIT 100
        """, (ticker_input,))

        if not ticker_rows:
            st.warning(f"No transactions found for {ticker_input}.")
        else:
            df = pd.DataFrame(ticker_rows)
            df["total_value"]     = df["total_value"].apply(_fmt_currency)
            df["price_per_share"] = df["price_per_share"].apply(
                lambda x: f"${float(x):.2f}" if x else "N/A"
            )
            df["is_routine"] = df["is_routine"].apply(
                lambda x: "routine" if x is True else ("opportunistic" if x is False else "—")
            )
            df.columns = [c.replace("_", " ").title() for c in df.columns]
            st.dataframe(df, use_container_width=True, hide_index=True)

            buy_rows = [r for r in ticker_rows if r["transaction_code"] == "P"]
            if buy_rows:
                bdf = pd.DataFrame(buy_rows)
                bdf["price_per_share"] = pd.to_numeric(bdf["price_per_share"], errors="coerce")
                bdf["shares"]          = pd.to_numeric(bdf["shares"], errors="coerce").fillna(0)
                bdf = bdf[bdf["price_per_share"].notna()]
                bdf["label"] = bdf["is_routine"].apply(
                    lambda x: "routine" if x else "opportunistic"
                )
                fig2 = px.scatter(
                    bdf, x="transaction_date", y="price_per_share", size="shares",
                    color="label",
                    color_discrete_map={"opportunistic": "#00cc66", "routine": "#666"},
                    hover_data=["insider_name", "role_category", "total_value"],
                    title=f"{ticker_input} — Purchases (green = opportunistic, grey = routine)",
                    labels={"price_per_share": "Price ($)", "transaction_date": "Date"},
                )
                st.plotly_chart(fig2, use_container_width=True)

        sig_rows = query("""
            SELECT signal_date, score, signal_type, cluster_flag
            FROM signals WHERE ticker = %s ORDER BY signal_date DESC LIMIT 20
        """, (ticker_input,))
        if sig_rows:
            st.subheader(f"Signal history for {ticker_input}")
            st.dataframe(pd.DataFrame(sig_rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — ABOUT
# ══════════════════════════════════════════════════════════════════════════════
with tab_about:

    st.subheader("How This Works")
    st.markdown("""
This dashboard surfaces statistically-grounded buy signals from SEC Form 4 insider purchase
disclosures. Everything is automated and runs at zero cost.
""")

    a1, a2 = st.columns(2)

    with a1:
        st.markdown("### Data Source")
        st.markdown("""
**SEC EDGAR Form 4** — required by law whenever a corporate insider (officer, director, or >10%
shareholder) buys or sells shares. Filed within 2 business days of each transaction.

- Fetched daily from `https://efts.sec.gov` and `https://www.sec.gov/Archives/edgar/`
- ~300–500 filings/day after filtering to the S&P 500 + Russell 2000 universe (~3,500 tickers)
- Each filing includes: who bought, how many shares, at what price, in what form (direct/indirect),
  and whether it was a pre-arranged 10b5-1 plan trade
- **Coverage**: 2024-04-03 → present, updated weekdays at 11am UTC via GitHub Actions
""")

        st.markdown("### Universe")
        st.markdown("""
Only companies in the **S&P 500 + Russell 2000** are tracked. This covers:
- All major large/mid-cap names (S&P 500)
- The small-cap universe where insider signal alpha is strongest (Russell 2000)

Filings outside this universe are ignored to keep database size manageable (<0.5 GB).
""")

        st.markdown("### Market Cap Data")
        st.markdown("""
Refreshed weekly using a 3-pass approach — all free, no API keys:

1. **EDGAR bulk XBRL frames** — `us-gaap/CommonStockSharesOutstanding` (~4,250 companies)
2. **EDGAR DEI frames** — `dei/EntityCommonStockSharesOutstanding` (+850 companies that file
   under the DEI taxonomy instead, including many large-caps like LLY, WMT, IT, LUV)
3. **EDGAR per-company concept API** — fallback for community banks and newer filers
   not yet in the bulk frames

Shares outstanding × current price (Yahoo Finance) = market cap → cap tier.

| Tier | Range | Signal adjustment |
|------|-------|-------------------|
| Small | < $2B | +15 pts (highest alpha per research) |
| Mid | $2B–$10B | +8 pts |
| Large | > $10B | +0 pts |
| Unknown | not resolved | +5 pts (conservative — some unknowns are large-caps) |
""")

    with a2:
        st.markdown("### Scoring Model")
        st.markdown("""
Every open-market purchase (Form 4 code **P**) is scored 0–100. Three hard disqualifiers
return score = 0 immediately:

- **10b5-1 plan** (`isSubjectToRule10b51 = true`) — pre-arranged trades have near-zero alpha
  (Cohen, Malloy & Pomorski 2012)
- **Total value < $2,000** — DRIP / 401(k) noise
- **Routine trader** — insider bought in the same calendar month in ≥2 of 3 prior years
  (Cohen et al.: routine trades ≈ 0 alpha; opportunistic trades → 82 bps/month)
""")

        scoring_data = [
            ("CFO purchase",              "+20", "21.5% avg annual return — highest of any role (TipRanks)"),
            ("Director purchase",         "+16", "20.7% avg annual return"),
            ("Chairman purchase",         "+14", ""),
            ("COO / Officer purchase",    "+12", "19.8% avg annual return"),
            ("CEO purchase",              "+10", "19.3% — counterintuitively lowest"),
            ("Other role",                "+6",  ""),
            ("Indirect purchase",         "−8",  "Via LLC/trust — less conviction"),
            ("Small-cap (<$2B)",          "+15", "Lakonishok & Lee: +7.4% abnormal at 12 months"),
            ("Mid-cap ($2B–$10B)",        "+8",  "Moderate information asymmetry"),
            ("Large-cap (>$10B)",         "+0",  "Near-zero alpha in research"),
            ("Unknown cap",               "+5",  "Conservative — some unknowns are large-caps"),
            ("Value ≥ $500K",             "+12", "High conviction capital commitment"),
            ("Value ≥ $100K",             "+8",  ""),
            ("Holdings +30%",             "+15", "Insiders rarely increase position by >30%"),
            ("Holdings +15%",             "+10", ""),
            ("Holdings +5%",              "+5",  ""),
            ("First purchase in 12 mo",   "+10", "Non-routine; chose to act after long absence"),
            ("Sequenced buy (2nd in 30d)","+ 8", "Extended sequence = longer alpha window"),
            ("Within 5% of 52wk low",     "+12", "Buying into weakness = high conviction"),
            ("Within 10% of 52wk low",    "+7",  ""),
        ]
        st.dataframe(
            pd.DataFrame(scoring_data, columns=["Factor", "Points", "Research basis"]),
            use_container_width=True,
            hide_index=True,
            height=580,
        )

    st.divider()
    st.markdown("### Signal Types")

    s1, s2, s3 = st.columns(3)
    with s1:
        st.markdown("#### ⚡ CLUSTER_BUY")
        st.markdown("""
3+ independent insiders bought within a **14-day rolling window**, each with:
- Direct purchase (not through LLC/trust)
- Value ≥ $25K per buyer
- Not an IPO/PIPE allocation (same-price-same-date blocks are excluded)

**Quality gate to reach CLUSTER_BUY** (not just WATCH):
- Avg participant score ≥ 35
- AND: tight cluster (≥3 in 5-day sub-window) OR any buyer score ≥ 50
- Large-cap clusters are downgraded to WATCH (0% hit rate at 90d, −16% avg excess)

_Research_: cluster buys produce ~2× the alpha of a single insider buy.
""")

    with s2:
        st.markdown("#### ✅ BUY")
        st.markdown("""
Single insider (or multiple) with **score ≥ 65**.

Score 65 was chosen from the academic literature as the threshold separating
high-conviction opportunistic purchases from routine/noise. It was not tuned
on backtest results to avoid overfitting.

**Typical BUY profile:**
- CFO or Director (≥+16 pts)
- Small or mid-cap (+8–15 pts)
- Meaningful dollar size (+8–12 pts)
- Holdings increase >15% (+10–15 pts)
""")

    with s3:
        st.markdown("#### 👁 WATCH")
        st.markdown("""
Score 45–64, OR a cluster signal that didn't meet the CLUSTER_BUY quality gate
(loose clusters with weak individual scores, large-cap clusters).

WATCH signals are logged and visible here but **no Telegram alert is sent**.
Backtesting shows WATCH hit rate ~35% vs 55%+ for BUY/CLUSTER_BUY — worth
monitoring but not acting on alone.
""")

    st.divider()
    st.markdown("### Backtest Methodology")
    st.markdown("""
| Parameter | Value | Why |
|-----------|-------|-----|
| Signals included | `signal_type IN ('BUY','CLUSTER_BUY')` AND `(score ≥ 65 OR cluster_flag = TRUE)` | WATCH excluded; all CLUSTER_BUY signals included regardless of individual score |
| Signal date | `filed_date + 1` | Avoids look-ahead bias — you can't trade until the filing is public |
| Execution date | `signal_date + 3 calendar days` | Realistic fill lag |
| Benchmark | SPY for all; IWM also for small-cap | SPY understates the opportunity cost for Russell 2000 names |
| Delisted stocks | −50% excess return | Blunt survivorship bias correction |
| Horizons | 30 / 60 / 90 / 180 days | Based on literature; 60–90d is the peak alpha window |
| Cluster 50–64 | Also analyzed as a sub-group | CLUSTER_BUY signals with score 50–64 are part of the main pool (via `cluster_flag = TRUE`) and additionally broken out in their own bucket to show how that sub-group performs in isolation |

The backtest runs every Sunday via GitHub Actions. Results are stored in `backtest_runs` and
displayed in the **Backtest** tab with hit rate, avg/median excess return, and risk metrics.
""")

    st.markdown("### Known Limitations")
    st.markdown("""
- **Small sample sizes**: many buckets (large-cap, score-band) have n<30 signals — treat
  those numbers as directional, not statistically reliable
- **Lions Gate (LGF)**: insiders averaging down on a declining business generate repeated
  cluster signals. The model has no company-level "avoid" filter
- **April 2024 gap**: coverage starts mid-month (2024-04-03); first few weeks are thin
- **Delisted = −50%**: real outcomes vary (acquisition premium vs. bankruptcy); this is
  a rough bias correction
- **10b5-1 plans**: Congress required disclosure of plan adoption dates starting 2023,
  but historical plans filed before 2023 may have been excluded without this flag
- **Market cap staleness**: cap tier is refreshed weekly; a company that crossed a tier
  boundary mid-week will use the prior week's tier until Sunday
""")

    st.markdown("### Research Papers")
    st.markdown("""
| Study | Key Finding | Applied As |
|-------|-------------|------------|
| Lakonishok & Lee (2001) | Small-cap insider buys: +7.4% abnormal return at 12 months | Small-cap score boost |
| Jeng, Metrick & Zeckhauser (2003) | Purchase portfolio: ~6% annualized alpha | 60–90d hold horizon |
| Cohen, Malloy & Pomorski (2012) | Opportunistic: 82 bps/month alpha; routine ≈ 0 | Routine trader filter is mandatory |
| TipRanks CFO study | CFO: 21.5% > Director: 20.7% > Officer: 19.8% > CEO: 19.3% | Role weights (CFO scored highest, CEO lowest) |
| Cluster research (multiple) | 3+ insiders buying together ≈ 2× alpha of single buy | Cluster bonus + CLUSTER_BUY signal type |
""")
