"""
The evidence blob stored on every signal: who bought, what, and why it scored.

Telegram renders it and the dashboard reads it. It holds only what can be
computed from stored data, so a signal carries the same evidence whichever
entrypoint built it. Today's price is not stored data, which is why there is no
"near its 52-week low right now" field here any more.
"""

from collections import defaultdict
from datetime import date
from typing import Optional

_ROLE_PRIORITY = {"cfo": 0, "ceo": 1, "chairman": 2, "director": 3, "coo": 4, "officer": 5, "other": 6}


RESEARCH_REFS = {
    "discount_rank": ("Bought this far below the stock's 52-week high: +11.13pp above "
                      "same-month, same-volatility peers at the top decile, median +7.39pp, "
                      "18 months out of sample. The same screen without an insider buying "
                      "has a median of -1.30pp."),
    "price_context_missing": ("Under a year of trading history, so there is no 52-week high to "
                              "measure against. Not ranked, and never alerted."),
}

CLUSTER_NOTE = ("Cluster (3+ insiders inside 14 days): decides the signal type and adds no "
                "points. Measured here, the number of buyers does not order returns.")

HOLD_HORIZON = "90 days, the horizon every published result here is measured on"


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


def _float(value) -> Optional[float]:
    return float(value) if value is not None else None


def _insider_summary(name: str, scored: list) -> dict:
    """One buyer, aggregated across every purchase they made in the window."""
    best_role = min(
        (s["owner"].get("role_category") or "other" for s in scored),
        key=lambda r: _ROLE_PRIORITY.get(r, 99),
    )
    role_raw = next((s["owner"]["role_raw"] for s in scored if s["owner"].get("role_raw")), "")
    txs = [s["transaction"] for s in scored]
    total_shares = sum(float(t.get("shares") or 0) for t in txs)
    total_value = sum(float(t.get("total_value") or 0) for t in txs)
    weighted = [(float(t.get("price_per_share") or 0), float(t.get("shares") or 0)) for t in txs]
    weighted = [(p, s) for p, s in weighted if p > 0 and s > 0]
    avg_price = sum(p * s for p, s in weighted) / sum(s for _, s in weighted) if weighted else None

    latest = max(scored, key=lambda s: str(s["transaction"].get("transaction_date") or ""))
    latest_tx = latest["transaction"]
    shares_after = latest_tx.get("shares_after")
    shares_before = float(shares_after or 0) - total_shares
    dates = sorted(str(t["transaction_date"]) for t in txs if t.get("transaction_date"))
    return {
        "name": name,
        "role": best_role.upper(),
        "role_category": best_role,
        "role_raw": role_raw,
        "shares_bought": total_shares,
        "price": avg_price,
        "total_value": total_value,
        "shares_after": _float(shares_after),
        "pct_increase": (total_shares / shares_before * 100) if shares_before > 0 else None,
        "pct_below_52wk_high": _float(latest_tx.get("pct_below_52wk_high")),
        "is_direct": all(t.get("is_direct") is not False for t in txs),
        "timing": latest["score_result"]["facts"]["timing"],
        "transaction_date": latest_tx.get("transaction_date"),
        "in_scoring_window": True,
        "purchase_count": len(scored),
        "date_range": (dates[0], dates[-1]) if len(dates) > 1 else None,
    }


def build_evidence(
    ticker: str,
    company_name: str,
    score: int,
    signal_type: str,
    score_breakdown: dict,
    cluster_info: dict,
    transactions: list,
    cap_tier: str,
    filed_date: date,
    signal_date: date,
) -> dict:
    """
    `transactions` is `ScoredWindow.scored_txs`: {"owner", "transaction",
    "score_result"} per eligible purchase in the scoring window.
    """
    research_basis = [RESEARCH_REFS[k] for k in score_breakdown if k in RESEARCH_REFS]
    if cluster_info.get("is_cluster"):
        research_basis.append(CLUSTER_NOTE)

    by_name = defaultdict(list)
    for scored in transactions:
        by_name[scored["owner"].get("name") or "Unknown"].append(scored)
    insiders = [_insider_summary(name, scored) for name, scored in by_name.items()]

    # A cluster reaches back 14 days and the scoring window 7, so a cluster can
    # hold buyers the window did not score. List them, or the count and the
    # table disagree.
    if cluster_info.get("is_cluster"):
        for ci in cluster_info.get("insiders", []):
            name = ci.get("insider_name") or "Unknown"
            if name in by_name:
                continue
            insiders.append({
                "name": name,
                "role": (ci.get("role_category") or "other").upper(),
                "role_category": ci.get("role_category") or "other",
                "role_raw": ci.get("role_category") or "",
                "shares_bought": _float(ci.get("shares")),
                "price": _float(ci.get("price_per_share")),
                "total_value": _float(ci.get("total_value")),
                "shares_after": None,
                "pct_increase": None,
                "pct_below_52wk_high": _float(ci.get("pct_below_52wk_high")),
                "is_direct": True,
                "timing": None,
                "transaction_date": ci.get("transaction_date"),
                "in_scoring_window": False,
            })

    return {
        "ticker": ticker,
        "company_name": company_name,
        "score": score,
        "signal_type": signal_type,
        "score_breakdown": score_breakdown,
        "insiders": insiders,
        "cluster": cluster_info,
        "cap_tier": cap_tier,
        "filed_date": filed_date.isoformat(),
        "signal_date": signal_date.isoformat(),
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

    def _fmt_date(d) -> str:
        if not d:
            return ""
        try:
            if hasattr(d, "strftime"):
                return d.strftime("%-m/%-d")
            from datetime import datetime
            return datetime.strptime(str(d)[:10], "%Y-%m-%d").strftime("%-m/%-d")
        except Exception:
            return str(d)[:10]

    insiders = e.get("insiders", [])
    if cluster.get("is_cluster"):
        n = cluster.get("insider_count", 0)
        total_spent = sum(float(ins.get("total_value") or 0) for ins in insiders)
        buy_dates = sorted(set(
            _fmt_date(ins.get("transaction_date")) for ins in insiders
            if ins.get("transaction_date")
        ))
        date_span = f" · {buy_dates[0]}–{buy_dates[-1]}" if len(buy_dates) > 1 else (f" · {buy_dates[0]}" if buy_dates else "")
        lines.append(f"<b>👥 {n} insiders · {fmt_currency(total_spent)} total{date_span}</b>")
    else:
        lines.append("<b>👤 Insider purchase</b>")

    for ins in insiders:
        name = ins.get("name", "Unknown")
        role = (ins.get("role_raw") or ins.get("role") or "").title()
        val = fmt_currency(ins.get("total_value"))
        shares = f"{int(ins.get('shares_bought') or 0):,}"
        note = " <i>(earlier)</i>" if not ins.get("in_scoring_window", True) else ""
        count = ins.get("purchase_count", 1)
        count_str = f" · {count}×" if count > 1 else ""
        if ins.get("price"):
            price_label = "avg" if count > 1 else "@"
            price_str = f" {price_label} ${ins['price']:.2f}"
        else:
            price_str = ""
        date_str = f" · {_fmt_date(ins.get('transaction_date'))}" if ins.get("transaction_date") else ""
        lines.append(f"  • <b>{name}</b> ({role}){count_str}{date_str}")
        lines.append(f"    {shares} sh{price_str} = {val}{note}")

    lines.append("")

    ctx = []
    cap = e.get("cap_tier")
    if cap and cap != "unknown":
        ctx.append(f"{cap.title()}-cap")
    discounts = [ins["pct_below_52wk_high"] for ins in insiders
                 if ins.get("pct_below_52wk_high") is not None]
    if discounts:
        ctx.append(f"{max(discounts):.0f}% below 52-wk high")
    if ctx:
        lines.append("📍 " + " · ".join(ctx))

    lines.append("")
    lines.append(f"📅 Filed {e.get('filed_date')}")

    return "\n".join(lines)
