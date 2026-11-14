"""Turn raw prices + fundamentals into the site's data: valuation history, quality tests,
over/undervaluation verdicts with reasons, sector peer analytics and factor tables.

Logic hierarchy (research aid, not investment advice):
1. Coffee Can (Saurabh Mukherjea): revenue growth >10% and ROCE >15% in EVERY year for 10 years.
2. Buffett-style quality: durable high ROE, low debt, cash conversion, retained-earnings test.
3. Damodaran-style valuation: reverse DCF (what growth does the price imply?), an intrinsic
   value range, and relative valuation vs own history and vs sector peers adjusted for quality.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta

import numpy as np
import pandas as pd

from common import CONFIG, FUND, SITE_DATA, is_financial, now_iso, read_json, write_json
from events import load_events
from fundamentals import fiscal_years
from prices import load_prices
from levels import compute_levels_full, precompute


def _f(x):
    try:
        x = float(x)
        return None if math.isnan(x) or math.isinf(x) else x
    except (TypeError, ValueError):
        return None


def _div(a, b):
    a, b = _f(a), _f(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def _cagr(a, b, years):
    a, b = _f(a), _f(b)
    if a is None or b is None or a <= 0 or b <= 0 or years <= 0:
        return None
    return (b / a) ** (1 / years) - 1


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


# --------------------------------------------------------------------------- annual metrics
def annual_metrics(years: dict, fin: bool) -> list[dict]:
    rows, prev = [], None
    for fy, r in years.items():
        if _f(r.get("revenue")) is None and _f(r.get("net_income")) is None:
            continue  # balance-sheet-only stub years carry no usable signal
        eq, debt = _f(r.get("equity")), _f(r.get("debt")) or 0.0
        ce = (eq + debt) if eq is not None else None
        pce = None
        if prev and _f(prev.get("equity")) is not None:
            pce = prev["equity"] + (_f(prev.get("debt")) or 0.0)
        avg_ce = (ce + pce) / 2 if ce is not None and pce is not None else ce
        avg_eq = (eq + prev["equity"]) / 2 if eq is not None and prev and _f(prev.get("equity")) else eq
        rev, ni, ebit, ebitda = (_f(r.get(k)) for k in ("revenue", "net_income", "ebit", "ebitda"))
        fcf = _f(r.get("fcf"))
        if fcf is None:
            fcf = _f(r.get("fcf_proxy"))
        row = {
            "fy": fy, "period_end": r.get("period_end"),
            "revenue": rev, "ebitda": ebitda, "ebit": ebit, "net_income": ni,
            "eps": _f(r.get("eps")), "equity": eq, "debt": debt, "cash": _f(r.get("cash")),
            "fcf": fcf, "cfo": _f(r.get("cfo")), "dividends": _f(r.get("dividends")),
            "total_assets": _f(r.get("total_assets")), "shares": _f(r.get("shares")),
            "roce": None if fin else _div(ebit, avg_ce),
            "roe": _div(ni, avg_eq),
            "opm": None if fin else _div(ebitda, rev),
            "npm": _div(ni, rev),
            "de": _div(debt, eq) if not fin else None,
            "asset_turnover": _div(rev, r.get("total_assets")),
            "leverage": _div(r.get("total_assets"), eq),
            "interest_cover": None if fin else _div(ebit, r.get("interest")),
            "cash_conversion": _div(r.get("cfo"), ni),
            "payout": _div(r.get("dividends"), ni),
            "rev_growth": _div(rev, prev.get("revenue")) - 1 if prev and _div(rev, prev.get("revenue")) else None,
            "ni_growth": (_div(ni, prev.get("net_income")) - 1) if prev and _f(prev.get("net_income")) and prev["net_income"] > 0 and ni is not None else None,
        }
        rows.append(row)
        prev = {**r, "equity": eq, "debt": debt}
    return rows


def ttm_snapshot(sym: str) -> dict | None:
    """Trailing-twelve-month figures from the latest 4 consecutive quarters (if available)."""
    q = (read_json(FUND / f"{sym}.json", {}) or {}).get("quarterly") or {}
    items = sorted(q.items())
    if len(items) < 4:
        return None
    for end in range(len(items), 3, -1):
        win = items[end - 4:end]
        dates = [pd.Timestamp(k) for k, _ in win]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        if all(80 <= g <= 100 for g in gaps) and all(_f(v.get("net_income")) is not None for _, v in win):
            last = win[-1][1]
            snap = {"period_end": win[-1][0], "ttm": True}
            for k in ("revenue", "net_income", "ebitda", "ebit"):
                vals = [_f(v.get(k)) for _, v in win]
                snap[k] = sum(vals) if all(v is not None for v in vals) else None
            for k in ("equity", "debt", "cash"):
                bs = [_f(v.get(k)) for _, v in items if _f(v.get(k)) is not None]
                snap[k] = _f(last.get(k)) if _f(last.get(k)) is not None else (bs[-1] if bs else None)
            return snap
    return None


# --------------------------------------------------------------------------- valuation series
def valuation_series(prices: pd.DataFrame, annual: list[dict], ttm: dict | None, shares_now: float | None):
    if prices.empty or not shares_now:
        return None
    wk = prices["close"].resample("W-FRI").last().dropna()
    lag = timedelta(days=CONFIG["reporting_lag_days"])
    snaps = []
    for a in annual:
        if a.get("period_end"):
            snaps.append((pd.Timestamp(a["period_end"]) + lag, a))
    if ttm:
        prev = [s for s in snaps if pd.Timestamp(s[1]["period_end"]) < pd.Timestamp(ttm["period_end"])]
        base = prev[-1][1] if prev else {}
        snaps.append((pd.Timestamp(ttm["period_end"]) + timedelta(days=45),
                      {**base, **{k: v for k, v in ttm.items() if v is not None}}))
    snaps.sort(key=lambda s: s[0])
    if not snaps:
        return None
    out = defaultdict(list)
    j = -1
    for d, close in wk.items():
        while j + 1 < len(snaps) and snaps[j + 1][0] <= d:
            j += 1
        if j < 0:
            continue
        a = snaps[j][1]
        mcap = close * shares_now / 1e7
        ev = mcap + (_f(a.get("debt")) or 0) - (_f(a.get("cash")) or 0)
        ni, eq, ebitda, rev = (_f(a.get(k)) for k in ("net_income", "equity", "ebitda", "revenue"))
        out["t"].append(d.strftime("%Y-%m-%d"))
        out["close"].append(close)
        out["mcap"].append(mcap)
        out["pe"].append(mcap / ni if ni and ni > 0 else None)
        out["pb"].append(mcap / eq if eq and eq > 0 else None)
        out["ev_ebitda"].append(ev / ebitda if ebitda and ebitda > 0 else None)
        out["ps"].append(mcap / rev if rev and rev > 0 else None)
        out["ey"].append(ni / mcap * 100 if ni is not None and mcap else None)
    return dict(out) if out else None


# --------------------------------------------------------------------------- DCF
def cost_of_equity(beta):
    b = _clip(_f(beta) or 1.0, 0.6, 1.6)
    return CONFIG["risk_free"] + b * CONFIG["equity_risk_premium"]


def fcfe_value(e0, g, roe, r, n=None, gt=None):
    """Two-stage FCFE (Damodaran): reinvestment = g / ROE, so growth has to be paid for."""
    n = n or CONFIG["high_growth_years"]
    gt = gt if gt is not None else CONFIG["terminal_growth"]
    roe = max(roe, r + 0.02)
    roe_t = max(r + 0.01, (roe + r) / 2)
    pv, e = 0.0, e0
    for t in range(1, n + 1):
        e *= 1 + g
        pv += e * (1 - _clip(g / roe, 0, 1)) / (1 + r) ** t
    tv = e * (1 + gt) * (1 - gt / roe_t) / (r - gt)
    return pv + tv / (1 + r) ** n


def implied_growth(mcap, e0, roe, r):
    lo, hi = -0.30, 0.80
    if fcfe_value(e0, hi, roe, r) < mcap:
        return hi
    if fcfe_value(e0, lo, roe, r) > mcap:
        return lo
    for _ in range(60):
        mid = (lo + hi) / 2
        if fcfe_value(e0, mid, roe, r) < mcap:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# --------------------------------------------------------------------------- tests
def coffee_can(annual: list[dict], fin: bool) -> dict:
    need = CONFIG["cc_years"]
    rows = [a for a in annual if a.get("rev_growth") is not None or a.get("roce" if not fin else "roe") is not None]
    rows = rows[-need:]
    metric = "roe" if fin else "roce"
    detail = []
    for a in rows:
        g, q = a.get("rev_growth"), a.get(metric)
        detail.append({"fy": a["fy"], "rev_growth": g, "quality": q,
                       "growth_ok": None if g is None else g > CONFIG["cc_rev_growth"],
                       "quality_ok": None if q is None else q > CONFIG["cc_roce"]})
    checked = [d for d in detail if d["growth_ok"] is not None and d["quality_ok"] is not None]
    fails = [d["fy"] for d in checked if not (d["growth_ok"] and d["quality_ok"])]
    n = len(checked)
    if n == 0:
        status = "No data"
    elif fails:
        status = "Fail"
    elif n >= need:
        status = "Pass"
    else:
        status = "Pass so far"
    return {"status": status, "years_checked": n, "years_needed": need, "failed_years": fails,
            "metric": metric.upper(), "detail": detail,
            "note": ("Coffee Can for lenders uses ROE in place of ROCE (approximation)." if fin else None)}


def buffett_checks(annual: list[dict], fin: bool, mcap_series: list | None, val_t: list | None) -> list[dict]:
    last = annual[-5:]
    roes = [a["roe"] for a in last if a.get("roe") is not None]
    opms = [a["opm"] for a in last if a.get("opm") is not None]
    fcfs = [a["fcf"] for a in last if a.get("fcf") is not None]
    out = []
    if roes:
        out.append({"name": "Durable ROE above 15%", "pass": min(roes) > 0.15,
                    "value": f"avg {np.mean(roes)*100:.1f}%, min {min(roes)*100:.1f}%"})
    if not fin and annual and annual[-1].get("de") is not None:
        de = annual[-1]["de"]
        out.append({"name": "Low debt (D/E < 0.5)", "pass": de < 0.5, "value": f"{de:.2f}x"})
    if opms and len(opms) >= 3:
        sd = float(np.std(opms))
        out.append({"name": "Stable margins (moat signal)", "pass": sd < 0.04,
                    "value": f"OPM {np.mean(opms)*100:.1f}% ± {sd*100:.1f}pp"})
    if fcfs and not fin:
        pos = sum(1 for x in fcfs if x > 0)
        out.append({"name": "Generates free cash", "pass": pos == len(fcfs), "value": f"{pos}/{len(fcfs)} years positive"})
    conv = [a["cash_conversion"] for a in last if a.get("cash_conversion") is not None]
    if conv and not fin:
        out.append({"name": "Profits convert to cash (CFO/PAT > 0.8)", "pass": np.median(conv) > 0.8,
                    "value": f"median {np.median(conv):.2f}"})
    # Retained-earnings ("one-dollar") test
    if mcap_series and len(annual) >= 3:
        retained = sum((a.get("net_income") or 0) - (a.get("dividends") or 0) for a in annual[1:])
        start = next((m for m in mcap_series if m), None)
        if retained > 0 and start:
            created = mcap_series[-1] - start
            out.append({"name": "Each ₹1 retained created ≥ ₹1 of market value", "pass": created >= retained,
                        "value": f"₹{created/retained:.2f} per ₹1 retained"})
    return out


def decompose_returns(val: dict | None) -> dict | None:
    if not val:
        return None
    pairs = [(t, c, pe) for t, c, pe in zip(val["t"], val["close"], val["pe"]) if pe and pe > 0]
    if len(pairs) < 20:
        return None
    t0, c0, pe0 = pairs[0]
    t1, c1, pe1 = pairs[-1]
    yrs = (pd.Timestamp(t1) - pd.Timestamp(t0)).days / 365.25
    if yrs < 1:
        return None
    lp, lpe = math.log(c1 / c0), math.log(pe1 / pe0)
    le = lp - lpe
    return {"from": t0, "to": t1, "years": round(yrs, 1),
            "price_cagr": math.exp(lp / yrs) - 1, "earnings_cagr": math.exp(le / yrs) - 1,
            "rerating_cagr": math.exp(lpe / yrs) - 1, "pe_from": pe0, "pe_to": pe1,
            "share_from_earnings": (le / lp) if abs(lp) > 1e-6 else None}


def percentile(series, x):
    s = [v for v in series if v is not None]
    if x is None or len(s) < 10:
        return None
    return sum(1 for v in s if v <= x) / len(s)


# --------------------------------------------------------------------------- main build
def build_company(u: dict, events_by_sym: dict) -> dict | None:
    sym, fin = u["symbol"], is_financial(u["industry"])
    prices = load_prices(sym)
    years, source = fiscal_years(sym)
    yfd = read_json(FUND / f"{sym}.json", {}) or {}
    info, est = yfd.get("info") or {}, yfd.get("estimates") or {}
    annual = annual_metrics(years, fin)
    ttm = ttm_snapshot(sym)
    if ttm and annual and annual[-1].get("revenue") and ttm.get("revenue"):
        ratio = ttm["revenue"] / annual[-1]["revenue"]
        if not (0.5 <= ratio <= 2):  # quarterly data inconsistent with annual (units/currency): don't trust it
            ttm = None
    shares_now = _f(info.get("sharesOutstanding")) or next(
        (a["shares"] for a in reversed(annual) if a.get("shares")), None)
    val = valuation_series(prices, annual, ttm, shares_now)

    price = float(prices["close"].iloc[-1]) if len(prices) else None
    last_date = prices.index[-1].strftime("%Y-%m-%d") if len(prices) else None
    mcap = price * shares_now / 1e7 if price and shares_now else None
    latest = annual[-1] if annual else {}
    cur = (ttm or {})
    ni_now = _f(cur.get("net_income")) if cur.get("net_income") is not None else latest.get("net_income")
    eq_now = _f(cur.get("equity")) or latest.get("equity")

    def ret(days):
        if len(prices) < 2:
            return None
        past = prices["close"][prices.index <= prices.index[-1] - pd.Timedelta(days=days)]
        return price / float(past.iloc[-1]) - 1 if len(past) else None

    def cagr_n(field, n):
        rows = [a for a in annual if a.get(field) is not None]
        if len(rows) < 2:
            return None
        n = min(n, rows[-1]["fy"] - rows[0]["fy"])
        base = next((a for a in rows if a["fy"] == rows[-1]["fy"] - n), None)
        return _cagr(base[field], rows[-1][field], n) if base and n > 0 else None

    roes = [a["roe"] for a in annual[-3:] if a.get("roe") is not None]
    roe_avg = float(np.mean(roes)) if roes else None
    r = cost_of_equity(info.get("beta"))

    m = {
        "price": price, "price_date": last_date, "mcap": mcap,
        "pe": (mcap / ni_now) if mcap and ni_now and ni_now > 0 else None,
        "pb": (mcap / eq_now) if mcap and eq_now and eq_now > 0 else None,
        "ev_ebitda": val["ev_ebitda"][-1] if val and val.get("ev_ebitda") else None,
        "roce": latest.get("roce"), "roe": latest.get("roe"), "opm": latest.get("opm"),
        "npm": latest.get("npm"), "de": latest.get("de"),
        "rev_cagr3": cagr_n("revenue", 3), "ni_cagr3": cagr_n("net_income", 3),
        "rev_growth": latest.get("rev_growth"),
        "asset_turnover": latest.get("asset_turnover"), "leverage": latest.get("leverage"),
        "fcf_yield": (latest.get("fcf") / mcap) if mcap and latest.get("fcf") is not None and not fin else None,
        "div_yield": (latest.get("dividends") / mcap) if mcap and latest.get("dividends") else None,
        "ret_1y": ret(365), "ret_3y": ret(365 * 3), "ret_5y": ret(365 * 5),
        "cost_of_equity": r, "beta": _f(info.get("beta")),
        "years_of_data": len(annual),
    }
    m["ey"] = 1 / m["pe"] if m["pe"] else None

    # expected growth: analyst +1y EPS growth, 3y revenue CAGR and 3y profit CAGR, blended & capped
    g_inputs = []
    try:
        ag = _f(((est.get("eps") or {}).get("+1y") or {}).get("growth"))
        if ag is not None:
            g_inputs.append(("analyst EPS growth (next FY)", ag))
    except Exception:
        pass
    for lab, v in (("3y revenue CAGR", m["rev_cagr3"]), ("3y profit CAGR", m["ni_cagr3"])):
        if v is not None:
            g_inputs.append((lab, v))
    g_exp = _clip(float(np.median([v for _, v in g_inputs])), -0.05, 0.25) if g_inputs else None

    dcf = None
    if mcap and ni_now and ni_now > 0 and roe_avg:
        roe_use = _clip(roe_avg, 0.05, 0.45)
        ig = implied_growth(mcap, ni_now, roe_use, r)
        dcf = {"implied_growth": ig, "roe_used": roe_use, "cost_of_equity": r,
               "years": CONFIG["high_growth_years"], "terminal_growth": CONFIG["terminal_growth"],
               "expected_growth": g_exp, "growth_inputs": g_inputs}
        if g_exp is not None and shares_now:
            per_share = lambda g: fcfe_value(ni_now, g, roe_use, r) * 1e7 / shares_now
            dcf["fair_value"] = {"bear": per_share(g_exp - 0.04), "base": per_share(g_exp),
                                 "bull": per_share(g_exp + 0.04)}
            dcf["upside"] = dcf["fair_value"]["base"] / price - 1 if price else None
    m["implied_growth"] = dcf["implied_growth"] if dcf else None
    m["upside"] = dcf.get("upside") if dcf else None

    hist = {}
    if val:
        for k in ("pe", "pb", "ev_ebitda"):
            s = [v for v in val[k] if v is not None]
            if s:
                hist[k] = {"median": float(np.median(s)), "min": min(s), "max": max(s),
                           "pct": percentile(val[k], m.get(k)),
                           "since": val["t"][0]}
    m["pe_pct"] = (hist.get("pe") or {}).get("pct")

    company = {
        "symbol": sym, "name": u["name"], "industry": u["industry"], "isin": u["isin"],
        "financial": fin, "yahoo_sector": info.get("sector"), "yahoo_industry": info.get("industry"),
        "about": (info.get("longBusinessSummary") or "")[:900], "website": info.get("website"),
        "metrics": m, "history_stats": hist,
        "annual": annual, "ttm": ttm, "sources": {str(k): v for k, v in source.items()},
        "price": {"t": [d.strftime("%Y-%m-%d") for d in prices.index], "c": prices["close"].round(2).tolist(),
                  "o": prices["open"].round(2).tolist(), "h": prices["high"].round(2).tolist(),
                  "l": prices["low"].round(2).tolist()},
        "levels": compute_levels_full(sym, prices),
        "val": val, "dcf": dcf,
        "coffee_can": coffee_can(annual, fin),
        "buffett": buffett_checks(annual, fin, val["mcap"] if val else None, val["t"] if val else None),
        "decomposition": decompose_returns(val),
        "estimates": {k: est.get(k) for k in ("eps", "revenue", "next_earnings", "surprises")},
        "analyst": {k: info.get(k) for k in ("recommendationKey", "targetMeanPrice", "numberOfAnalystOpinions")},
        "events": sorted(events_by_sym.get(sym, []), key=lambda e: e["date"], reverse=True)[:60],
    }
    return company


def sector_regression(rows: list[dict]):
    """log(PE) ~ quality + growth across sector peers -> 'justified' PE for each company."""
    pts = [(r, math.log(r["pe"]), r.get("roce") if r.get("roce") is not None else r.get("roe"), r.get("rev_cagr3"))
           for r in rows if r.get("pe") and 0 < r["pe"] < 300]
    pts = [p for p in pts if p[2] is not None and p[3] is not None]
    if len(pts) < 6:
        return {}
    y = np.array([p[1] for p in pts])
    X = np.column_stack([np.ones(len(pts)), [_clip(p[2], -0.2, 0.8) for p in pts], [_clip(p[3], -0.3, 0.6) for p in pts]])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    out = {}
    for (r, ly, q, g) in pts:
        pred = beta[0] + beta[1] * _clip(q, -0.2, 0.8) + beta[2] * _clip(g, -0.3, 0.6)
        out[r["symbol"]] = {"justified_pe": math.exp(pred), "residual": ly - pred}
    return out


def spearman(a, b):
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if len(pairs) < 6:
        return None
    xa = pd.Series([p[0] for p in pairs]).rank()
    xb = pd.Series([p[1] for p in pairs]).rank()
    c = xa.corr(xb)
    return None if pd.isna(c) else float(c)


def verdict(c: dict, sec: dict, reg: dict) -> dict:
    """Blend three valuation signals; each is weighted by how much evidence stands behind it.

    +ve signal = expensive, -ve = cheap.
      DCF       (weight 0.45): price-implied growth minus supportable growth (Damodaran reverse DCF).
      History   (weight up to 0.20, scaled by years of multiple history: 2 years of PE data says little).
      Peers     (weight 0.35): PE premium over the sector's quality/growth-justified PE.
    A guard-rail then applies: a price far outside even the bull/bear DCF value can't be called "fair".
    """
    m, dcf = c["metrics"], c.get("dcf") or {}
    reasons, parts = [], []
    pe, spe, price = m.get("pe"), sec.get("median_pe"), m.get("price")
    ig, ge = dcf.get("implied_growth"), dcf.get("expected_growth")
    if ig is not None and ge is not None:
        gap = ig - ge
        parts.append({"name": "Reverse DCF", "w": 0.45, "s": _clip(gap / 0.06, -2, 2),
                      "detail": f"implied {ig*100:.0f}% vs supportable {ge*100:.0f}% growth"})
        if gap > 0.04:
            reasons.append(f"Price implies ~{ig*100:.0f}% a year profit growth for {dcf['years']} years, versus ~{ge*100:.0f}% that history and analysts support (reverse DCF at {dcf['cost_of_equity']*100:.1f}% cost of equity).")
        elif gap < -0.03:
            reasons.append(f"Price implies only ~{ig*100:.0f}% a year growth, below the ~{ge*100:.0f}% that history and analysts support, so expectations look modest.")
    pct = m.get("pe_pct")
    hs = c["history_stats"].get("pe", {})
    if pct is not None and pe and hs.get("since"):
        yrs = (pd.Timestamp(m["price_date"]) - pd.Timestamp(hs["since"])).days / 365.25
        w = 0.20 * _clip(yrs / 7, 0, 1)
        if w > 0.03:
            parts.append({"name": "Own history", "w": w, "s": (pct - 0.5) * 2,
                          "detail": f"PE at {pct*100:.0f}th percentile of {yrs:.1f} years"})
        span = f"the last {yrs:.0f} years" if yrs >= 1.5 else "the past year"
        if pct >= 0.8:
            reasons.append(f"PE of {pe:.0f} is near the top of its range over {span} (median {hs.get('median',0):.0f}).")
        elif pct <= 0.2:
            reasons.append(f"PE of {pe:.0f} is near the bottom of its range over {span} (median {hs.get('median',0):.0f})" + (", though that is a short history to judge by." if yrs < 5 else "."))
    rg = reg.get(c["symbol"])
    if rg:
        parts.append({"name": "Peers", "w": 0.35, "s": _clip(rg["residual"] / 0.4, -1.5, 1.5),
                      "detail": f"PE {pe:.0f} vs peer-justified {rg['justified_pe']:.0f}" if pe else ""})
        if pe and spe:
            prem = pe / spe - 1
            qual = "ROCE" if not c["financial"] else "ROE"
            if rg["residual"] > 0.3:
                reasons.append(f"Trades at PE {pe:.0f} vs sector median {spe:.0f}; peers with similar {qual} and growth trade nearer {rg['justified_pe']:.0f}, so the premium is not explained by fundamentals alone.")
            elif rg["residual"] < -0.3:
                reasons.append(f"Trades at PE {pe:.0f} vs a quality-and-growth-justified {rg['justified_pe']:.0f} for this sector; cheaper than peers of similar quality.")
            elif prem > 0.3:
                reasons.append(f"PE {pe:.0f} is {prem*100:.0f}% above the sector median, but its {qual} and growth largely justify that premium.")
    a = c["annual"]
    if a and a[-1].get("ni_growth") is not None and a[-1]["ni_growth"] < -0.25:
        reasons.append(f"Profit fell {abs(a[-1]['ni_growth'])*100:.0f}% last year, which inflates PE; check whether the drop is cyclical (temporary) or structural.")
    if not c["financial"] and m.get("de") is not None and m["de"] > 1.5:
        reasons.append(f"High leverage (D/E {m['de']:.1f}x) raises risk; equity value is sensitive to interest costs.")
    d = c.get("decomposition")
    if d and d.get("share_from_earnings") is not None and d["price_cagr"] > 0.05:
        se = d["share_from_earnings"]
        if se < 0.4:
            reasons.append(f"Since {d['from'][:4]}, most of the {d['price_cagr']*100:.0f}% a year price gain came from re-rating (PE {d['pe_from']:.0f} → {d['pe_to']:.0f}), not earnings growth ({d['earnings_cagr']*100:.0f}% a year).")
        elif se > 0.8:
            reasons.append(f"Since {d['from'][:4]}, the price gain has been earned: earnings grew {d['earnings_cagr']*100:.0f}% a year while PE moved {d['pe_from']:.0f} → {d['pe_to']:.0f}.")
    if not parts:
        return {"label": "Insufficient data", "score": None, "reasons": reasons or ["Not enough profitable history to value."], "parts": []}
    W = sum(p["w"] for p in parts)
    score = float(sum(p["w"] * p["s"] for p in parts) / W)
    label = "Looks expensive" if score > 0.35 else "Looks cheap" if score < -0.35 else "Around fair value"
    fv = dcf.get("fair_value") or {}
    if price and fv.get("bull") and price > 1.5 * fv["bull"] and label != "Looks expensive":
        label = "Looks expensive"
        reasons.insert(0, f"Price ₹{price:,.0f} is more than 1.5x even the optimistic DCF value (₹{fv['bull']:,.0f}), so other signals can't make it look fair.")
    elif price and fv.get("bear") and fv["bear"] > 0 and price < fv["bear"] / 1.5 and label != "Looks cheap":
        label = "Looks cheap"
        reasons.insert(0, f"Price ₹{price:,.0f} is below two-thirds of even the cautious DCF value (₹{fv['bear']:,.0f}).")
    if m.get("pe") is None:
        label = "Loss-making / not meaningful"
    for p in parts:
        p["share"] = p["w"] / W
    return {"label": label, "score": score, "reasons": reasons, "signals": len(parts), "parts": parts}


def build_all(universe: list[dict]):
    events_by_sym = defaultdict(list)
    for kind in ("announcements", "results", "board_meetings"):
        for e in load_events(kind):
            e = {**e, "kind": kind}
            events_by_sym[e["symbol"]].append(e)

    precompute([u["symbol"] for u in universe])  # levels at every candle, cached

    companies = {}
    for i, u in enumerate(universe):
        try:
            c = build_company(u, events_by_sym)
            if c:
                companies[u["symbol"]] = c
        except Exception as e:
            print("analyze fail", u["symbol"], repr(e))
        if i % 50 == 0:
            print(f"analyze {i}/{len(universe)}", flush=True)

    # ----- sector layer
    by_ind = defaultdict(list)
    for c in companies.values():
        by_ind[c["industry"]].append(c)
    sectors, regs = {}, {}
    factors = ["roce", "roe", "rev_cagr3", "ni_cagr3", "opm", "npm", "de", "asset_turnover", "leverage", "fcf_yield"]
    for ind, cs in by_ind.items():
        rows = [{"symbol": c["symbol"], **c["metrics"]} for c in cs]
        reg = sector_regression(rows)
        regs.update(reg)
        med = lambda k: (float(np.median([r[k] for r in rows if r.get(k) is not None])) if any(r.get(k) is not None for r in rows) else None)
        fin = any(c["financial"] for c in cs)
        target = "pb" if fin else "pe"
        rewards = []
        for f in factors:
            rho = spearman([r.get(target) if (r.get(target) or 0) > 0 else None for r in rows], [r.get(f) for r in rows])
            if rho is not None:
                rewards.append({"factor": f, "rho": rho})
        rewards.sort(key=lambda x: -abs(x["rho"]))
        # sector median valuation through time (weekly)
        agg = defaultdict(lambda: defaultdict(list))
        for c in cs:
            v = c.get("val")
            if not v:
                continue
            for k in ("pe", "pb", "ev_ebitda"):
                for t, x in zip(v["t"], v[k]):
                    if x is not None and 0 < x < 500:
                        agg[k][t].append(x)
        series = {}
        for k, d in agg.items():
            ts = sorted(t for t, xs in d.items() if len(xs) >= max(3, len(cs) // 3))
            series[k] = {"t": ts, "v": [float(np.median(d[t])) for t in ts]}
        sectors[ind] = {
            "industry": ind, "count": len(cs), "financial": fin, "valuation_target": target,
            "median_pe": med("pe"), "median_pb": med("pb"), "median_ev_ebitda": med("ev_ebitda"),
            "median_roce": med("roce"), "median_roe": med("roe"), "median_rev_cagr3": med("rev_cagr3"),
            "median_opm": med("opm"), "median_npm": med("npm"), "median_de": med("de"),
            "median_asset_turnover": med("asset_turnover"), "median_leverage": med("leverage"),
            "total_mcap": sum(r["mcap"] or 0 for r in rows), "rewards": rewards, "series": series,
            "members": sorted((c["symbol"] for c in cs), key=lambda s: -(companies[s]["metrics"].get("mcap") or 0)),
        }

    # ----- verdicts, peer differences, write company files
    summary = []
    for sym, c in companies.items():
        sec = sectors[c["industry"]]
        c["verdict"] = verdict(c, sec, regs)
        c["peer"] = {"justified_pe": (regs.get(sym) or {}).get("justified_pe"),
                     "pe_residual": (regs.get(sym) or {}).get("residual")}
        m = c["metrics"]
        diffs = []
        for k, lab in (("npm", "net margin"), ("opm", "operating margin"), ("asset_turnover", "asset turnover"),
                       ("leverage", "balance-sheet leverage"), ("roce", "ROCE"), ("roe", "ROE"), ("rev_cagr3", "3y revenue growth")):
            v, s = m.get(k), sec.get(f"median_{k}")
            if v is not None and s not in (None, 0):
                diffs.append({"metric": k, "label": lab, "value": v, "sector": s, "rel": v / s - 1 if s > 0 else None})
        c["peer"]["vs_sector"] = diffs
        write_json(SITE_DATA / "company" / f"{sym.replace('&', '_')}.json", c)
        cc = c["coffee_can"]
        summary.append({
            "symbol": sym, "name": c["name"], "industry": c["industry"], "financial": c["financial"],
            **{k: m.get(k) for k in ("price", "price_date", "mcap", "pe", "pb", "ev_ebitda", "roce", "roe", "opm", "npm", "de",
                                     "rev_cagr3", "ni_cagr3", "rev_growth", "fcf_yield", "div_yield", "ret_1y", "ret_3y",
                                     "ret_5y", "implied_growth", "upside", "pe_pct", "ey", "asset_turnover", "years_of_data")},
            "expected_growth": (c.get("dcf") or {}).get("expected_growth"),
            "justified_pe": c["peer"]["justified_pe"],
            "coffee_can": cc["status"], "cc_years": cc["years_checked"],
            "buffett_score": sum(1 for b in c["buffett"] if b["pass"]), "buffett_total": len(c["buffett"]),
            "verdict": c["verdict"]["label"], "score": c["verdict"]["score"],
            "next_earnings": (c["estimates"].get("next_earnings") or [None])[0] if isinstance(c["estimates"].get("next_earnings"), list) else c["estimates"].get("next_earnings"),
        })
    write_json(SITE_DATA / "summary.json", summary)
    write_json(SITE_DATA / "sectors.json", sectors)
    feed = []
    for sym, evs in events_by_sym.items():
        feed.extend(evs)
    feed.sort(key=lambda e: e["date"], reverse=True)
    write_json(SITE_DATA / "events.json", feed[:400])
    write_json(SITE_DATA / "meta.json", {"updated": now_iso(), "companies": len(companies), "config": CONFIG,
                                         "price_date": max((s["price_date"] or "" for s in summary), default=None)})
    return len(companies)
