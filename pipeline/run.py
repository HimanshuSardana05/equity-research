"""Pipeline entry point.

  python pipeline/run.py daily      # after market close: NSE bhavcopy prices, feeds, rotating fundamentals
  python pipeline/run.py weekly     # full price re-sync from Yahoo (re-adjusts splits) + more fundamentals
  python pipeline/run.py full       # first-time backfill of everything
  python pipeline/run.py analyze    # rebuild site data only
Options: --limit N (first N symbols, for testing)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from common import FUND, read_json
from analyze import build_all
from events import fetch_feeds, load_events
from fundamentals import refresh_fundamentals
from prices import append_bhavcopies, backfill_yf, price_path
from screener_import import import_all
from universe import refresh_universe


def stale_fundamentals(symbols, max_age_days, cap):
    now = datetime.now(timezone.utc)
    recent_results = set()
    cutoff = (now - timedelta(days=4)).strftime("%Y-%m-%d")
    for e in load_events("announcements") + load_events("results"):
        txt = (e.get("subject", "") + " " + e.get("text", "")).lower()
        if e["date"] >= cutoff and ("financial result" in txt or e.get("kind") == "results"):
            recent_results.add(e["symbol"])
    todo = []
    for s in symbols:
        d = read_json(FUND / f"{s}.json")
        if not d:
            todo.append((0, s))
            continue
        age = (now - datetime.fromisoformat(d["fetched"])).days
        if s in recent_results:
            todo.append((1, s))
        elif age >= max_age_days:
            todo.append((2 + 1 / (age + 1), s))
    todo.sort()
    return [s for _, s in todo[:cap]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["daily", "weekly", "full", "analyze"])
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    uni = refresh_universe()
    if a.limit:
        uni = uni[:a.limit]
    syms = [u["symbol"] for u in uni]
    print(f"{a.mode}: {len(syms)} symbols", flush=True)

    if a.mode in ("full", "weekly"):
        print("prices (yahoo, max history)...", flush=True)
        backfill_yf(syms, period="max")
    if a.mode == "daily":
        missing = [s for s in syms if not price_path(s).exists()]
        if missing:
            backfill_yf(missing, period="max")
        got = append_bhavcopies(syms)
        print("bhavcopy days appended:", got, flush=True)
        if not got:  # NSE unreachable -> fall back to Yahoo's recent bars
            backfill_yf(syms, period="1mo")
    if a.mode != "analyze":
        print("feeds:", fetch_feeds(uni), flush=True)
        cap = {"full": len(syms), "weekly": 200, "daily": 90}[a.mode]
        todo = stale_fundamentals(syms, max_age_days=7, cap=cap)
        print("fundamentals to refresh:", len(todo), flush=True)
        refresh_fundamentals(todo)
    print("screener imports:", import_all(), flush=True)
    n = build_all(uni)
    print("built", n, "companies", flush=True)


if __name__ == "__main__":
    sys.exit(main())
