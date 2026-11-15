"""Zone-to-zone behaviour: does a stock rejected at a zone travel to the opposite zone (mean reversion, in
Himanshu's equity sense), or does it break through zones directionally?

For every daily candle we take the full zone map (DailyLevelIndicator, every band in the last ~250 sessions
with 2+ pivots, using only candles before that day) and merge bands closer than one daily ATR, so the
ladder holds only clean, separated zones. Then a simple state machine walks the candles:

  * Visit:      price trades into a zone from one side. It stays "in the zone" until a close resolves it.
  * Deflection: the close leaves the zone back on the side it came from (0.5 ATR beyond the edge, or three
                closes outside without touching). Outcome: did price reach the nearest zone on the other
                side (a zone-to-zone move) before touching the deflection zone again?
  * Breakout:   the close leaves through the far side. Consecutive breakouts in the same direction form a
                leg; zones broken per leg measures breakout strength. A leg ends on a deflection against
                its direction or a breakout the other way.

Writes app/data/zones/{SYM}.json (candles, zone ladder through time, events, stats) and _all.json (screener).
Zone maps are cached in data/cache/zonemaps so daily runs only add the new candles.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from statistics import median

import numpy as np

from common import ROOT, SITE_DATA, write_json
from levels import DailyLevelIndicator, _candles

BARS = 520            # candles analysed (about two years); stats use the last year and both years
YEAR = 250
LOOKBACK = 600        # candles the indicator sees at each step
CACHE = ROOT / "data" / "cache" / "zonemaps"
CACHE_V = 2
OUT = SITE_DATA / "zones"
DEFLECT_ATR = 1.0     # close this far beyond the entry edge confirms a deflection
AWAY_CLOSES = 5       # ...or this many closes outside the zone without touching it
BREAK_ATR = 0.1       # close this far beyond the far edge confirms a breakout
MERGE_ATR = 1.0       # zones closer than this are one zone...
MAX_WIDTH_ATR = 2.5   # ...unless the merged zone would be wider than this
TARGET_GAP_ATR = 1.5  # the opposite zone must be at least this far away to count as a zone-to-zone move


class FullZones(DailyLevelIndicator):
    """Every zone in the window, not just the ones near price."""
    ZONE_IN_PLAY_DAILY_ATR = 1e9
    ZONE_MAX = 1000


def _atr(h, l, c, n=20):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    out = np.full(len(c), np.nan)
    cs = np.cumsum(tr)
    out[n - 1:] = (cs[n - 1:] - np.r_[0, cs[:-n]]) / n
    out[:n - 1] = out[n - 1] if len(c) >= n else np.nanmean(tr)
    return out


def _merge(bands, atr):
    bands = sorted(bands)
    out = []
    for lo, hi, n in bands:
        if out and lo - out[-1][1] < MERGE_ATR * atr and max(hi, out[-1][1]) - out[-1][0] <= MAX_WIDTH_ATR * atr:
            out[-1] = [out[-1][0], max(out[-1][1], hi), out[-1][2] + n]
        else:
            out.append([lo, hi, n])
    return [[round(a, 2), round(b, 2), n] for a, b, n in out]


def zone_maps(sym, full):
    """Merged zone ladder at each of the last BARS candles (from candles before it). Cached by date."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{sym.replace('&', '_')}.json"
    times = full["time"].tolist()
    closes = [round(x, 2) for x in full["close"].tolist()]
    known = {}
    try:
        old = json.loads(path.read_text())
        if old.get("v") == CACHE_V:
            pc = dict(zip(old["t"], old["c"]))
            if all(pc.get(t) is None or abs(pc[t] - x) <= 0.01 * max(1, abs(x)) for t, x in zip(times, closes)):
                known = dict(zip(old["t"], old["m"]))
    except Exception:
        pass
    ind, n = FullZones(), len(full)
    start = max(60, n - BARS)
    maps = []
    for k in range(start, n):
        t = times[k]
        if t in known:
            maps.append(known[t])
            continue
        c = full.iloc[max(0, k + 1 - LOOKBACK):k + 1].reset_index(drop=True)
        try:
            z = ind.zones(c)
        except Exception:
            z = None
        if z is None or z.empty:
            maps.append([])
            continue
        datr = z["dailyAtr"].iloc[0] or z["bandAtr"].iloc[0]
        maps.append(_merge([[float(r.low), float(r.high), int(r.touches)] for r in z.itertuples()], datr))
    path.write_text(json.dumps({"v": CACHE_V, "t": times[start:], "c": closes[start:], "m": maps}, separators=(",", ":")))
    return start, maps


def walk(o, h, l, c, atr, maps):
    """Visits (zone touches) and their resolution. All arrays are aligned with maps (index 0 = first analysed bar).
    A visit: {s, e, lo, hi, side (+1 tested as resistance, -1 as support), out ('D' deflect, 'B' break, None open)}."""
    n = len(c)
    visits, act, last_break = [], None, (None, 0)
    for k in range(1, n):
        a = atr[k - 1]
        if act is not None:
            lo, hi, s = act["lo"], act["hi"], act["side"]
            beyond_far = c[k] > hi + BREAK_ATR * a if s > 0 else c[k] < lo - BREAK_ATR * a
            near_out = c[k] < lo if s > 0 else c[k] > hi
            far_enough = c[k] < lo - DEFLECT_ATR * a if s > 0 else c[k] > hi + DEFLECT_ATR * a
            touching = h[k] >= lo and l[k] <= hi
            act["away"] = 0 if touching or not near_out else act.get("away", 0) + 1
            if beyond_far:
                act.update(e=k, out="B")
                last_break = (k, s)
            elif near_out and (far_enough or act["away"] >= AWAY_CLOSES):
                act.update(e=k, out="D")
            else:
                continue
            act.pop("away", None)
            visits.append(act)
            act = None
            continue
        # not in a zone: did this candle trade into one (or straight through several)?
        pc = c[k - 1]
        hits = []
        for lo, hi, _n in maps[k]:
            if h[k] < lo or l[k] > hi:
                continue
            if pc < lo:
                side = 1
            elif pc > hi:
                side = -1
            elif last_break[0] == k - 1:
                side = last_break[1]          # broke one zone and closed inside the next
            else:
                continue                      # zone appeared around price: no clear side
            hits.append((lo, hi, side))
        if not hits:
            continue
        side = hits[0][2]
        hits = sorted([x for x in hits if x[2] == side], key=lambda x: x[0] * side)
        for lo, hi, s in hits:
            if (s > 0 and c[k] > hi + BREAK_ATR * atr[k - 1]) or (s < 0 and c[k] < lo - BREAK_ATR * atr[k - 1]):
                visits.append({"s": k, "e": k, "lo": lo, "hi": hi, "side": s, "out": "B"})
                last_break = (k, s)
                continue
            act = {"s": k, "lo": lo, "hi": hi, "side": s}
            if (s > 0 and c[k] < lo - DEFLECT_ATR * atr[k - 1]) or (s < 0 and c[k] > hi + DEFLECT_ATR * atr[k - 1]):
                act.update(e=k, out="D")      # poked in and was thrown out the same day
                visits.append(act)
                act = None
            break
    if act is not None:
        act.pop("away", None)
        act.update(e=n - 1, out=None)
        visits.append(act)

    # deflections: did price reach the opposite zone before touching the deflection zone again?
    for v in visits:
        if v["out"] != "D":
            continue
        k, d = v["e"], -v["side"]
        gap = TARGET_GAP_ATR * atr[k]
        if d < 0:
            cands = [z for z in maps[k] if z[1] < v["lo"] - gap and z[1] < c[k]]
            tgt = max(cands, key=lambda z: z[1]) if cands else None
        else:
            cands = [z for z in maps[k] if z[0] > v["hi"] + gap and z[0] > c[k]]
            tgt = min(cands, key=lambda z: z[0]) if cands else None
        if not tgt:
            v["res"] = "open"                 # nothing on the other side to travel to
            continue
        v["tlo"], v["thi"] = tgt[0], tgt[1]
        edge_from, edge_to = (v["lo"], tgt[1]) if d < 0 else (v["hi"], tgt[0])
        v["move"] = round(float(abs(edge_to - edge_from) / c[k]), 4)
        v["res"] = "pending"
        for j in range(k + 1, n):
            back = h[j] >= v["lo"] if d < 0 else l[j] <= v["hi"]
            got = l[j] <= tgt[1] if d < 0 else h[j] >= tgt[0]
            if back:
                v["res"], v["r"] = "failed", j
                break
            if got:
                v["res"], v["r"] = "reached", j
                break

    # breakouts: zones broken per leg
    legs, cur = [], None
    for i, v in enumerate(visits):
        if v["out"] == "B":
            d = v["side"]
            if cur and cur["d"] == d:
                cur["n"] += 1
                cur["e"] = v["e"]
                visits[cur["first"]]["leg"] = cur["n"]
                v["in_leg"] = cur["first"]
            else:
                cur = {"d": d, "n": 1, "s": v["s"], "e": v["e"], "first": i}
                v["leg"] = 1
                legs.append(cur)
        elif v["out"] == "D" and cur and -v["side"] != cur["d"]:
            cur = None                         # rejected against the leg's direction: leg over
    return visits, legs


def stats(visits, legs, n, window, c):
    lo_i = n - window
    V = [v for v in visits if v["s"] >= lo_i and v["out"]]
    D = [v for v in V if v["out"] == "D"]
    B = [v for v in V if v["out"] == "B"]
    reached = [v for v in D if v.get("res") == "reached"]
    failed = [v for v in D if v.get("res") == "failed"]
    L = [g for g in legs if g["s"] >= lo_i]
    breaks = len(B)
    z2z = len(reached)
    resolved = z2z + len(failed)
    score = 100 * z2z / (z2z + len(failed) + breaks + 2)
    return {
        "tests": len(V), "defl": len(D), "brk": breaks, "z2z": z2z, "failed": len(failed),
        "reach": round(z2z / resolved, 3) if resolved else None,
        "defl_share": round(len(D) / len(V), 3) if V else None,
        "legs": len(L), "leg_zones": round(sum(g["n"] for g in L) / len(L), 2) if L else None,
        "leg_max": max((g["n"] for g in L), default=0),
        "days_in": median([v["e"] - v["s"] + 1 for v in V]) if V else None,
        "days_to": median([v["r"] - v["e"] for v in reached]) if reached else None,
        "move": round(float(median([v["move"] for v in reached])), 4) if reached else None,
        "score": round(score, 1),
        "ret": round(float(c[-1] / c[lo_i] - 1), 4) if lo_i >= 0 and c[lo_i] else None,
    }


def classify(s):
    """Thresholds set from the first run over the Nifty 500 (roughly the top seventh deflecting, two fifths directional)."""
    if s["tests"] < 6:
        return "thin"
    if s["z2z"] >= 4 and s["score"] >= 25 and (s["leg_zones"] or 0) < 1.6:
        return "deflecting"
    if (s["leg_zones"] or 0) >= 1.7 or s["score"] < 12:
        return "directional"
    return "mixed"


def status(visits, maps, c, dates):
    """What is happening now, in a few words, plus the zones either side of the last close."""
    n, last = len(c), c[-1]
    zs = maps[-1] if maps else []
    above = min((z for z in zs if z[0] > last), key=lambda z: z[0], default=None)
    below = max((z for z in zs if z[1] < last), key=lambda z: z[1], default=None)
    inside = next((z for z in zs if z[0] <= last <= z[1]), None)
    st = {"above": above, "below": below, "inside": inside, "kind": None, "days": None}
    if visits:
        v = visits[-1]
        ago = n - 1 - (v["e"] if v["out"] else v["s"])
        st["days"] = ago
        if v["out"] is None:
            st["kind"] = "at_res" if v["side"] > 0 else "at_sup"
            st["zone"] = [v["lo"], v["hi"]]
        elif v["out"] == "D" and ago <= 20:
            st["kind"] = "defl_res" if v["side"] > 0 else "defl_sup"
            st["zone"] = [v["lo"], v["hi"]]
            if v.get("tlo") is not None:
                st["target"] = [v["tlo"], v["thi"]]
                st["res"] = v["res"]
        elif v["out"] == "B" and ago <= 20:
            leg_first = visits[v.get("in_leg", len(visits) - 1)]
            st["kind"] = "brk_up" if v["side"] > 0 else "brk_dn"
            st["zone"] = [v["lo"], v["hi"]]
            st["leg"] = leg_first.get("leg", 1)
    return st


def segments(maps, dates):
    """Zone ladder through time as drawable bands: same band on consecutive days = one segment."""
    segs, open_ = [], {}
    for i, zs in enumerate(maps):
        nxt = {}
        for lo, hi, nt in zs:
            key = (lo, hi)
            seg = open_.get(key)
            if seg is None:
                seg = {"lo": lo, "hi": hi, "n": nt, "s": i, "e": i}
                segs.append(seg)
            seg["e"] = i
            nxt[key] = seg
        open_ = nxt
    return [[s["lo"], s["hi"], s["s"], s["e"], s["n"]] for s in segs]


def analyse(sym):
    from prices import load_prices
    p = load_prices(sym)
    full = _candles(p, LOOKBACK + BARS)
    if len(full) < 200:
        return sym, None
    start, maps = zone_maps(sym, full)
    w = full.iloc[start:].reset_index(drop=True)
    o = p["open"].reindex(p.index[-len(full):]).to_numpy(float)[start:]
    h, l, c = (w[k].to_numpy(float) for k in ("high", "low", "close"))
    o = np.where(np.isfinite(o), o, c)
    atr = _atr(h, l, c)
    dates = w["time"].tolist()
    visits, legs = walk(o, h, l, c, atr, maps)
    n = len(c)
    s1 = stats(visits, legs, n, min(YEAR, n - 1), c)
    s2 = stats(visits, legs, n, n - 1, c)
    s1["class"] = classify(s1)
    st = status(visits, maps, c, dates)
    r2 = lambda a: [round(float(x), 2) for x in a]
    ev = []
    for v in visits:
        e = {"s": v["s"], "e": v["e"], "lo": v["lo"], "hi": v["hi"], "side": v["side"], "out": v["out"]}
        for k in ("res", "r", "tlo", "thi", "move", "leg"):
            if k in v:
                e[k] = v[k]
        ev.append(e)
    detail = {"sym": sym, "t": dates, "o": r2(o), "h": r2(h), "l": r2(l), "c": r2(c),
              "zones": segments(maps, dates), "events": ev,
              "legs": [{"d": g["d"], "n": g["n"], "s": g["s"], "e": g["e"]} for g in legs],
              "y1": s1, "y2": s2, "now": st, "year_start": max(0, n - YEAR)}
    write_json(OUT / f"{sym.replace('&', '_')}.json", detail)
    return sym, {"y1": s1, "y2": s2, "now": st, "close": round(float(c[-1]), 2)}


def _safe(sym):
    try:
        return analyse(sym)
    except Exception as e:
        print("zones fail", sym, repr(e)[:200], flush=True)
        return sym, None


def build(universe: list[dict], workers: int | None = None):
    workers = workers or max(1, os.cpu_count() or 2)
    names = {u["symbol"]: u for u in universe}
    rows = []
    with ProcessPoolExecutor(workers) as ex:
        for sym, r in ex.map(_safe, list(names), chunksize=4):
            if not r:
                continue
            u = names[sym]
            rows.append({"sym": sym, "name": u.get("name") or u.get("company") or sym,
                         "sector": u.get("industry") or u.get("sector"), **r})
    dates = None
    write_json(OUT / "_all.json", {"rows": rows, "params": {
        "bars": BARS, "year": YEAR, "deflect_atr": DEFLECT_ATR, "break_atr": BREAK_ATR, "merge_atr": MERGE_ATR,
        "max_width_atr": MAX_WIDTH_ATR, "target_gap_atr": TARGET_GAP_ATR, "away_closes": AWAY_CLOSES}})
    print(f"zones: {len(rows)}/{len(names)} stocks", flush=True)
    return rows


if __name__ == "__main__":
    import sys
    from universe import refresh_universe
    uni = refresh_universe()
    if len(sys.argv) > 1:
        want = set(sys.argv[1:])
        uni = [u for u in uni if u["symbol"] in want]
    build(uni)
