"""Trade candidates for the Backtest tab and live strategy signals.

Every setup (candle pattern at a prior-day level, see setups.py) becomes a candidate carrying what a trading
rule needs, all known at the close of the signal day (no lookahead):
  pattern high/low, the level, ATR(14), 50/200-day averages and the 50-day slope, volume vs its 20-day average,
  how far a breakout closed beyond its level, and the next opposite level (the target) from the levels drawn
  at the close of the signal day.
It also carries the next PATH_BARS candles, which the app uses to simulate entry, stop, target, trailing stop
and time stop. Prices are stored as integer basis points relative to the signal-day close.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from setups import PATTERNS, _frame

PATH_BARS = 20  # up to 5 days to enter + 15 days held
VOL_FLOOR = 1.0  # the backtest file keeps setups with volume at least this multiple of the 20-day average
ROOT = Path(__file__).resolve().parent.parent
STRATS_PATH = ROOT / "config" / "strategies.json"

COLS = ["s", "date", "p", "kind", "lt", "zs", "strength", "vol", "trend", "tol", "atr", "phi", "plo", "zlo", "zhi",
        "tgt", "brk", "a50", "s50", "a200", "ref", "path"]


def _bp(x, ref):
    return None if x is None or not np.isfinite(x) else int(round((x / ref - 1) * 10000))


def _target(direction, close, lv):
    """Next opposite level beyond the close, from the levels drawn at the close of the signal day."""
    if not lv:
        return None
    zones, swings = lv
    if direction > 0:
        c = [z[0] for z in zones if z[0] > close] + [s[0] for s in swings if s[1] > 0 and not s[2] and s[0] > close]
        return min(c) if c else None
    c = [z[1] for z in zones if z[1] < close] + [s[0] for s in swings if s[1] < 0 and not s[2] and s[0] < close]
    return max(c) if c else None


def candidates(prices: pd.DataFrame, hist: dict, setups: list[dict], sidx: int = 0) -> list[list]:
    if not setups or not hist or not hist.get("t"):
        return []
    d = _frame(prices)
    d["sma50"] = d["close"].rolling(50).mean()
    d["sma200"] = d["close"].rolling(200).mean()
    dates = d.index.strftime("%Y-%m-%d").tolist()
    pos = {t: i for i, t in enumerate(dates)}
    lv_by_date = dict(zip(hist["t"], hist["lv"]))
    o, h, l, c = (d[k].to_numpy() for k in ("open", "high", "low", "close"))
    s50, s200, atr = d["sma50"].to_numpy(), d["sma200"].to_numpy(), d["atr"].to_numpy()
    pkeys = list(PATTERNS)
    out = []
    for st in setups:
        i = pos.get(st["date"])
        if i is None:
            continue
        ref = float(c[i])
        n = st.get("candles", 1)
        phi, plo = float(h[i - n + 1:i + 1].max()), float(l[i - n + 1:i + 1].min())
        a = float(atr[i]) if np.isfinite(atr[i]) and atr[i] > 0 else float(h[i] - l[i]) or ref * 0.02
        brk = 0.0
        if st["kind"] == "break":
            brk = (ref - st["hi"]) / a if st["dir"] > 0 else (st["lo"] - ref) / a
        slope = None
        if i >= 10 and np.isfinite(s50[i]) and np.isfinite(s50[i - 10]):
            slope = 1 if s50[i] > s50[i - 10] else -1
        path = []
        for k in range(1, PATH_BARS + 1):
            if i + k >= len(c):
                break
            path += [_bp(o[i + k], ref), _bp(h[i + k], ref), _bp(l[i + k], ref), _bp(c[i + k], ref)]
        out.append([sidx, st["date"], pkeys.index(st["pattern"]), 1 if st["kind"] == "break" else 0,
                    1 if st.get("lt") == "swing" else 0, st.get("zs"), st["strength"], st.get("vol"), 1 if st["trend"] else 0, st.get("tol", 0),
                    _bp(ref + a, ref), _bp(phi, ref), _bp(plo, ref), _bp(st["lo"], ref), _bp(st["hi"], ref),
                    _bp(_target(st["dir"], ref, lv_by_date.get(st["date"])), ref), round(brk, 2),
                    _bp(s50[i], ref) if np.isfinite(s50[i]) else None, slope,
                    _bp(s200[i], ref) if np.isfinite(s200[i]) else None, round(ref, 2), path])
    return out


# ------------------------------------------------------------------ live strategy signals (for alerts)
DEFAULT_STRATEGY = {
    "min_score": 70, "dir": "both", "kinds": {"test": True, "break": True}, "patterns": {},
    "trend": "sma50", "no_counter_200": True, "min_vol": 1.5, "brk_atr": 0.5, "tol": 0.25,
    "entry": "break", "entry_days": 3, "stop_atr": 0.25, "min_rr": 2.0, "no_target": "allow",
    "partial_r": 1.0, "partial_frac": 0.5, "be_after_partial": True, "trail_atr": 2.0, "max_days": 15,
    "risk_pct": 1.0, "max_open": 5,
}


def load_strategies() -> list[dict]:
    try:
        return json.loads(STRATS_PATH.read_text()).get("strategies", [])
    except (OSError, ValueError):
        return []


def _get(row, k):
    return row[COLS.index(k)]


def passes(row, P: dict, score: int, direction: int) -> bool:
    """Signal filters of a strategy; mirrored by btPass() in app/page.html."""
    if score < P["min_score"]:
        return False
    if (P["dir"] == "long" and direction < 0) or (P["dir"] == "short" and direction > 0):
        return False
    kind = "break" if _get(row, "kind") else "test"
    if not P["kinds"].get(kind, True) or P["patterns"].get(list(PATTERNS)[_get(row, "p")]) is False:
        return False
    if (_get(row, "tol") or 0) > P["tol"] + 1e-9:
        return False
    vol = _get(row, "vol")
    if P["min_vol"] and (vol is None or vol < P["min_vol"]):
        return False
    if kind == "break" and _get(row, "brk") < P["brk_atr"]:
        return False
    a50, s50, a200 = _get(row, "a50"), _get(row, "s50"), _get(row, "a200")
    if P["trend"] == "sma50":
        if a50 is None:
            return False
        if not ((direction > 0 and (a50 < 0 or s50 == 1)) or (direction < 0 and (a50 > 0 or s50 == -1))):
            return False
    if P["no_counter_200"] and a200 is not None and ((direction > 0 and a200 > 0) or (direction < 0 and a200 < 0)):
        return False
    return True


def live_signals(cands: list[list], syms: list[str], score_of, as_of: str, recent_dates: list[str]) -> list[dict]:
    """Signals from live strategies in config/strategies.json: setups from the last few days that pass the
    strategy and are waiting for (or just got) their entry."""
    out = []
    for strat in load_strategies():
        if not strat.get("live"):
            continue
        P = {**DEFAULT_STRATEGY, **(strat.get("params") or {})}
        window = recent_dates[-int(P["entry_days"]):] if P["entry_days"] else recent_dates[-1:]
        for row in cands:
            if _get(row, "date") not in window:
                continue
            key = list(PATTERNS)[_get(row, "p")]
            direction = PATTERNS[key][1]
            sc = score_of(row)
            if not passes(row, P, sc, direction):
                continue
            path = _get(row, "path") or []
            trig = _get(row, "phi") if direction > 0 else _get(row, "plo")
            hit = None
            for k in range(len(path) // 4):
                hh, ll = path[4 * k + 1], path[4 * k + 2]
                if (direction > 0 and hh is not None and hh > trig) or (direction < 0 and ll is not None and ll < trig):
                    hit = k + 1
                    break
            status = "entered" if hit else ("waiting" if len(path) // 4 < P["entry_days"] else "expired")
            if status == "expired":
                continue
            sym = syms[_get(row, "s")]
            out.append({"strategy": strat.get("name", "Strategy"), "symbol": sym, "date": _get(row, "date"),
                        "pattern": key, "dir": direction, "score": sc, "status": status,
                        "id": f"sig|{strat.get('name')}|{sym}|{_get(row, 'date')}|{key}",
                        "text": f"{'BUY' if direction > 0 else 'SELL'} signal {sym} · {PATTERNS[key][0]} {_get(row, 'date')} · "
                                f"score {sc} · {strat.get('name', 'Strategy')} · "
                                f"{'entry triggered' if status == 'entered' else 'waiting for entry'}"})
    return out
