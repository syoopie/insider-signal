"""
One definition of "an insider's purchase on a day".

A Form 4 reports each broker fill as its own Table I line, so a single decision
to buy $2M of stock can arrive as five rows at five prices. Separately, a 4/A
amendment restates transactions the original already reported, under a new
accession number, so the same purchase can exist twice in `transactions`.

Both paths used `DISTINCT ON (insider_name, transaction_date, transaction_code)`,
which handles the amendment and mangles the tranches: it keeps one arbitrary
fill and throws the rest away. Across the stored history that discarded 1,550
insider-days and $2.97B of purchase value, understating `shares` for the
holdings-increase factor and pushing real buyers under the $25k cluster floor.

A third pattern cuts the other way. A joint Form 4 repeats one transaction once
per reporting owner, and since the parser records only the first owner those
arrive as identical rows under one name. Totalling those would multiply a real
purchase by the number of co-filers.

So the rule is: drop exact duplicates inside a filing, pick the newest filing
for a given (issuer, insider, date, code, ownership form), then total what
remains. Direct and indirect purchases stay separate rows because they are
different holdings, not tranches of one order.
"""

# Callers append their own WHERE clauses via {extra_where} and choose the
# columns they need. Placeholders are positional, so {extra_where} must use %s
# in the same order the caller passes them.
PURCHASE_ROLLUP_SQL = """
WITH deduped AS (
    -- A joint Form 4 repeats the SAME transaction once per reporting owner.
    -- Alyeska's 6 affiliated funds filed one purchase of 135,135 shares as six
    -- identical Table I lines. The parser keeps only the first reportingOwner,
    -- so all six land under one insider_name and summing them would sextuple a
    -- real purchase. Identical (shares, price) inside one filing is that
    -- pattern; genuine broker fills differ in share count or price, which is
    -- why they survive this and get totalled below.
    SELECT DISTINCT ON (t.filing_id, t.insider_name, t.transaction_date,
                        t.transaction_code, t.shares, t.price_per_share, t.is_direct)
        t.filing_id, t.insider_name, t.insider_role, t.role_category,
        t.transaction_date, t.transaction_code, t.shares, t.price_per_share,
        t.total_value, t.shares_after, t.is_10b51, t.is_direct, t.is_routine,
        f.filed_date, f.cik, c.ticker, c.cap_tier, c.name AS company_name
    FROM transactions t
    JOIN form4_filings f ON f.id = t.filing_id
    JOIN companies c ON c.cik = f.cik
    WHERE t.transaction_code = 'P'
      {extra_where}
    ORDER BY t.filing_id, t.insider_name, t.transaction_date, t.transaction_code,
             t.shares, t.price_per_share, t.is_direct, t.shares_after DESC NULLS LAST
),
ranked AS (
    SELECT *,
        first_value(filing_id) OVER (
            PARTITION BY cik, insider_name, transaction_date,
                         transaction_code, is_direct
            ORDER BY filed_date DESC, filing_id DESC
        ) AS winning_filing_id
    FROM deduped
)
SELECT
    ticker, cik, cap_tier, company_name,
    insider_name,
    max(insider_role)  AS insider_role,
    max(role_category) AS role_category,
    transaction_date, transaction_code, is_direct,
    max(filed_date)    AS filed_date,
    sum(shares)        AS shares,
    sum(total_value)   AS total_value,
    CASE WHEN sum(shares) > 0 AND sum(total_value) IS NOT NULL
         THEN sum(total_value) / sum(shares) END AS price_per_share,
    max(shares_after)  AS shares_after,
    bool_or(is_10b51)  AS is_10b51,
    bool_or(is_routine) AS is_routine
FROM ranked
WHERE filing_id = winning_filing_id
GROUP BY ticker, cik, cap_tier, company_name, insider_name,
         transaction_date, transaction_code, is_direct
"""


def purchase_rollup(extra_where: str = "") -> str:
    """The rollup query with caller-supplied filters spliced into the CTE."""
    return PURCHASE_ROLLUP_SQL.format(extra_where=extra_where)
