"""Run the user's LevelIndicator on daily NSE candles (session = trading date).

The indicator was designed for 5-minute candles. On daily candles its windows mean much longer spans
(e.g. 72-bar pivots = ~3.5 months either side), so we compute two sets:
  * "given":  the class exactly as supplied
  * "daily":  same logic, parameters rescaled for daily bars (see DailyLevelIndicator)
"""
from __future__ import annotations

import pandas as pd

from level_indicator import LevelIndicator


class DailyLevelIndicator(LevelIndicator):
    """Same algorithm; parameter values chosen for daily candles (one candle per session)."""
    ZONE_SESSIONS = 250            # about one trading year of daily candles
    ZONE_PIVOT_BARS = 10           # a pivot must top/bottom two weeks either side
    ZONE_SUPPRESSION_BARS = 10
    ZONE_IN_PLAY_DAILY_ATR = 3     # zones within 3 daily ATRs of the close
    SWING_HISTORY_SESSIONS = 120   # ~6 months of swings
    SWING_HISTORY_BARS = 120


def _candles(prices: pd.DataFrame, bars: int) -> pd.DataFrame:
    df = prices.dropna(subset=["open", "high", "low", "close"]).tail(bars)
    if df.empty:
        return df
    out = pd.DataFrame({
        "time": df.index.strftime("%Y-%m-%d"),
        "session": df.index.strftime("%Y-%m-%d"),
        "high": df["high"].to_numpy(float), "low": df["low"].to_numpy(float),
        "close": df["close"].to_numpy(float), "volume": df["volume"].fillna(0).to_numpy(float),
    })
    return out.reset_index(drop=True)


def _records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    recs = []
    for r in df.to_dict("records"):
        recs.append({k: (v.item() if hasattr(v, "item") else v) for k, v in r.items()})
    return recs


def compute_levels(prices: pd.DataFrame) -> dict:
    out = {}
    for key, ind, bars in (("given", LevelIndicator(), 400), ("daily", DailyLevelIndicator(), 600)):
        c = _candles(prices, bars)
        if len(c) < 30:
            out[key] = {"zones": [], "swings": []}
            continue
        try:
            out[key] = {"zones": _records(ind.zones(c)), "swings": _records(ind.swingLevels(c))}
        except Exception as e:  # never let one stock break the build
            out[key] = {"zones": [], "swings": [], "error": str(e)[:200]}
    out["as_of"] = prices.index[-1].strftime("%Y-%m-%d") if len(prices) else None
    return out


def compute_levels_full(sym: str, prices: pd.DataFrame) -> dict:
    out = compute_levels(prices)
    try:
        hist = history_for(sym, prices)
        for key, _, _ in SETS:
            out[key]["history"] = segments(hist.get(key, {}))
    except Exception as e:
        out["history_error"] = str(e)[:200]
    return out


# ---------------------------------------------------------------------------
# Levels through time: run the indicator at every daily candle using only the
# candles up to it (the class's own design), then compress into segments.
# ---------------------------------------------------------------------------
import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor

from common import ROOT

HIST_BARS = 500           # about two trading years of history
CACHE = ROOT / "data" / "cache" / "levels"
CACHE_VERSION = 2
SETS = (("daily", DailyLevelIndicator, 600), ("given", LevelIndicator, 400))


def _bar_levels(ind, c: pd.DataFrame) -> list:
    """Compact levels at the last candle of c: [zones, swings]."""
    try:
        z = ind.zones(c)
        s = ind.swingLevels(c)
    except Exception:
        return [[], []]
    zones = [[round(float(r.low), 2), round(float(r.high), 2), int(r.touches), {"above": 1, "below": -1}.get(r.side, 0)]
             for r in z.itertuples()]
    swings = [[round(float(r.price), 2), 1 if r.side == "high" else -1, bool(r.broken)] for r in s.itertuples()]
    return [zones, swings]


def _fingerprint(c: pd.DataFrame) -> list:
    return [c["time"].tolist(), [round(x, 2) for x in c["close"].tolist()]]


def history_for(sym: str, prices: pd.DataFrame, sets=None, cache=None, hist_bars=None) -> dict:
    """Per-bar levels for the last HIST_BARS candles, cached and extended incrementally."""
    sets, cache_dir, HIST = sets or SETS, cache or CACHE, hist_bars or HIST_BARS
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{sym.replace('&', '_')}.json"
    cache = {}
    if path.exists():
        try:
            cache = json.loads(path.read_text())
        except Exception:
            cache = {}
    out = {}
    for key, cls, bars in sets:
        full = _candles(prices, bars + HIST)
        n = len(full)
        if n < 60:
            out[key] = {"t": [], "lv": []}
            continue
        start = max(30, n - HIST)
        times = full["time"].tolist()
        closes = [round(x, 2) for x in full["close"].tolist()]
        old = cache.get(key) if cache.get("v") == CACHE_VERSION else None
        known = {}
        if old:
            # reuse bars whose history (dates and closes) is unchanged; a split re-adjustment changes closes
            idx = {t: i for i, t in enumerate(old["t"])}
            prev_close = dict(zip(old["t"], old["c"]))
            stable = all(prev_close.get(t) is None or abs(prev_close[t] - c_) <= 0.01 * max(1, abs(c_))
                         for t, c_ in zip(times, closes))
            if stable:
                known = {t: old["lv"][i] for t, i in idx.items()}
        ind = cls()
        lv = []
        for k in range(start, n):
            t = times[k]
            if t in known:
                lv.append(known[t])
                continue
            c = full.iloc[max(0, k + 1 - bars):k + 1].reset_index(drop=True)
            lv.append(_bar_levels(ind, c))
        out[key] = {"t": times[start:], "c": closes[start:], "lv": lv}
    out["v"] = CACHE_VERSION
    path.write_text(json.dumps(out, separators=(",", ":")))
    return out


def segments(hist: dict) -> dict:
    """Compress per-bar levels into drawable segments: same level on consecutive bars = one segment."""
    t, lv = hist.get("t", []), hist.get("lv", [])
    zones, swings, open_z, open_s = [], [], {}, {}
    for i, (zs, ss) in enumerate(lv):
        seen_z, seen_s = set(), set()
        for lo, hi, touches, side in zs:
            k = (lo, hi, side)
            seen_z.add(k)
            if k in open_z and open_z[k]["e"] == i - 1:
                open_z[k]["e"] = i
                open_z[k]["n"] = max(open_z[k]["n"], touches)
            else:
                seg = {"lo": lo, "hi": hi, "side": side, "s": i, "e": i, "n": touches}
                open_z[k] = seg
                zones.append(seg)
        for price, side, broken in ss:
            k = (price, side, broken)
            seen_s.add(k)
            if k in open_s and open_s[k]["e"] == i - 1:
                open_s[k]["e"] = i
            else:
                seg = {"p": price, "side": side, "b": broken, "s": i, "e": i}
                open_s[k] = seg
                swings.append(seg)
    fmt = lambda segs: [{**{k: v for k, v in s.items() if k not in ("s", "e")}, "from": t[s["s"]], "to": t[s["e"]]} for s in segs]
    return {"zones": fmt(zones), "swings": fmt(swings)}


def _warm(sym: str):
    from prices import load_prices
    try:
        history_for(sym, load_prices(sym))
        return sym, None
    except Exception as e:
        return sym, str(e)[:200]


def precompute(symbols: list[str], workers: int | None = None):
    """Fill/extend the per-bar cache for every symbol in parallel (the slow part, run once a day)."""
    workers = workers or max(1, (os.cpu_count() or 2))
    errs = 0
    with ProcessPoolExecutor(workers) as ex:
        for sym, err in ex.map(_warm, symbols, chunksize=4):
            if err:
                errs += 1
                print("levels fail", sym, err)
    print(f"levels history ready for {len(symbols) - errs}/{len(symbols)}")


# ---------------------------------------------------------------- long history for the weekly research run
LONG_BARS = 2500          # about ten trading years
LONG_CACHE = ROOT / "data" / "cache" / "levels_long"
LONG_SETS = (("daily", DailyLevelIndicator, 600),)


def long_history(sym: str, prices: pd.DataFrame) -> dict:
    return history_for(sym, prices, LONG_SETS, LONG_CACHE, LONG_BARS).get("daily") or {}


def _warm_long(sym: str):
    from prices import load_prices
    import time
    t = time.time()
    try:
        long_history(sym, load_prices(sym))
        return sym, None, time.time() - t
    except Exception as e:
        return sym, str(e)[:200], time.time() - t


def precompute_long(symbols: list[str], budget_s: float = 3000, workers: int | None = None) -> list[str]:
    """Extend the 10-year level history, stopping new work after budget_s seconds; the rest continues next run.
    Returns the symbols whose long history is complete."""
    import time
    from concurrent.futures import ProcessPoolExecutor, as_completed
    workers = workers or max(1, (os.cpu_count() or 2))
    t0, done = time.time(), []
    with ProcessPoolExecutor(workers) as ex:
        pending = list(symbols)
        futs = {}
        while pending or futs:
            while pending and len(futs) < workers * 2 and time.time() - t0 < budget_s:
                s_ = pending.pop(0)
                futs[ex.submit(_warm_long, s_)] = s_
            if not futs:
                break
            f = next(as_completed(list(futs)))
            sym, err, _ = f.result()
            futs.pop(f)
            if not err:
                done.append(sym)
    print(f"long level history: {len(done)}/{len(symbols)} stocks done in {time.time() - t0:.0f}s", flush=True)
    return done
