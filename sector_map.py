"""NSE screening universe grouped by sector.

The checked-in CSV is the official Nifty 500 constituent export.  Keeping the
source file in the repository makes a scan reproducible; replace it with a newer
official export when the index is rebalanced.
"""

import csv
from pathlib import Path

_FALLBACK_SECTOR_MAP = {
    # Financial services
    "HDFCBANK.NS": "Financial Services",
    "ICICIBANK.NS": "Financial Services",
    "SBIN.NS": "Financial Services",
    "AXISBANK.NS": "Financial Services",
    "KOTAKBANK.NS": "Financial Services",
    "BAJFINANCE.NS": "Financial Services",
    "BAJAJFINSV.NS": "Financial Services",
    "SBILIFE.NS": "Financial Services",
    "HDFCLIFE.NS": "Financial Services",
    "CHOLAFIN.NS": "Financial Services",
    # Information technology
    "TCS.NS": "Information Technology",
    "INFY.NS": "Information Technology",
    "HCLTECH.NS": "Information Technology",
    "WIPRO.NS": "Information Technology",
    "TECHM.NS": "Information Technology",
    "LTIM.NS": "Information Technology",
    "PERSISTENT.NS": "Information Technology",
    "COFORGE.NS": "Information Technology",
    # Energy
    "RELIANCE.NS": "Energy",
    "ONGC.NS": "Energy",
    "BPCL.NS": "Energy",
    "IOC.NS": "Energy",
    "GAIL.NS": "Energy",
    "OIL.NS": "Energy",
    "PETRONET.NS": "Energy",
    # Fast-moving consumer goods
    "HINDUNILVR.NS": "FMCG",
    "ITC.NS": "FMCG",
    "NESTLEIND.NS": "FMCG",
    "BRITANNIA.NS": "FMCG",
    "DABUR.NS": "FMCG",
    "MARICO.NS": "FMCG",
    "GODREJCP.NS": "FMCG",
    "COLPAL.NS": "FMCG",
    # Healthcare
    "SUNPHARMA.NS": "Healthcare",
    "DRREDDY.NS": "Healthcare",
    "CIPLA.NS": "Healthcare",
    "DIVISLAB.NS": "Healthcare",
    "APOLLOHOSP.NS": "Healthcare",
    "MAXHEALTH.NS": "Healthcare",
    "LUPIN.NS": "Healthcare",
    "AUROPHARMA.NS": "Healthcare",
    # Automobile
    "MARUTI.NS": "Automobile",
    "M&M.NS": "Automobile",
    "TATAMOTORS.NS": "Automobile",
    "BAJAJ-AUTO.NS": "Automobile",
    "EICHERMOT.NS": "Automobile",
    "HEROMOTOCO.NS": "Automobile",
    "TVSMOTOR.NS": "Automobile",
    "ASHOKLEY.NS": "Automobile",
    # Metals and mining
    "TATASTEEL.NS": "Metals & Mining",
    "HINDALCO.NS": "Metals & Mining",
    "JSWSTEEL.NS": "Metals & Mining",
    "VEDL.NS": "Metals & Mining",
    "NMDC.NS": "Metals & Mining",
    "HINDZINC.NS": "Metals & Mining",
    "NATIONALUM.NS": "Metals & Mining",
    # Industrials and infrastructure
    "LT.NS": "Industrials",
    "SIEMENS.NS": "Industrials",
    "ABB.NS": "Industrials",
    "BEL.NS": "Industrials",
    "HAL.NS": "Industrials",
    "BHEL.NS": "Industrials",
    "CUMMINSIND.NS": "Industrials",
    "POLYCAB.NS": "Industrials",
    # Consumer discretionary
    "TITAN.NS": "Consumer Discretionary",
    "ASIANPAINT.NS": "Consumer Discretionary",
    "TRENT.NS": "Consumer Discretionary",
    "DMART.NS": "Consumer Discretionary",
    "INDHOTEL.NS": "Consumer Discretionary",
    "JUBLFOOD.NS": "Consumer Discretionary",
    "PAGEIND.NS": "Consumer Discretionary",
    # Telecommunications
    "BHARTIARTL.NS": "Telecommunication",
    "INDUSTOWER.NS": "Telecommunication",
    "TATACOMM.NS": "Telecommunication",
    "IDEA.NS": "Telecommunication",
    "HFCL.NS": "Telecommunication",
    # Utilities
    "NTPC.NS": "Utilities",
    "POWERGRID.NS": "Utilities",
    "TATAPOWER.NS": "Utilities",
    "ADANIGREEN.NS": "Utilities",
    "NHPC.NS": "Utilities",
    "TORNTPOWER.NS": "Utilities",
    # Real estate
    "DLF.NS": "Real Estate",
    "GODREJPROP.NS": "Real Estate",
    "OBEROIRLTY.NS": "Real Estate",
    "PRESTIGE.NS": "Real Estate",
    "PHOENIXLTD.NS": "Real Estate",
}


def _load_nifty_500() -> dict[str, str]:
    source = Path(__file__).with_name("nifty500_constituents.csv")
    if not source.exists():
        return _FALLBACK_SECTOR_MAP
    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        universe = {
            f"{row['Symbol'].strip().upper()}.NS": row["Industry"].strip()
            for row in rows
            if row.get("Symbol") and row.get("Industry") and row.get("Series", "EQ") == "EQ"
        }
    return universe or _FALLBACK_SECTOR_MAP


SECTOR_MAP = _load_nifty_500()


def get_sector(symbol: str) -> str:
    return SECTOR_MAP.get(symbol.upper(), "Unknown")


def display_name(symbol: str) -> str:
    return (
        symbol.removesuffix(".NS")
        .removesuffix(".BO")
        .replace("-", " ")
    )
