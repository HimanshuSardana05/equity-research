"""Shared paths, config and helpers for the Nifty 500 research pipeline."""
from __future__ import annotations

import json
import math
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PRICES = RAW / "prices"          # one CSV per symbol: date,close,volume
FUND = RAW / "fundamentals"      # yfinance statements per symbol (JSON)
SCREENER = RAW / "screener"      # parsed Screener.in exports per symbol (JSON)
SCREENER_INBOX = ROOT / "data" / "screener_exports"  # drop .xlsx exports here
EVENTS = RAW / "events"
SITE = ROOT / "app"
SITE_DATA = SITE / "data"

for p in (PRICES, FUND, SCREENER, SCREENER_INBOX, EVENTS, SITE_DATA / "company"):
    p.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (personal research; nifty500-equity-app)"}
CRORE = 1e7

# ---- Valuation assumptions (edit here; documented in README) -----------------
CONFIG = {
    # India 10y G-sec yield (rupee risk-free rate). Update periodically.
    "risk_free": 0.065,
    # Damodaran-style total equity risk premium for India (mature ERP + country risk).
    "equity_risk_premium": 0.072,
    # Long-run nominal growth used for terminal value (must stay below risk-free).
    "terminal_growth": 0.055,
    "high_growth_years": 10,
    # Coffee Can (Saurabh Mukherjea): revenue growth > 10% and ROCE > 15% every year, 10 years.
    "cc_rev_growth": 0.10,
    "cc_roce": 0.15,
    "cc_years": 10,
    # Results are usually published within ~60 days of period end; avoid look-ahead.
    "reporting_lag_days": 60,
}

FINANCIAL_INDUSTRIES = {"Financial Services"}


def is_financial(industry: str | None) -> bool:
    return (industry or "") in FINANCIAL_INDUSTRIES


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def clean(o):
    """Make an object JSON safe (NaN/inf -> None, round floats)."""
    if isinstance(o, float):
        if math.isnan(o) or math.isinf(o):
            return None
        return round(o, 4)
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if hasattr(o, "item"):  # numpy scalar
        return clean(o.item())
    return o


def write_json(path: Path, obj, compact=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    txt = json.dumps(clean(obj), separators=(",", ":") if compact else None,
                     indent=None if compact else 1, ensure_ascii=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(txt)
    os.replace(tmp, path)


def today_ist() -> date:
    return datetime.now(timezone.utc).astimezone().date() if os.getenv("TZ") else \
        datetime.fromtimestamp(time.time() + 5.5 * 3600, tz=timezone.utc).date()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def yf_symbol(sym: str) -> str:
    return f"{sym}.NS"


def load_universe() -> list[dict]:
    return read_json(SITE_DATA / "universe.json", []) or []
