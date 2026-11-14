"""Fundamentals.

Sources, in order of preference for any given fiscal year:
1. Screener.in Excel exports that the user downloads themselves (10-12 years). We never scrape
   screener.in: its terms do not allow automated access. See screener_import.py.
2. Yahoo Finance via yfinance (about 4 annual years + 5 quarters, plus analyst estimates).

Everything is normalised to Rs crore and keyed by fiscal year (year of the period end).
"""
from __future__ import annotations

import math
import time

import pandas as pd

from common import CRORE, FUND, SCREENER, now_iso, read_json, write_json, yf_symbol

# normalised field -> candidate yfinance row labels (first non-null wins)
YF_MAP = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "ebit": ["EBIT", "Operating Income"],
    "pbt": ["Pretax Income"],
    "interest": ["Interest Expense", "Interest Expense Non Operating"],
    "depreciation": ["Reconciled Depreciation", "Depreciation And Amortization"],
    "tax": ["Tax Provision"],
    "net_income": ["Net Income Common Stockholders", "Net Income"],
    "eps": ["Diluted EPS", "Basic EPS"],
    "equity": ["Stockholders Equity", "Common Stock Equity"],
    "debt": ["Total Debt"],
    "cash": ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"],
    "total_assets": ["Total Assets"],
    "current_liabilities": ["Current Liabilities"],
    "net_block": ["Net PPE"],
    "inventory": ["Inventory"],
    "receivables": ["Accounts Receivable", "Receivables"],
    "shares": ["Ordinary Shares Number", "Share Issued"],
    "cfo": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "capex": ["Capital Expenditure"],
    "fcf": ["Free Cash Flow"],
    "dividends": ["Cash Dividends Paid", "Common Stock Dividend Paid"],
}
NOT_CRORE = {"eps", "shares"}


def _frames_to_years(frames: list[pd.DataFrame]) -> dict:
    out: dict[str, dict] = {}
    for df in frames:
        if df is None or df.empty:
            continue
        for col in df.columns:
            ts = pd.Timestamp(col)
            key = ts.strftime("%Y-%m-%d")
            rec = out.setdefault(key, {"period_end": key})
            for field, labels in YF_MAP.items():
                if rec.get(field) is not None:
                    continue
                for lab in labels:
                    if lab in df.index:
                        v = df.at[lab, col]
                        if pd.notna(v):
                            v = float(v)
                            rec[field] = v if field in NOT_CRORE else v / CRORE
                            break
    for rec in out.values():
        for k in ("capex", "dividends"):
            if rec.get(k) is not None:
                rec[k] = abs(rec[k])
        if rec.get("fcf") is None and rec.get("cfo") is not None and rec.get("capex") is not None:
            rec["fcf"] = rec["cfo"] - rec["capex"]
        if rec.get("ebitda") is None and rec.get("ebit") is not None and rec.get("depreciation") is not None:
            rec["ebitda"] = rec["ebit"] + rec["depreciation"]
    return dict(sorted(out.items()))


def _est_table(df):
    if df is None or getattr(df, "empty", True):
        return None
    df = df.copy()
    for c in df.columns:
        if c in ("avg", "low", "high", "yearAgoEps", "yearAgoRevenue") and "revenue" in str(df.columns).lower():
            pass
    recs = {}
    for idx, row in df.iterrows():
        recs[str(idx)] = {k: (float(v) if isinstance(v, (int, float)) and pd.notna(v) else (None if pd.isna(v) else str(v)))
                          for k, v in row.items()}
    return recs


def fetch_yf_fundamentals(sym: str) -> dict | None:
    import yfinance as yf
    t = yf.Ticker(yf_symbol(sym))
    try:
        annual = _frames_to_years([t.income_stmt, t.balance_sheet, t.cashflow])
    except Exception as e:
        print("annual fail", sym, e)
        annual = {}
    try:
        quarterly = _frames_to_years([t.quarterly_income_stmt, t.quarterly_balance_sheet])
    except Exception:
        quarterly = {}
    info = {}
    try:
        raw = t.info or {}
        for k in ("sector", "industry", "longBusinessSummary", "beta", "sharesOutstanding",
                  "marketCap", "trailingPE", "forwardPE", "priceToBook", "enterpriseToEbitda",
                  "dividendYield", "heldPercentInsiders", "heldPercentInstitutions",
                  "earningsGrowth", "revenueGrowth", "website", "fullTimeEmployees",
                  "recommendationKey", "targetMeanPrice", "numberOfAnalystOpinions", "financialCurrency"):
            if raw.get(k) is not None:
                info[k] = raw[k]
    except Exception:
        pass
    est = {}
    for attr, key in (("earnings_estimate", "eps"), ("revenue_estimate", "revenue"),
                      ("growth_estimates", "growth")):
        try:
            est[key] = _est_table(getattr(t, attr))
        except Exception:
            pass
    try:
        cal = t.calendar or {}
        ed = cal.get("Earnings Date")
        est["next_earnings"] = [str(d) for d in ed] if isinstance(ed, list) else (str(ed) if ed else None)
    except Exception:
        pass
    try:
        hist = t.earnings_dates
        if hist is not None and not hist.empty:
            est["surprises"] = [
                {"date": str(i.date()), "estimate": None if pd.isna(r.get("EPS Estimate")) else float(r["EPS Estimate"]),
                 "reported": None if pd.isna(r.get("Reported EPS")) else float(r["Reported EPS"]),
                 "surprise_pct": None if pd.isna(r.get("Surprise(%)")) else float(r["Surprise(%)"])}
                for i, r in hist.head(12).iterrows()]
    except Exception:
        pass
    if not annual and not info:
        return None
    # Some Indian companies (e.g. Infosys) report to Yahoo in USD: convert statements to INR.
    cur = (info.get("financialCurrency") or "INR").upper()
    fx = 1.0
    if cur != "INR":
        try:
            fx = float(yf.Ticker(f"{cur}INR=X").history(period="5d")["Close"].dropna().iloc[-1])
        except Exception as e:
            print("fx fail", sym, cur, e)
            fx = None
        # Yahoo's currency flag is not always right (HCLTECH is flagged USD but reported in INR).
        # Check against Yahoo's own market cap / trailing PE and keep whichever scale fits.
        try:
            target = float(info["marketCap"]) / float(info["trailingPE"]) / CRORE
            raw = next(r["net_income"] for _, r in sorted(annual.items(), reverse=True) if r.get("net_income"))
            if fx and raw > 0 and abs(math.log(raw / target)) < abs(math.log(raw * fx / target)):
                fx, cur = 1.0, "INR"
        except Exception:
            pass
        if fx and fx != 1.0:
            for block in (annual, quarterly):
                for rec in block.values():
                    for k, v in list(rec.items()):
                        if k not in NOT_CRORE and k != "period_end" and isinstance(v, (int, float)):
                            rec[k] = v * fx
                    if rec.get("eps") is not None:
                        rec["eps"] *= fx
    return {"symbol": sym, "fetched": now_iso(), "currency": cur, "fx_to_inr": fx, "annual": annual, "quarterly": quarterly,
            "info": info, "estimates": est}


def refresh_fundamentals(symbols: list[str], workers=6) -> list[str]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def one(s):
        for attempt in range(3):
            try:
                d = fetch_yf_fundamentals(s)
                if d:
                    write_json(FUND / f"{s}.json", d)
                    return s
                return None
            except Exception as e:
                print("fund fail", s, attempt, e)
                time.sleep(2 + attempt * 5)
        return None

    done = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, s) for s in symbols]
        for i, f in enumerate(as_completed(futs)):
            if f.result():
                done.append(f.result())
            if i % 25 == 0:
                print(f"fundamentals {i}/{len(symbols)}", flush=True)
    return done


def fiscal_years(sym: str) -> tuple[dict, dict]:
    """Merged annual fundamentals keyed by fiscal year (int) and the source of each year."""
    years, source = {}, {}
    yfd = read_json(FUND / f"{sym}.json", {}) or {}
    for pe, rec in (yfd.get("annual") or {}).items():
        fy = int(pe[:4]) if int(pe[5:7]) >= 3 else int(pe[:4])
        years[fy] = dict(rec)
        source[fy] = "yahoo"
    scr = read_json(SCREENER / f"{sym}.json", {}) or {}
    for pe, rec in (scr.get("annual") or {}).items():
        fy = int(pe[:4])
        merged = dict(years.get(fy, {}))
        merged.update({k: v for k, v in rec.items() if v is not None})
        years[fy] = merged
        source[fy] = "screener"
    return dict(sorted(years.items())), source
