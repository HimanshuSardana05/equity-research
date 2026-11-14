"""Corporate announcements, results filings and board meetings from NSE's public RSS feeds.

The feeds only hold recent items, so each run appends new items to a running history
(data/raw/events/*.jsonl) which becomes the long-term record.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime

import requests

from common import EVENTS, UA

FEEDS = {
    "announcements": "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml",
    "results": "https://nsearchives.nseindia.com/content/RSS/Financial_Results.xml",
    "board_meetings": "https://nsearchives.nseindia.com/content/RSS/Board_Meetings.xml",
}
ITEM = re.compile(r"<item>(.*?)</item>", re.S)


def _tag(block, tag):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
    if not m:
        return ""
    v = m.group(1).strip()
    v = re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", v, flags=re.S)
    return html.unescape(v).strip()


def _parse_date(s):
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%dT%H:%M")
        except Exception:
            pass
    return s.strip()


def _norm_name(s):
    s = re.sub(r"\b(ltd|limited|the)\b\.?", "", (s or "").lower())
    return re.sub(r"[^a-z0-9]", "", s)


def fetch_feeds(universe: list[dict]) -> dict:
    by_sym = {u["symbol"]: u for u in universe}
    by_name = {_norm_name(u["name"]): u["symbol"] for u in universe}
    added = {}
    for kind, url in FEEDS.items():
        path = EVENTS / f"{kind}.jsonl"
        seen = set()
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    seen.add(json.loads(line)["id"])
                except Exception:
                    pass
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
            txt = r.content.decode("utf-8", "replace")
        except Exception as e:
            print("feed fail", kind, e)
            continue
        new = []
        for block in ITEM.findall(txt):
            title, link, desc = _tag(block, "title"), _tag(block, "link"), _tag(block, "description")
            pub = _parse_date(_tag(block, "pubDate"))
            sym = None
            m = re.search(r"/corporate/([A-Z0-9&\-]+)_\d", link)
            if m and m.group(1) in by_sym:
                sym = m.group(1)
            if not sym:
                sym = by_name.get(_norm_name(title))
            if not sym:
                continue  # only keep Nifty 500 companies
            eid = f"{sym}|{pub}|{hashlib.md5(desc.encode()).hexdigest()[:10]}"
            if eid in seen:
                continue
            seen.add(eid)
            subject = ""
            ms = re.search(r"SUBJECT:\s*(.*)$", desc)
            if ms:
                subject = ms.group(1).strip()
            new.append({"id": eid, "symbol": sym, "date": pub, "subject": subject,
                        "text": re.sub(r"\s*\|SUBJECT:.*$", "", desc)[:600], "link": link})
        if new:
            with path.open("a") as f:
                for n in new:
                    f.write(json.dumps(n, ensure_ascii=False) + "\n")
        added[kind] = len(new)
    return added


def load_events(kind: str) -> list[dict]:
    path = EVENTS / f"{kind}.jsonl"
    out = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out
