"""
Builds the human-readable evidence block for each signal.
Every signal surfaced to the user includes full reasoning — no signal
appears without the evidence that generated it.
"""

from datetime import date
from typing import Optional


RESEARCH_REFS = {
    "role_cfo":            "CFO: 21.5% avg annual return (highest of any role) — TipRanks/ResearchGate",
    "role_ceo":            "CEO: 19.3% avg annual return — TipRanks/ResearchGate",
    "role_director":       "Director: 20.7% avg annual return — TipRanks/ResearchGate",
    "role_coo":            "COO (officer): 19.8% avg annual return — TipRanks/ResearchGate",
    "role_officer":        "Named officer: 19.8% avg annual return — TipRanks/ResearchGate",
    "role_chairman":       "Chairman: strong operational visibility, similar to director",
    "role_other":          "Insider with company ownership stake",
    "cap_small":           "Small-cap: +7.4% abnormal return at 12 months — Lakonishok & Lee (2001)",
    "cap_mid":             "Mid-cap: moderate information asymmetry advantage",
    "cap_large":           "Large-cap: minimal alpha from insider signals in research",
    "cap_unknown":         "Market cap unknown; moderate uncertainty",
    "value_500k_plus":     "Transaction ≥$500K: high-conviction capital commitment",
    "value_100k_plus":     "Transaction ≥$100K: meaningful capital commitment",
    "first_purchase_12mo": "First purchase in 12+ months: non-routine, discretionary signal",
    "sequenced_buying_30d":"Sequenced buying: 2nd purchase within 30 days — extended informational advantage",
    "near_52wk_low":       "Purchasing within 10% of 52-week low: insider buying into weakness",
    "cluster":             "Cluster signal (3+ insiders, 14-day window): ~2× alpha vs single buy — multiple empirical studies",
}

HOLD_HORIZON = "60–90 days (Jeng, Metrick & Zeckhauser 2003 optimal window for opportunistic purchases)"


def fmt_currency(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    if val >= 1_000_000:
        return f"${val/1_000_000:.1f}M"
    if val >= 1_000:
        return f"${val/1_000:.0f}K"
    return f"${val:.2f}"


def fmt_pct(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    return f"{val:+.1f}%"


def build_evidence(
    ticker: str,
    company_name: str,
    score: int,
    signal_type: str,
    score_breakdown: dict,
    cluster_info: dict,
    transactions: list,   # list of scored transactions with owner info
    market_data: dict,
    filed_date: str,
    signal_date: date,
) -> dict:
    """
    Builds the full evidence dict stored in the signals table and
    used for both Telegram messages and the Streamlit dashboard.
    """
    # Collect research citations for factors that fired
    research_basis = []
    for factor in score_breakdown:
        if factor in RESEARCH_REFS:
            research_basis.append(RESEARCH_REFS[factor])
    if cluster_info.get("is_cluster"):
        research_basis.append(RESEARCH_REFS["cluster"])

    # Build per-insider summary from scored transactions (7-day window)
    insider_summaries = []
    scored_names = set()
    for tx in transactions:
        owner = tx.get("owner", {})
        t = tx.get("transaction", {})
        name = owner.get("name", "Unknown")
        scored_names.add(name)
        shares_before = (t.get("shares_after") or 0) - (t.get("shares") or 0)
        pct_increase = None
        if shares_before and shares_before > 0:
            pct_increase = (t.get("shares") or 0) / shares_before * 100

        insider_summaries.append({
            "name": name,
            "role": owner.get("role_category", "other").upper(),
            "role_raw": owner.get("role_raw", ""),
            "shares_bought": t.get("shares"),
            "price": t.get("price_per_share"),
            "total_value": t.get("total_value"),
            "shares_after": t.get("shares_after"),
            "pct_increase": pct_increase,
            "transaction_date": t.get("transaction_date"),
            "is_10b51": t.get("is_10b51", False),
            "in_scoring_window": True,
        })

    # For cluster signals: also include buyers from the 14-day window who
    # didn't appear in the 7-day scoring window so the display matches the cluster count
    if cluster_info.get("is_cluster"):
        for ci in cluster_info.get("insiders", []):
            name = ci.get("insider_name", "Unknown")
            if name in scored_names:
                continue
            insider_summaries.append({
                "name": name,
                "role": (ci.get("role_category") or "other").upper(),
                "role_raw": ci.get("role_category") or "",
                "shares_bought": float(ci["shares"]) if ci.get("shares") else None,
                "price": float(ci["price_per_share"]) if ci.get("price_per_share") else None,
                "total_value": float(ci["total_value"]) if ci.get("total_value") else None,
                "shares_after": None,
                "pct_increase": None,
                "transaction_date": ci.get("transaction_date"),
                "is_10b51": False,
                "in_scoring_window": False,
            })

    current_price = market_data.get("current_price")
    low_52wk = market_data.get("price_52wk_low")
    near_low = False
    pct_above_low = None
    if current_price and low_52wk and low_52wk > 0:
        pct_above_low = (current_price - low_52wk) / low_52wk * 100
        near_low = pct_above_low <= 10

    return {
        "ticker": ticker,
        "company_name": company_name,
        "score": score,
        "signal_type": signal_type,
        "score_breakdown": score_breakdown,
        "insiders": insider_summaries,
        "cluster": cluster_info,
        "market_cap": market_data.get("market_cap"),
        "cap_tier": market_data.get("cap_tier"),
        "current_price": current_price,
        "price_52wk_low": low_52wk,
        "pct_above_52wk_low": pct_above_low,
        "near_52wk_low": near_low,
        "filed_date": filed_date,
        "signal_date": signal_date.isoformat() if hasattr(signal_date, "isoformat") else str(signal_date),
        "research_basis": research_basis,
        "suggested_hold_horizon": HOLD_HORIZON,
    }


def format_telegram_message(evidence: dict) -> str:
    """Renders the signal as a Telegram HTML message optimised for mobile."""
    e = evidence
    sig_type = e.get("signal_type", "")
    score = e.get("score", 0)
    ticker = e.get("ticker", "")
    company = e.get("company_name", ticker)
    cluster = e.get("cluster", {})

    icon = {"CLUSTER_BUY": "🔴", "BUY": "🟢", "WATCH": "🟡"}.get(sig_type, "⚪")
    label = {"CLUSTER_BUY": "CLUSTER BUY", "BUY": "BUY SIGNAL", "WATCH": "WATCH"}.get(sig_type, sig_type)

    lines = [f"{icon} <b>{label} — ${ticker}</b>"]
    if company and company != ticker:
        lines.append(f"<i>{company}</i>")
    lines.append(f"Score <b>{score}</b>/100")
    lines.append("")

    # Cluster header
    if cluster.get("is_cluster"):
        n = cluster.get("insider_count", 0)
        lines.append(f"<b>👥 {n} insiders bought in {CLUSTER_WINDOW_DAYS_DISPLAY} days</b>")
    else:
        lines.append("<b>👤 Insider purchase</b>")

    # Buyer list
    insiders = e.get("insiders", [])
    for ins in insiders:
        name = ins.get("name", "Unknown")
        role = (ins.get("role_raw") or ins.get("role") or "").title()
        val = fmt_currency(ins.get("total_value"))
        price = f"${ins.get('price'):.2f}" if ins.get("price") else "N/A"
        shares = f"{int(ins.get('shares_bought') or 0):,}"
        note = " <i>(earlier)</i>" if not ins.get("in_scoring_window", True) else ""
        lines.append(f"  • <b>{name}</b> ({role})")
        lines.append(f"    {shares} sh @ {price} = {val}{note}")

    lines.append("")

    # Key context
    ctx = []
    cap = e.get("cap_tier")
    if cap and cap not in ("unknown", None):
        ctx.append(f"{cap.title()}-cap")
    if e.get("near_52wk_low"):
        pct = e.get("pct_above_52wk_low", 0)
        ctx.append(f"{pct:.0f}% above 52-wk low")
    if ctx:
        lines.append("📍 " + " · ".join(ctx))

    # Score factors — one per line, compact
    breakdown = e.get("score_breakdown", {})
    if breakdown:
        factor_parts = []
        for factor, pts in breakdown.items():
            if isinstance(pts, int):
                label_str = factor.replace("_", " ").title()
                factor_parts.append(f"{label_str} (+{pts})")
        lines.append("📊 " + " · ".join(factor_parts))

    lines.append("")
    lines.append(f"📅 Filed {e.get('filed_date')}")

    return "\n".join(lines)


CLUSTER_WINDOW_DAYS_DISPLAY = 14
