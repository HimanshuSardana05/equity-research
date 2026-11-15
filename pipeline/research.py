"""Weekly research: which combination of factors has worked, for which kind of stock.

1. Label: every setup across the Nifty 500 (no strict filters) is run through one fixed trade rule
   (LABEL_RULE: enter on a break of the pattern high/low within 3 days, stop 0.25 ATR beyond the pattern or level,
   book half at 1R and move the stop to entry, trail the rest by 2 ATR, exit after 15 days). Result in R.
2. Features: the setup (direction, pattern, level kind and type, score parts, volume, trend, room to the next
   level, breakout distance) and the stock (sector, size, volatility, liquidity). Add new ones in FEATURES.
3. Model: one regression tree on R, pooled across all stocks (one tree per stock would overfit: most stocks
   have a handful of trades). Leaves need MIN_LEAF trades. Trained on the older ~70% of dates, tested on the
   newest ~30% (walk-forward), so every result is shown both in-sample and out-of-sample.
4. Probabilities: each leaf's win rate is shrunk towards the overall rate (PRIOR trades of weight) so small
   leaves are not over-confident; calibration is checked on the test period. Every number carries its sample
   size and a 95% range.
5. Factor value: each feature is dropped in turn and the tree refit; the change in out-of-sample results
   is that feature's utility.

Output: data/cache/research.json (kept between runs, copied into the site) - and live_predictions() applies the
tree to recent setups every day.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setups import PATTERNS, load_rules, score  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
OUT = CACHE / "research.json"
CFG = ROOT / "config" / "research.json"

LABEL_RULE = {"entry_days": 3, "stop_atr": 0.25, "partial_r": 1.0, "partial_frac": 0.5, "be": True,
              "trail_atr": 2.0, "max_days": 15}
DEFAULT_CFG = {"min_leaf": 150, "max_depth": 4, "test_share": 0.3, "prior": 40,
               "alert": {"min_exp_r": 0.15, "min_prob": 0.5, "require_oos_positive": True}}


def load_cfg() -> dict:
    c = json.loads(json.dumps(DEFAULT_CFG))
    try:
        u = json.loads(CFG.read_text())
        for k, v in u.items():
            c[k] = {**c[k], **v} if isinstance(v, dict) and isinstance(c.get(k), dict) else v
    except (OSError, ValueError):
        pass
    return c


# ------------------------------------------------------------------ label: one trade per setup
def simulate(c: dict, P: dict = LABEL_RULE):
    """Same logic as btSim in app/page.html (break entry). Returns R, or None if never entered / still open."""
    d, p = c["dir"], c["path"] or []
    nb = len(p) // 4
    B = lambda k: (p[4 * k], p[4 * k + 1], p[4 * k + 2], p[4 * k + 3])
    trig = c["phi"] if d > 0 else c["plo"]
    ek = entry = None
    for k in range(min(P["entry_days"], nb)):
        o, h, l, cl = B(k)
        if h is None or l is None:
            continue
        if (d > 0 and h > trig) or (d < 0 and l < trig):
            ek, entry = k, (max(o if o is not None else trig, trig) if d > 0 else min(o if o is not None else trig, trig))
            break
    if ek is None:
        return None
    stop0 = (min(c["plo"], c["zlo"]) - P["stop_atr"] * c["atr"]) if d > 0 else (max(c["phi"], c["zhi"]) + P["stop_atr"] * c["atr"])
    risk = d * (entry - stop0)
    if not risk > 0:
        return None
    tgt = c["tgt"] if c["tgt"] is not None and d * (c["tgt"] - entry) > 0 else None
    st, rem, R, booked, ext = stop0, 1.0, 0.0, False, entry
    last = min(nb, ek + P["max_days"])
    for k in range(ek, last):
        o, h, l, cl = B(k)
        if h is None or l is None:
            continue
        if (d > 0 and l <= st) or (d < 0 and h >= st):
            px = st if k == ek or o is None else (min(o, st) if d > 0 else max(o, st))
            return R + rem * d * (px - entry) / risk
        if not booked:
            lv = entry + d * P["partial_r"] * risk
            if (d > 0 and h >= lv) or (d < 0 and l <= lv):
                R += P["partial_frac"] * P["partial_r"]
                rem -= P["partial_frac"]
                booked = True
                if P["be"]:
                    st = max(st, entry) if d > 0 else min(st, entry)
        if tgt is not None and ((d > 0 and h >= tgt) or (d < 0 and l <= tgt)):
            return R + rem * d * (tgt - entry) / risk
        ext = max(ext, h) if d > 0 else min(ext, l)
        if booked and P["trail_atr"] > 0:
            ts = ext - d * P["trail_atr"] * c["atr"]
            st = max(st, ts) if d > 0 else min(st, ts)
    if ek + P["max_days"] > nb:
        return None  # still open
    cl = B(last - 1)[3]
    return R + rem * d * ((cl if cl is not None else entry) - entry) / risk


# ------------------------------------------------------------------ features (add new ones here)
def _with(direction, x):
    """'with' when the setup's direction agrees with the sign, else 'against' (None if unknown)."""
    if x is None:
        return None
    return "with" if x * direction > 0 else "against"


FEATURES = {
    # name: (kind, label, function(candidate, stock) -> value)
    "dir": ("cat", "Direction", lambda c, s: "buy" if c["dir"] > 0 else "sell"),
    "pattern": ("cat", "Pattern", lambda c, s: PATTERNS[c["pattern"]][0]),
    "kind": ("cat", "Level interaction", lambda c, s: "breakout" if c["kind"] else "level test"),
    "level_type": ("cat", "Level type", lambda c, s: "swing" if c["lt"] else "zone"),
    "score": ("num", "Setup score", lambda c, s: c["score"]),
    "level_strength": ("num", "Level strength", lambda c, s: c["zs"]),
    "pattern_quality": ("num", "Pattern quality", lambda c, s: c["strength"]),
    "volume": ("num", "Volume vs 20-day avg", lambda c, s: c["vol"]),
    "trend50": ("cat", "Price vs 50-day avg", lambda c, s: _with(c["dir"], -c["a50"]) if c["a50"] is not None else None),
    "slope50": ("cat", "50-day avg direction", lambda c, s: _with(c["dir"], c["s50"]) if c["s50"] else None),
    "trend200": ("cat", "Price vs 200-day avg", lambda c, s: _with(c["dir"], -c["a200"]) if c["a200"] is not None else None),
    "room": ("num", "Reward:risk to next level", lambda c, s: c["room"]),
    "breakout_dist": ("num", "Breakout distance (ATR)", lambda c, s: c["brk"] if c["kind"] else None),
    "touch": ("num", "Touch distance (ATR)", lambda c, s: c["tol"]),
    "volatility": ("num", "Daily range (ATR %)", lambda c, s: c["atr"] / 100),
    "liquidity": ("num", "Traded value (log10 ₹ cr)", lambda c, s: c.get("liq")),
    "size": ("cat", "Company size", lambda c, s: s.get("size")),
    "sector": ("cat", "Sector", lambda c, s: s.get("sector")),
}


def _room(c):
    d = c["dir"]
    trig = c["phi"] if d > 0 else c["plo"]
    stop = (min(c["plo"], c["zlo"]) - 0.25 * c["atr"]) if d > 0 else (max(c["phi"], c["zhi"]) + 0.25 * c["atr"])
    risk = d * (trig - stop)
    if c["tgt"] is None or not risk > 0:
        return 10.0  # nothing in the way: treated as plenty of room
    return round(max(0.0, min(10.0, d * (c["tgt"] - trig) / risk)), 2)


def _long_rows(sym):
    """Setups and trade candidates over the 10-year level history of one stock (data/cache/levels_long)."""
    from levels import LONG_CACHE
    from prices import load_prices
    from signals import candidates
    from setups import find_setups
    p = LONG_CACHE / f"{sym.replace('&', '_')}.json"
    if not p.exists():
        return sym, None
    try:
        h = (json.loads(p.read_text()).get("daily")) or {}
        prices = load_prices(sym)
        return sym, candidates(prices, h, find_setups(prices, h))
    except Exception as e:
        print("research rows fail", sym, repr(e)[:200])
        return sym, None


def collect_long(syms: list[str], workers: int | None = None) -> dict:
    import os
    from concurrent.futures import ProcessPoolExecutor
    out = {}
    with ProcessPoolExecutor(workers or max(1, os.cpu_count() or 2)) as ex:
        for sym, rows in ex.map(_long_rows, syms, chunksize=4):
            if rows is not None:
                out[sym] = rows
    return out


def load_candidates(long: bool = True):
    D = json.loads((CACHE / "candidates_all.json").read_text())
    if long:
        # stocks with a 10-year level history use it; the rest keep their ~2-year candidates
        L = collect_long(D["syms"])
        if L:
            from signals import COLS as SC
            assert SC == D["cols"], "candidate columns changed"
            keep = [r for r in D["rows"] if D["syms"][r[0]] not in L]
            for sym, rows in L.items():
                i = D["syms"].index(sym)
                for r in rows:
                    r[0] = i
                keep.extend(rows)
            D["rows"] = keep
        D["long_stocks"] = len(L)
    rules = load_rules()
    cols = D["cols"]
    stocks = D.get("stocks", {})
    # stock traits: size by market-cap rank, sector with small sectors grouped
    caps = sorted(((v.get("mcap") or 0), k) for k, v in stocks.items())
    n = len(caps)
    size = {}
    for r, (_, k) in enumerate(reversed(caps)):
        size[k] = "large (top 100)" if r < 100 else ("mid (101-250)" if r < 250 else "small (251+)")
    from collections import Counter
    cnt = Counter(v.get("industry") or "Other" for v in stocks.values())
    sector = {k: ((v.get("industry") or "Other") if cnt[v.get("industry") or "Other"] >= 8 else "Other") for k, v in stocks.items()}
    out = []
    for row in D["rows"]:
        c = dict(zip(cols, row))
        c["sym"] = D["syms"][c["s"]]
        c["pattern"] = D["patterns"][c["p"]]
        c["dir"] = PATTERNS[c["pattern"]][1]
        c["score"] = score({"zs": c["zs"] or 0, "strength": c["strength"], "vol": c["vol"], "trend": c["trend"]}, rules)
        c["room"] = _room(c)
        st = {"size": size.get(c["sym"]), "sector": sector.get(c["sym"])}
        c["x"] = {k: f[2](c, st) for k, f in FEATURES.items()}
        out.append(c)
    attr = {k: {"size": size.get(k), "sector": sector.get(k)} for k in stocks}
    return D, out, stocks, attr


# ------------------------------------------------------------------ a small CART regression tree
class Data:
    """Feature columns as numpy arrays, for fast splitting."""
    def __init__(self, rows):
        self.y = np.array([r["R"] for r in rows], dtype=float)
        self.cols = {}
        for f, (kind, _, _) in FEATURES.items():
            vals = [r["x"][f] for r in rows]
            if kind == "num":
                self.cols[f] = np.array([np.nan if v is None else float(v) for v in vals])
            else:
                self.cols[f] = np.array(["" if v is None else str(v) for v in vals], dtype=object)


def _candidate_splits(f, col):
    if FEATURES[f][0] == "num":
        v = col[~np.isnan(col)]
        if len(v) < 10:
            return []
        return [("num", f, float(q)) for q in sorted(set(np.round(np.quantile(v, np.linspace(0.1, 0.9, 9)), 3)))]
    cats = sorted(set(col) - {""})
    return [("cat", f, c) for c in cats] if len(cats) > 1 else []


def build_tree(data, feats, cfg, idx=None, depth=0):
    idx = np.arange(len(data.y)) if idx is None else idx
    y = data.y[idx]
    node = {"n": int(len(idx)), "avg": round(float(y.mean()), 3), "win": round(float((y > 0).mean()), 3)}
    ml = cfg["min_leaf"]
    if depth >= cfg["max_depth"] or len(idx) < 2 * ml:
        return node
    base = ((y - y.mean()) ** 2).sum()
    best = None
    for f in feats:
        col = data.cols[f][idx]
        for sp in _candidate_splits(f, col):
            if sp[0] == "num":
                known = ~np.isnan(col)
                L = known & (col < sp[2])
            else:
                known = col != ""
                L = col == sp[2]
            Rk = known & ~L
            miss_left = L.sum() >= Rk.sum()
            Lm = L | (~known if miss_left else False)
            if Lm.sum() < ml or (~Lm).sum() < ml:
                continue
            yl, yr = y[Lm], y[~Lm]
            gain = base - (((yl - yl.mean()) ** 2).sum() + ((yr - yr.mean()) ** 2).sum())
            if best is None or gain > best[0]:
                best = (gain, sp, bool(miss_left), Lm)
    if best is None or best[0] <= base * 0.002:
        return node
    _, sp, miss_left, Lm = best
    node.update({"split": list(sp), "miss_left": miss_left,
                 "left": build_tree(data, feats, cfg, idx[Lm], depth + 1),
                 "right": build_tree(data, feats, cfg, idx[~Lm], depth + 1)})
    return node


def _goes_left(split, x):
    kind, name, v = split
    val = x[name]
    if val is None:
        return None
    return float(val) < v if kind == "num" else str(val) == v


def _used(node, acc=None):
    acc = set() if acc is None else acc
    if "split" in node:
        acc.add(node["split"][1])
        _used(node["left"], acc)
        _used(node["right"], acc)
    return acc


def leaf_of(node, x, path=""):
    while "split" in node:
        g = _goes_left(tuple(node["split"]), x)
        go_left = g if g is not None else node["miss_left"]
        path += "L" if go_left else "R"
        node = node["left"] if go_left else node["right"]
    return path


def _leaves(node, path="", rules=()):
    if "split" not in node:
        yield path, node, list(rules)
        return
    kind, name, v = node["split"]
    lab = FEATURES[name][1]
    if kind == "num":
        l, r = f"{lab} < {v:g}", f"{lab} ≥ {v:g}"
    else:
        l, r = f"{lab}: {v}", f"{lab}: not {v}"
    yield from _leaves(node["left"], path + "L", rules + (l,))
    yield from _leaves(node["right"], path + "R", rules + (r,))


# ------------------------------------------------------------------ statistics
def wilson(w, n, z=1.96):
    if n == 0:
        return [None, None]
    p = w / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(c - h, 3), round(c + h, 3)]


def mean_ci(y):
    y = np.asarray(y, dtype=float)
    if len(y) < 2:
        return [None, None]
    m, se = y.mean(), y.std(ddof=1) / math.sqrt(len(y))
    return [round(m - 1.96 * se, 3), round(m + 1.96 * se, 3)]


def stats(y):
    y = np.asarray(y, dtype=float)
    if not len(y):
        return {"n": 0}
    pos, neg = y[y > 0].sum(), -y[y <= 0].sum()
    return {"n": int(len(y)), "avg": round(float(y.mean()), 3), "win": round(float((y > 0).mean()), 3),
            "avg_ci": mean_ci(y), "win_ci": wilson(int((y > 0).sum()), len(y)),
            "pf": round(float(pos / neg), 2) if neg > 0 else None}


def leaf_table(tree, train, test, everything, prior, p0, m0):
    by = lambda rows: {k: [r for r in rows if r["leaf"] == k] for k in {r["leaf"] for r in rows}}
    for rows in (train, test, everything):
        for r in rows:
            r["leaf"] = leaf_of(tree, r["x"])
    tr, te, al = by(train), by(test), by(everything)
    out = []
    for path, node, rules in _leaves(tree):
        a = [r["R"] for r in al.get(path, [])]
        w = sum(1 for x in a if x > 0)
        p = (w + prior * p0) / (len(a) + prior) if a else p0
        e = (sum(a) + prior * m0) / (len(a) + prior) if a else m0
        out.append({"path": path, "rules": rules,
                    "train": stats([r["R"] for r in tr.get(path, [])]), "test": stats([r["R"] for r in te.get(path, [])]),
                    "all": stats(a), "prob": round(p, 3), "prob_ci": wilson(w, len(a)), "exp_r": round(e, 3),
                    "dirs": sorted({r["x"]["dir"] for r in al.get(path, [])})})
        out[-1]["verdict"] = verdict(out[-1]["train"], out[-1]["test"])
    return out


def verdict(tr, te, min_test=30):
    """70:30 overfitting check for one group: does the in-sample result survive the newest 30% of dates?"""
    if not tr.get("n") or tr["avg"] <= 0:
        return "negative"
    if not te.get("n") or te["n"] < min_test:
        return "thin"
    if te["avg"] <= 0:
        return "degrades"
    if te["avg"] < 0.5 * tr["avg"]:
        return "weaker"
    return "holds"


def _predict_prob(leaves, rows):
    m = {l["path"]: l for l in leaves}
    return [m[r["leaf"]] for r in rows]


def evaluate(tree, train, test, prior):
    """Out-of-sample quality: Brier score of the win probability, and the result of trading only the leaves the
    training period called positive."""
    p0 = float(np.mean([r["R"] > 0 for r in train]))
    m0 = float(np.mean([r["R"] for r in train]))
    for r in train + test:
        r["leaf"] = leaf_of(tree, r["x"])
    g = {}
    for r in train:
        g.setdefault(r["leaf"], []).append(r["R"])
    pw = {k: (sum(1 for x in v if x > 0) + prior * p0) / (len(v) + prior) for k, v in g.items()}
    er = {k: (sum(v) + prior * m0) / (len(v) + prior) for k, v in g.items()}
    probs = np.array([pw.get(r["leaf"], p0) for r in test])
    wins = np.array([r["R"] > 0 for r in test], dtype=float)
    brier = float(((probs - wins) ** 2).mean()) if len(test) else None
    brier0 = float(((p0 - wins) ** 2).mean()) if len(test) else None
    picked = [r["R"] for r in test if er.get(r["leaf"], m0) > 0]
    picked_tr = [r["R"] for r in train if er.get(r["leaf"], m0) > 0]
    return {"brier": brier, "brier_base": brier0, "picked": stats(picked), "picked_train": stats(picked_tr), "all_test": stats([r["R"] for r in test]),
            "probs": probs, "wins": wins}


# ------------------------------------------------------------------ run
def run():
    cfg = load_cfg()
    D, cands, stocks, attr = load_candidates()
    rows = []
    for c in cands:
        R = simulate(c)
        if R is not None:
            c["R"] = float(R)
            rows.append(c)
    rows.sort(key=lambda r: r["date"])
    dates = sorted({r["date"] for r in rows})
    split = dates[int(len(dates) * (1 - cfg["test_share"]))]
    train = [r for r in rows if r["date"] < split]
    test = [r for r in rows if r["date"] >= split]
    feats = list(FEATURES)
    dtrain = Data(train)
    tree = build_tree(dtrain, feats, cfg)
    p0 = float(np.mean([r["R"] > 0 for r in rows]))
    m0 = float(np.mean([r["R"] for r in rows]))
    leaves = leaf_table(tree, train, test, rows, cfg["prior"], p0, m0)
    ev = evaluate(tree, train, test, cfg["prior"])
    # calibration on the test period
    cal = []
    for lo in np.arange(0.2, 0.8, 0.1):
        m = (ev["probs"] >= lo) & (ev["probs"] < lo + 0.1)
        if m.sum():
            cal.append({"from": round(float(lo), 1), "to": round(float(lo + 0.1), 1), "n": int(m.sum()),
                        "predicted": round(float(ev["probs"][m].mean()), 3), "actual": round(float(ev["wins"][m].mean()), 3)})
    # value of each factor: drop it, refit, compare out-of-sample
    fv = []
    used = _used(tree)
    for f in feats:
        t2 = build_tree(dtrain, [x for x in feats if x != f], cfg)
        e2 = evaluate(t2, train, test, cfg["prior"])
        fv.append({"feature": f, "label": FEATURES[f][1],
                   "brier_change": round((e2["brier"] - ev["brier"]) * 1000, 2) if ev["brier"] is not None else None,
                   "picked_avg_with": ev["picked"].get("avg"), "picked_avg_without": e2["picked"].get("avg"),
                   "picked_n_without": e2["picked"].get("n"), "used": f in used})
    fv.sort(key=lambda x: -(x["brier_change"] or 0))
    # single-factor view: average result by each value / band of every feature
    single = []
    for f in feats:
        kind = FEATURES[f][0]
        vals = [r["x"][f] for r in rows]
        groups = {}
        if kind == "num":
            v = np.array([x for x in vals if x is not None], dtype=float)
            if len(v) < 50:
                continue
            qs = np.unique(np.round(np.quantile(v, [0.2, 0.4, 0.6, 0.8]), 3))
            for r in rows:
                x = r["x"][f]
                if x is None:
                    continue
                i = int(np.searchsorted(qs, x, side="right"))
                lab = (f"< {qs[0]:g}" if i == 0 else (f"≥ {qs[-1]:g}" if i == len(qs) else f"{qs[i-1]:g} to {qs[i]:g}"))
                groups.setdefault((i, lab), []).append(r["R"])
        else:
            for r in rows:
                x = r["x"][f]
                if x is not None:
                    groups.setdefault((0, str(x)), []).append(r["R"])
        single.append({"feature": f, "label": FEATURES[f][1],
                       "bands": [{"value": k[1], **stats(v)} for k, v in sorted(groups.items(), key=lambda kv: (kv[0][0], -len(kv[1])))]})
    # per stock and per sector
    per_stock = {}
    for r in rows:
        per_stock.setdefault(r["sym"], []).append(r)
    stock_tab = []
    lmap = {l["path"]: l for l in leaves}
    for s, rs in per_stock.items():
        st = stats([r["R"] for r in rs])
        # best condition for this stock chosen on the training dates only, then checked on the test dates
        best = {}
        for r in rs:
            if r["date"] < split:
                best.setdefault(r["leaf"], []).append(r["R"])
        bl = max(best.items(), key=lambda kv: (np.mean(kv[1]) if len(kv[1]) >= 3 else -9)) if best else (None, [])
        tr_ = stats([r["R"] for r in rs if r["date"] < split])
        te_ = stats([r["R"] for r in rs if r["date"] >= split])
        bt = stats([r["R"] for r in rs if r["leaf"] == bl[0] and r["date"] >= split])
        stock_tab.append({"symbol": s, "name": (stocks.get(s) or {}).get("name"), **st, "train": tr_, "test": te_,
                          "verdict": verdict(tr_, te_, 10),
                          "buy": stats([r["R"] for r in rs if r["dir"] > 0]), "sell": stats([r["R"] for r in rs if r["dir"] < 0]),
                          "best_leaf": bl[0], "best_leaf_n": len(bl[1]),
                          "best_leaf_avg": round(float(np.mean(bl[1])), 3) if bl[1] else None, "best_leaf_test": bt,
                          "best_leaf_rules": lmap[bl[0]]["rules"] if bl[0] in lmap else []})
    stock_tab.sort(key=lambda x: -x["n"])
    out = {
        "run": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "as_of": D.get("as_of"),
        "label_rule": LABEL_RULE, "config": cfg, "split_date": split, "period": [dates[0], dates[-1]],
        "candidates": len(cands), "long_stocks": D.get("long_stocks", 0), "stocks_n": len({c["sym"] for c in cands}),
        "price_basis": "split/bonus-adjusted prices (Yahoo); trade results also dividend-adjusted (Yahoo Adj Close factor)",
        "labelled": len(rows), "train_n": len(train), "test_n": len(test),
        "overall": {"all": stats([r["R"] for r in rows]), "train": stats([r["R"] for r in train]), "test": stats([r["R"] for r in test])},
        "base": {"p0": round(p0, 3), "m0": round(m0, 3)},
        "tree": tree, "leaves": sorted(leaves, key=lambda l: -(l["test"].get("avg") if l["test"].get("n") else -9)),
        "eval": {k: v for k, v in ev.items() if k not in ("probs", "wins")}, "calibration": cal,
        "factor_value": fv, "single": single, "stocks": stock_tab,
        "features": {k: {"kind": v[0], "label": v[1]} for k, v in FEATURES.items()}, "stock_attr": attr,
        "zones": factor_zones(rows, split),
    }
    OUT.write_text(json.dumps(out, separators=(",", ":"), default=float))
    try:
        export_explorer(cands, split, attr, stocks)
    except Exception as e:
        print("explorer export failed:", repr(e))
    print(f"research: {len(rows)} labelled trades, tree with {len(leaves)} leaves, test avg {out['overall']['test'].get('avg')}R, "
          f"picked {ev['picked'].get('n')} avg {ev['picked'].get('avg')}R, brier {ev['brier']:.4f} vs {ev['brier_base']:.4f}")
    return out


# ------------------------------------------------------------------ validated 2-factor zones (back the Setups calls)
ZONE_BINS = {"score": [40, 50, 60, 70], "room": [0.5, 1, 1.5, 2, 3], "volume": [0.75, 1, 1.5, 2],
             "pattern_quality": [0.5, 0.7, 0.9], "touch": [0.1, 0.2], "breakout_dist": [0.25, 0.5, 1]}  # = EX_BINS in page.html


def _edges(f, vals):
    if f in ZONE_BINS:
        return ZONE_BINS[f]
    v = sorted(x for x in vals if x is not None)
    if not v:
        return []
    return sorted({float(f"{v[int(i * len(v) / 4)]:.3g}") for i in range(1, 4)})


def _fmt(x):
    return f"{x:.3g}"


def factor_zones(rows, split, min_n=50, min_test=25):
    """Every pair of factors with their steps (as in the Explorer); keep the zones that were positive on the older
    70% of dates and still positive on the newest 30%."""
    feats = list(FEATURES)
    n = len(rows)
    R = np.array([r["R"] for r in rows])
    tr = np.array([r["date"] < split for r in rows])
    codes, defs = {}, {}
    for f in feats:
        vals = [r["x"][f] for r in rows]
        if FEATURES[f][0] == "cat":
            cats = sorted({str(x) for x in vals if x is not None})
            if len(cats) < 2:
                continue
            ix = {c: i for i, c in enumerate(cats)}
            codes[f] = np.array([ix[str(x)] if x is not None else -1 for x in vals])
            defs[f] = [{"v": c, "label": c} for c in cats]
        else:
            e = _edges(f, vals)
            if not e:
                continue
            codes[f] = np.array([-1 if x is None else int(np.searchsorted(e, x, side="right")) for x in vals])
            lab = [f"< {_fmt(e[0])}"] + [f"{_fmt(e[i])} to {_fmt(e[i + 1])}" for i in range(len(e) - 1)] + [f"≥ {_fmt(e[-1])}"]
            defs[f] = [{"lo": (e[i - 1] if i > 0 else None), "hi": (e[i] if i < len(e) else None), "label": lab[i]} for i in range(len(e) + 1)]
    fs = list(codes)
    out = []
    for a in range(len(fs)):
        for b in range(a + 1, len(fs)):
            f, g = fs[a], fs[b]
            cf, cg = codes[f], codes[g]
            K = len(defs[g])
            ok = (cf >= 0) & (cg >= 0)
            key = np.where(ok, cf * K + cg, -1)
            size = len(defs[f]) * K
            def agg(mask):
                k = key[mask & ok]
                r = R[mask & ok]
                return (np.bincount(k, minlength=size), np.bincount(k, weights=r, minlength=size),
                        np.bincount(k, weights=np.where(r > 0, r, 0), minlength=size), np.bincount(k, weights=np.where(r > 0, 1, 0), minlength=size))
            tn, ts, tp, tw = agg(tr)
            en, es, ep, ew = agg(~tr)
            for k in np.nonzero((tn + en >= min_n) & (tn > 0) & (en >= min_test))[0]:
                ta, ea = ts[k] / tn[k], es[k] / en[k]
                if ta <= 0 or ea <= 0:
                    continue
                N = tn[k] + en[k]
                pos = tp[k] + ep[k]
                neg = pos - (ts[k] + es[k])
                i, j = divmod(int(k), K)
                out.append({"f": [f, g], "c": [defs[f][i], defs[g][j]],
                            "label": f"{FEATURES[f][1]}: {defs[f][i]['label']} · {FEATURES[g][1]}: {defs[g][j]['label']}",
                            "n": int(N), "avg": round(float((ts[k] + es[k]) / N), 3), "win": round(float((tw[k] + ew[k]) / N), 3),
                            "pf": round(float(pos / neg), 2) if neg > 0 else None,
                            "tn": int(tn[k]), "ta": round(float(ta), 3), "en": int(en[k]), "ea": round(float(ea), 3)})
    out.sort(key=lambda z: -min(z["ta"], z["ea"]) * math.sqrt(z["n"]))
    return out


def _in_zone(z, x):
    for f, c in zip(z["f"], z["c"]):
        v = x.get(f)
        if v is None:
            return False
        if "v" in c:
            if str(v) != c["v"]:
                return False
        elif (c["lo"] is not None and v < c["lo"]) or (c["hi"] is not None and v >= c["hi"]):
            return False
    return True


# ------------------------------------------------------------------ factor explorer files (Research > Explorer)
EXPLORE = CACHE / "explore"
SIM_KEYS = ("atr", "phi", "plo", "zlo", "zhi", "tgt")


def _num(v, d=3):
    return None if v is None else round(float(v), d)


def export_explorer(cands, split, attr, stocks):
    """Every setup with all its factor values, so the browser can slice any combination of factors without tokens.
    One file per stock (with price paths, so trade rules can be changed in the browser) and one pooled file
    (factor values and the result under the standard rule)."""
    import shutil
    if EXPLORE.exists():
        shutil.rmtree(EXPLORE)
    EXPLORE.mkdir(parents=True)
    feats = list(FEATURES)
    cats = {f: sorted({str(c["x"][f]) for c in cands if c["x"][f] is not None}) for f in feats if FEATURES[f][0] == "cat"}
    enc = lambda f, v: None if v is None else (cats[f].index(str(v)) if f in cats else _num(v))
    by = {}
    for c in cands:
        by.setdefault(c["sym"], []).append(c)
    index = []
    for sym, cs in by.items():
        cs.sort(key=lambda c: c["date"])
        Rs = [simulate(c) for c in cs]
        d = {"symbol": sym, "name": (stocks.get(sym) or {}).get("name"), "attr": attr.get(sym, {}),
             "dates": [c["date"] for c in cs], "R": [_num(r) for r in Rs],
             "x": {f: [enc(f, c["x"][f]) for c in cs] for f in feats},
             "sim": {**{k: [c[k] for c in cs] for k in SIM_KEYS}, "dir": [c["dir"] for c in cs], "path": [c["path"] for c in cs]}}
        (EXPLORE / f"{sym.replace('&', '_')}.json").write_text(json.dumps(d, separators=(",", ":")))
        rr = [r for r in Rs if r is not None]
        index.append({"symbol": sym, "name": d["name"], "sector": attr.get(sym, {}).get("sector"), "n": len(rr), "from": cs[0]["date"],
                      "avg": _num(np.mean(rr)) if rr else None,
                      "vol": _num(np.median([c["x"]["volatility"] for c in cs if c["x"]["volatility"] is not None]) if cs else None, 2)})
    lab = [c for c in cands if c.get("R") is not None]
    syms = sorted(by)
    si = {s_: i for i, s_ in enumerate(syms)}
    pooled = {"syms": syms, "dates": [c["date"] for c in lab], "s": [si[c["sym"]] for c in lab], "R": [_num(c["R"]) for c in lab],
              "x": {f: [enc(f, c["x"][f]) for c in lab] for f in feats}}
    (EXPLORE / "_all.json").write_text(json.dumps(pooled, separators=(",", ":")))
    meta = {"split": split, "features": {f: {"kind": FEATURES[f][0], "label": FEATURES[f][1], **({"cats": cats[f]} if f in cats else {})} for f in feats},
            "label_rule": LABEL_RULE, "stocks": sorted(index, key=lambda z: z["symbol"])}
    (EXPLORE / "_meta.json").write_text(json.dumps(meta, separators=(",", ":")))
    print(f"explorer: {len(by)} stock files, pooled {len(lab)} trades")


# ------------------------------------------------------------------ daily: apply the weekly tree to recent setups
def live_predictions(recent_rows: list[list], cols: list[str], syms: list[str], patterns: list[str]) -> dict | None:
    if not OUT.exists():
        return None
    R = json.loads(OUT.read_text())
    cfg = load_cfg()
    rules = load_rules()
    lmap = {l["path"]: l for l in R["leaves"]}
    out = []
    for row in recent_rows:
        c = dict(zip(cols, row))
        c["sym"], c["pattern"] = syms[c["s"]], patterns[c["p"]]
        c["dir"] = PATTERNS[c["pattern"]][1]
        c["score"] = score({"zs": c["zs"] or 0, "strength": c["strength"], "vol": c["vol"], "trend": c["trend"]}, rules)
        c["room"] = _room(c)
        st = R.get("stock_attr", {}).get(c["sym"], {})
        x = {k: f[2](c, st) for k, f in FEATURES.items()}
        leaf = lmap.get(leaf_of(R["tree"], x))
        if not leaf:
            continue
        a = cfg["alert"]
        ok = (leaf["exp_r"] >= a["min_exp_r"] and leaf["prob"] >= a["min_prob"]
              and (not a["require_oos_positive"] or leaf.get("verdict") in ("holds", "weaker")))
        zs = [i for i, z in enumerate(R.get("zones", [])) if _in_zone(z, x)]
        out.append({"symbol": c["sym"], "date": c["date"], "pattern": c["pattern"], "dir": c["dir"], "score": c["score"],
                    "kind": "break" if c["kind"] else "test", "zones": zs,
                    "leaf": leaf["path"], "prob": leaf["prob"], "prob_ci": leaf["prob_ci"], "exp_r": leaf["exp_r"],
                    "exp_ci": leaf["all"].get("avg_ci"), "n": leaf["all"].get("n"), "train": leaf["train"], "test": leaf["test"],
                    "verdict": leaf.get("verdict"), "alert": bool(ok),
                    "rules": leaf["rules"]})
    out.sort(key=lambda z: (z["date"], z["exp_r"]), reverse=True)
    return {"run": R["run"], "predictions": out, "zones": R.get("zones", []), "split": R.get("split_date")}


if __name__ == "__main__":
    run()
