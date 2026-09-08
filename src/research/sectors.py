"""
SIC code to sector ETF, so a purchase can be charged against its own industry.

The dataset has only ever carried `excess_spy` and `excess_iwm`. Both compare a
small-cap biotech to the whole market, which loads every biotech's label with
the sector's own move and calls the result insider alpha. Industry is also the
largest uncontrolled confound in the placebo control: it drew placebos at
random, so calendar and discount were matched and industry mix was not.

The map is a table of ranges rather than a chain of conditions, read in order so
a narrow range can sit in front of the wide one it lives inside. Pharmaceuticals
are 2833 to 2836 and belong to XLV even though the whole of 28 is chemicals, and
that is the shape of most of the exceptions here.
"""

from __future__ import annotations

from typing import Optional

# Checked in order. First match wins, so put the specific range first.
SIC_RANGES: tuple[tuple[int, int, str], ...] = (
    (100, 999, "XLP"),      # agriculture, forestry, fishing
    (1300, 1399, "XLE"),    # oil and gas extraction
    (1000, 1299, "XLB"),    # metal and coal mining
    (1400, 1499, "XLB"),    # nonmetallic minerals
    (1500, 1799, "XLI"),    # construction
    (2000, 2199, "XLP"),    # food and tobacco
    (2200, 2399, "XLY"),    # textiles and apparel
    (2400, 2699, "XLB"),    # lumber, furniture, paper
    (2700, 2799, "XLC"),    # printing and publishing
    (2833, 2836, "XLV"),    # pharmaceuticals and biologicals
    (2800, 2899, "XLB"),    # the rest of chemicals
    (2900, 2999, "XLE"),    # petroleum refining
    (3000, 3399, "XLB"),    # rubber, plastics, stone, primary metal
    (3400, 3499, "XLI"),    # fabricated metal
    (3570, 3579, "XLK"),    # computers and office equipment
    (3500, 3569, "XLI"),    # industrial machinery
    (3580, 3599, "XLI"),
    (3600, 3699, "XLK"),    # electronic and electrical equipment
    (3700, 3799, "XLI"),    # transportation equipment
    (3826, 3851, "XLV"),    # medical, dental and optical instruments
    (3800, 3825, "XLK"),    # measuring and control instruments
    (3852, 3899, "XLK"),
    (3900, 3999, "XLY"),    # miscellaneous manufacturing
    (4000, 4799, "XLI"),    # transportation
    (4800, 4899, "XLC"),    # communications
    (4900, 4999, "XLU"),    # electric, gas and sanitary services
    (5400, 5499, "XLP"),    # food stores
    (5000, 5399, "XLY"),    # wholesale and general retail
    (5500, 5999, "XLY"),
    (6500, 6599, "XLRE"),   # real estate
    (6798, 6798, "XLRE"),   # REITs
    (6000, 6799, "XLF"),    # the rest of finance and insurance
    (7300, 7399, "XLK"),    # business services, which is where software sits
    (7800, 7899, "XLC"),    # motion pictures
    (7000, 7299, "XLY"),    # hotels and personal services
    (7400, 7799, "XLY"),
    (7900, 7999, "XLY"),    # amusement and recreation
    (8000, 8099, "XLV"),    # health services
    (8731, 8731, "XLV"),    # commercial physical and biological research
    (8200, 8399, "XLY"),    # education and social services
    (8700, 8799, "XLI"),    # engineering, accounting, management
    (8900, 8999, "XLI"),
)


def sector_etf(sic_code: Optional[str]) -> Optional[str]:
    """
    The SPDR sector fund a SIC code belongs to, or None when nothing fits.

    None for public administration, for nonclassifiable establishments, and for
    the 146 companies EDGAR has no SIC for. Those rows get no sector label
    rather than a wrong one.
    """
    if not sic_code:
        return None
    digits = "".join(c for c in str(sic_code) if c.isdigit())
    if not digits:
        return None
    code = int(digits)
    for lo, hi, etf in SIC_RANGES:
        if lo <= code <= hi:
            return etf
    return None
