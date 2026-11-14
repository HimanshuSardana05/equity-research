"""Setups: classic candle patterns that form AT a support/resistance level.

No lookahead: a pattern completing on day t is checked against the levels the LevelIndicator showed
at the close of day t-1 (computed from candles up to t-1 only). Forward returns are measured from
the close of day t, for the backtest.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

PATTERNS = {
    "bull_engulf": ("Bullish engulfing", 1),
    "bear_engulf": ("Bearish engulfing", -1),
    "bull_harami": ("Bullish harami", 1),
    "bear_harami": ("Bearish harami", -1),
    "piercing": ("Piercing line", 1),
    "dark_cloud": ("Dark cloud cover", -1),
    "morning_star": ("Morning star", 1),
    "evening_star": ("Evening star", -1),
}
HORIZONS = (5, 10, 20)
TOL_MAX = 0.5  # widest touch tolerance (in ATR) recorded; the rules pick a tighter one
RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "setup_rules.json"
DEFAULT_RULES = {
    "version": 1, "min_score": 60, "weights": {"level": 35, "pattern": 25, "volume": 25, "trend": 15},
    "level_full_at": 0.6, "volume": {"floor": 0.7, "full": 2.0}, "tolerance_atr": 0.25, "direction": "both",
    "patterns": {k: True for k in PATTERNS}, "kinds": {"test": True, "break": True},
    "level_types": {"zone": True, "swing": True}, "alerts": {"enabled": False, "channels": {}},
}


def load_rules(path: Path = RULES_PATH) -> dict:
    """Screening rules shared by the web app, the daily build and alerts (config/setup_rules.json)."""
    r = json.loads(json.dumps(DEFAULT_RULES))
    try:
        user = json.loads(path.read_text())
    except (OSError, ValueError):
        return r
    for k, v in user.items():
        r[k] = {**r[k], **v} if isinstance(v, dict) and isinstance(r.get(k), dict) else v
    return r


def score(s: dict, rules: dict) -> int:
    """0-100 score from the setup's components and the rule weights. Mirrored in app/page.html (suScore)."""
    w = rules["weights"]
    tot = sum(max(0, float(w.get(k, 0))) for k in ("level", "pattern", "volume", "trend")) or 1
    v = rules["volume"]
    vol_s = 0.5 if s.get("vol") is None else max(0, min(1, (s["vol"] - v["floor"]) / max(1e-9, v["full"] - v["floor"])))
    x = (max(0, w.get("level", 0)) * min(1, s["zs"] / max(1e-9, rules["level_full_at"]))
         + max(0, w.get("pattern", 0)) * s["strength"]
         + max(0, w.get("volume", 0)) * vol_s
         + max(0, w.get("trend", 0)) * (1 if s["trend"] else 0))
    return int(round(100 * x / tot))


def matches(s: dict, rules: dict, min_score=None) -> bool:
    """Does a setup pass the rules (pattern, kind, level type, direction, tolerance, minimum score)?"""
    if not rules["patterns"].get(s["pattern"], True) or not rules["kinds"].get(s["kind"], True):
        return False
    if not rules["level_types"].get(s.get("lt", "zone"), True):
        return False
    d = rules.get("direction", "both")
    if (d == "bull" and s["dir"] < 0) or (d == "bear" and s["dir"] > 0):
        return False
    if s.get("tol", 0) > rules["tolerance_atr"] + 1e-9:
        return False
    return score(s, rules) >= (rules["min_score"] if min_score is None else min_score)


def _frame(prices: pd.DataFrame) -> pd.DataFrame:
    d = prices.dropna(subset=["open", "high", "low", "close"]).copy()
    d["body"] = (d["close"] - d["open"]).abs()
    prev = d["close"].shift()
    tr = pd.concat([d["high"] - d["low"], (d["high"] - prev).abs(), (d["low"] - prev).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    d["avg_body"] = d["body"].rolling(10).mean().shift()
    d["vol20"] = d["volume"].replace(0, np.nan).rolling(20).mean().shift()
    d["sma20"] = d["close"].rolling(20).mean()
    d["chg10"] = d["close"] / d["close"].shift(10) - 1  # trend into the pattern
    return d


def detect(d: pd.DataFrame, i: int) -> list[tuple[str, float, int]]:
    """Patterns completing on row i: (key, strength 0..1, number of candles)."""
    if i < 3:
        return []
    o, h, l, c = d["open"].to_numpy(), d["high"].to_numpy(), d["low"].to_numpy(), d["close"].to_numpy()
    body, avg = d["body"].to_numpy(), d["avg_body"].to_numpy()
    if not (avg[i] > 0):
        return []
    out = []
    green, red = c[i] > o[i], c[i] < o[i]
    pg, pr = c[i - 1] > o[i - 1], c[i - 1] < o[i - 1]
    pmid = (o[i - 1] + c[i - 1]) / 2
    big_prev = body[i - 1] >= 0.8 * avg[i]
    # engulfing: today's real body covers yesterday's
    if pr and green and o[i] <= c[i - 1] and c[i] >= o[i - 1] and body[i] > body[i - 1]:
        out.append(("bull_engulf", min(1, body[i] / max(body[i - 1], 1e-9) / 3), 2))
    if pg and red and o[i] >= c[i - 1] and c[i] <= o[i - 1] and body[i] > body[i - 1]:
        out.append(("bear_engulf", min(1, body[i] / max(body[i - 1], 1e-9) / 3), 2))
    # harami: small body inside a large opposite body
    if pr and big_prev and green and o[i] >= c[i - 1] and c[i] <= o[i - 1] and body[i] <= 0.6 * body[i - 1]:
        out.append(("bull_harami", 1 - body[i] / body[i - 1], 2))
    if pg and big_prev and red and o[i] <= c[i - 1] and c[i] >= o[i - 1] and body[i] <= 0.6 * body[i - 1]:
        out.append(("bear_harami", 1 - body[i] / body[i - 1], 2))
    # piercing / dark cloud (opening beyond yesterday's close, closing past its midpoint, not engulfing)
    if pr and big_prev and green and o[i] < c[i - 1] and pmid < c[i] < o[i - 1]:
        out.append(("piercing", (c[i] - pmid) / max(o[i - 1] - pmid, 1e-9), 2))
    if pg and big_prev and red and o[i] > c[i - 1] and o[i - 1] < c[i] < pmid:
        out.append(("dark_cloud", (pmid - c[i]) / max(pmid - o[i - 1], 1e-9), 2))
    # stars: big candle, small-bodied middle candle, strong opposite candle closing past the first's midpoint
    a = i - 2
    amid = (o[a] + c[a]) / 2
    small_mid = body[i - 1] <= 0.35 * body[a] and body[a] >= avg[i]
    if c[a] < o[a] and small_mid and max(o[i - 1], c[i - 1]) <= c[a] + 0.1 * body[a] and green and c[i] > amid:
        out.append(("morning_star", min(1, (c[i] - amid) / max(o[a] - amid, 1e-9)), 3))
    if c[a] > o[a] and small_mid and min(o[i - 1], c[i - 1]) >= c[a] - 0.1 * body[a] and red and c[i] < amid:
        out.append(("evening_star", min(1, (amid - c[i]) / max(amid - o[a], 1e-9)), 3))
    return [(k, float(max(0, min(1, s))), n) for k, s, n in out]


def _interaction(direction, lo, hi, prev_close, close, levels, atr):
    """Best level interaction for a pattern: (kind, label, level_lo, level_hi, level_score)."""
    zones, swings = levels
    tol = TOL_MAX * atr
    best = None
    cands = [(z[0], z[1], z[3], 1 - math.exp(-z[2] / 7.163), "zone") for z in zones]
    cands += [(s[0], s[0], s[1], 0.25 if s[2] else 0.35, "swing") for s in swings]
    for zlo, zhi, side, sc, what in cands:
        name = "zone" if what == "zone" else ("swing high" if side > 0 else "swing low")
        if direction > 0:
            # support held: the pattern's low reached the level and the close stayed above its bottom
            if side <= 0 and lo <= zhi + tol and close >= zlo - tol and lo >= zlo - 2 * atr:
                need = max(0.0, (lo - zhi) / atr, (zlo - close) / atr)
                hit = ("test", f"Held support {name}", zlo, zhi, sc, need, what)
            # breakout: the close crossed above a resistance level
            elif side >= 0 and prev_close <= zhi and close > zhi:
                hit = ("break", f"Broke above resistance {name}", zlo, zhi, sc, 0.0, what)
            else:
                continue
        else:
            if side >= 0 and hi >= zlo - tol and close <= zhi + tol and hi <= zhi + 2 * atr:
                need = max(0.0, (zlo - hi) / atr, (close - zhi) / atr)
                hit = ("test", f"Rejected at resistance {name}", zlo, zhi, sc, need, what)
            elif side <= 0 and prev_close >= zlo and close < zlo:
                hit = ("break", f"Broke below support {name}", zlo, zhi, sc, 0.0, what)
            else:
                continue
        # strongest level first; among equals, the closest touch
        if best is None or (hit[4], -hit[5]) > (best[4], -best[5]):
            best = hit
    return best


def find_setups(prices: pd.DataFrame, hist: dict, rules: dict | None = None) -> list[dict]:
    """All pattern-at-level setups over the level-history window, with forward returns where known."""
    if not hist or not hist.get("t"):
        return []
    rules = rules or load_rules()
    d = _frame(prices)
    if len(d) < 40:
        return []
    dates = d.index.strftime("%Y-%m-%d").tolist()
    pos = {t: i for i, t in enumerate(dates)}
    lv_by_date = dict(zip(hist["t"], hist["lv"]))
    closes = d["close"].to_numpy()
    out = []
    for t in hist["t"]:
        i = pos.get(t)
        if i is None or i < 3:
            continue
        prev_lv = lv_by_date.get(dates[i - 1])
        if prev_lv is None:
            continue
        pats = detect(d, i)
        if not pats:
            continue
        row = d.iloc[i]
        atr = float(row["atr"]) if row["atr"] > 0 else float(row["high"] - row["low"]) or 1.0
        for key, strength, n in pats:
            direction = PATTERNS[key][1]
            lo = float(d["low"].iloc[i - n + 1:i + 1].min())
            hi = float(d["high"].iloc[i - n + 1:i + 1].max())
            hit = _interaction(direction, lo, hi, float(closes[i - 1]), float(closes[i]), prev_lv, atr)
            if not hit:
                continue
            kind, label, zlo, zhi, zscore, need, what = hit
            vol_ratio = float(row["volume"] / row["vol20"]) if row["vol20"] and row["vol20"] > 0 else None
            trend_ok = (row["chg10"] < 0) if direction > 0 else (row["chg10"] > 0)
            if kind == "break":  # a breakout should go with the trend, a reversal against it
                trend_ok = (row["close"] > row["sma20"]) if direction > 0 else (row["close"] < row["sma20"])
            fwd = {}
            for hz in HORIZONS:
                if i + hz < len(closes):
                    fwd[f"r{hz}"] = round(float(closes[i + hz] / closes[i] - 1), 4)
            out.append({"date": t, "pattern": key, "dir": direction, "kind": kind, "level": label,
                        "lo": round(zlo, 2), "hi": round(zhi, 2), "close": round(float(closes[i]), 2),
                        "strength": round(strength, 2), "vol": None if vol_ratio is None else round(vol_ratio, 2),
                        "trend": bool(trend_ok), "candles": n, "zs": round(zscore, 3), "tol": round(need, 2),
                        "lt": what, **fwd})
            out[-1]["score"] = score(out[-1], rules)
    return out


def baseline(prices: pd.DataFrame, start_date: str | None) -> dict:
    """Unconditional forward returns over the same window, to compare setups against."""
    c = prices["close"].dropna()
    if start_date:
        c = c[c.index >= pd.Timestamp(start_date)]
    out = {}
    for hz in HORIZONS:
        r = (c.shift(-hz) / c - 1).dropna()
        out[f"r{hz}"] = r.tolist()
    return out


def summarize(all_setups: list[dict], base: dict, rules: dict | None = None) -> list[dict]:
    """Backtest table: per pattern x level-kind, forward returns in the pattern's direction and hit rate.
    Signals outside the rules' tolerance / level types are left out; the *_hi columns are those passing the minimum score."""
    rules = rules or load_rules()
    rows = []
    groups = {}
    for s in all_setups:
        if s.get("tol", 0) > rules["tolerance_atr"] + 1e-9 or not rules["level_types"].get(s.get("lt", "zone"), True):
            continue
        groups.setdefault((s["pattern"], s["kind"]), []).append(s)
        groups.setdefault((s["pattern"], "all"), []).append(s)
    for (pat, kind), ss in sorted(groups.items()):
        direction = PATTERNS[pat][1]
        row = {"pattern": pat, "name": PATTERNS[pat][0], "dir": direction, "kind": kind, "n": len(ss)}
        for hz in HORIZONS:
            r = [direction * s[f"r{hz}"] for s in ss if f"r{hz}" in s]
            if r:
                row[f"avg{hz}"] = round(float(np.mean(r)), 4)
                row[f"med{hz}"] = round(float(np.median(r)), 4)
                row[f"hit{hz}"] = round(float(np.mean([x > 0 for x in r])), 3)
                row[f"n{hz}"] = len(r)
                b = base.get(f"r{hz}") or []
                if b:
                    row[f"base{hz}"] = round(float(np.mean(b)) * direction, 4)
        hi_r = [direction * s["r10"] for s in ss if "r10" in s and s["score"] >= rules["min_score"]]
        if hi_r:
            row["hit10_hi"] = round(float(np.mean([x > 0 for x in hi_r])), 3)
            row["n10_hi"] = len(hi_r)
        rows.append(row)
    return rows
