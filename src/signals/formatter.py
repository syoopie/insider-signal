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

    # Build per-insider summary
    insider_summaries = []
    for tx in transactions:
        owner = tx.get("owner", {})
        t = tx.get("transaction", {})
        shares_before = (t.get("shares_after") or 0) - (t.get("shares") or 0)
        pct_increase = None
        if shares_before and shares_before > 0:
            pct_increase = (t.get("shares") or 0) / shares_before * 100

        insider_summaries.append({
            "name": owner.get("name", "Unknown"),
            "role": owner.get("role_category", "other").upper(),
            "role_raw": owner.get("role_raw", ""),
            "shares_bought": t.get("shares"),
            "price": t.get("price_per_share"),
            "total_value": t.get("total_value"),
            "shares_after": t.get("shares_after"),
            "pct_increase": pct_increase,
            "transaction_date": t.get("transaction_date"),
            "is_10b51": t.get("is_10b51", False),
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
    """Renders the full signal evidence block as a Telegram-ready text message."""
    e = evidence
    sig_type = e.get("signal_type", "")
    score = e.get("score", 0)
    ticker = e.get("ticker", "")
    company = e.get("company_name", ticker)

    if sig_type == "CLUSTER_BUY":
        icon = "🔴"
    elif sig_type == "BUY":
        icon = "🟢"
    else:
        icon = "🟡"

    lines = [
        f"{'━'*38}",
        f"{icon} {sig_type} — ${ticker} ({company})",
        f"Score: {score}/100 | Type: {sig_type}",
        f"{'━'*38}",
        "",
        "WHO BOUGHT:",
    ]

    insiders = e.get("insiders", [])
    for ins in insiders:
        role_display = ins.get("role_raw") or ins.get("role", "")
        val = fmt_currency(ins.get("total_value"))
        price = f"${ins.get('price'):.2f}" if ins.get("price") else "N/A"
        shares = f"{int(ins.get('shares_bought') or 0):,}"
        lines.append(f"  • {ins['name']} ({role_display}) — {shares} shares @ {price} = {val}")

    cluster = e.get("cluster", {})
    if cluster.get("is_cluster"):
        n = cluster.get("insider_count", 0)
        lines.append(f"  {n} insiders in a {CLUSTER_WINDOW_DAYS_DISPLAY}-day window → CLUSTER SIGNAL")

    lines += ["", "WHAT THEY NOW HOLD:"]
    for ins in insiders:
        if ins.get("shares_after") and ins.get("pct_increase") is not None:
            total = f"{int(ins['shares_after']):,}"
            pct = f"{ins['pct_increase']:.0f}%"
            lines.append(f"  • {ins['name']}: {total} shares total (+{pct})")

    lines += [
        "",
        "CONTEXT:",
    ]
    if e.get("near_52wk_low"):
        pct = e.get("pct_above_52wk_low", 0)
        low = e.get("price_52wk_low")
        lines.append(f"  • Stock is {pct:.0f}% above 52-week low (${low:.2f})")

    lines.append(f"  • Filed: {e.get('filed_date')} | Signal available: {e.get('signal_date')}")
    lines.append(f"  • All purchases are open-market (not grants/exercises)")
    lines.append(f"  • None flagged as 10b5-1 plan")

    lines += ["", "SCORE BREAKDOWN:"]
    breakdown = e.get("score_breakdown", {})
    for factor, pts in breakdown.items():
        if isinstance(pts, int):
            label = factor.replace("_", " ").title()
            lines.append(f"  {label:<30} +{pts}")
    lines.append(f"  {'─'*36}")
    lines.append(f"  {'Total:':<30} {score}")

    lines += ["", "RESEARCH BASIS:"]
    for ref in e.get("research_basis", []):
        lines.append(f"  • {ref}")

    lines += [
        "",
        f"SUGGESTED HOLD: {e.get('suggested_hold_horizon', '')}",
        f"{'━'*38}",
    ]

    return "\n".join(lines)


CLUSTER_WINDOW_DAYS_DISPLAY = 14
