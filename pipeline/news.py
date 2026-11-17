"""Recent news headlines for every company, from Google News RSS search (free, no key).

Each run fetches the last 30 days of headlines per company, keeps the ones that name it,
merges them into data/raw/news/{SYM}.json (90 days of history) and tags each with a theme,
so the site can show a short "in brief" summary with links to the original sources.
"""
from __future__ import annotations

import html
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests

from common import RAW, UA

NEWS = RAW / "news"
NEWS.mkdir(parents=True, exist_ok=True)
KEEP_DAYS, KEEP_MAX = 90, 60
ITEM = re.compile(r"<item>(.*?)</item>", re.S)

THEMES = [  # first match wins, so the more specific themes come first
    ("Results", r"\b(results?|q[1-4]|quarter|net profit|profit|revenue|earnings|ebitda|margins?|pat|loss)\b"),
    ("Dividend and capital", r"\b(dividend|bonus|split|buyback|qip|rights issue|fund ?rais\w*|ncd|ipo|ofs|block deal|bulk deal|promoter)\b"),
    ("Orders and deals", r"\b(orders?|contracts?|acquir\w*|acquisition|stake|deal|merger|demerger|jv|joint venture|partnership|tie-?up|wins?|bags?|secures?|launch\w*|plant|capex|expansion)\b"),
    ("Analyst views", r"\b(target|rating|brokerage|upgrade\w*|downgrade\w*|outperform|underperform|overweight|underweight|buy|sell|accumulate|neutral|coverage)\b"),
    ("Management", r"\b(ceo|md|cfo|coo|chairman|chairperson|director|resign\w*|appoint\w*|steps down|board|succession)\b"),
    ("Regulatory and legal", r"\b(sebi|rbi|tax|gst|penalty|fine|court|nclt|probe|notice|raid|ban|licen[cs]e|approval|usfda|fda|warning letter|recall)\b"),
    ("Share price", r"\b(shares?|stocks?|surge\w*|jump\w*|fall\w*|slump\w*|rall\w*|52-week|hits?|ris\w*|drops?|gains?|tumbles?|soars?|plunges?|sensex|nifty)\b"),
]


def short_name(name: str) -> str:
    s = re.sub(r"\s*\((india|i)\)\s*", " ", name, flags=re.I)
    s = re.sub(r"[\s,.]*\b(ltd|limited|ltd\.)\.?$", "", s.strip(), flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" .,")


def theme(title: str) -> str:
    t = title.lower()
    for k, rx in THEMES:
        if re.search(rx, t):
            return k
    return "Other"


def _tag(block, tag):
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S)
    if not m:
        return ""
    v = re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", m.group(1).strip(), flags=re.S)
    return html.unescape(v).strip()


def _names(u: dict) -> list[str]:
    sn = short_name(u["name"])
    out = {sn.lower()}
    w = sn.split()
    if len(w) >= 3:
        out.add(" ".join(w[:2]).lower())
    return [x for x in out if len(x) >= 3]


def parse(xml: str, u: dict) -> list[dict]:
    names = _names(u)
    rx = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(n) for n in names) + r")(?![a-z0-9])", re.I)
    sym = re.compile(r"(?<![A-Za-z0-9])" + re.escape(u["symbol"]) + r"(?![A-Za-z0-9])")  # symbol only in capitals
    out = []
    for b in ITEM.findall(xml):
        title, link, src = _tag(b, "title"), _tag(b, "link"), _tag(b, "source")
        if not title or not link.startswith("http"):
            continue
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3].strip()
        if not (rx.search(title) or sym.search(title)):
            continue  # the headline has to name the company
        try:
            d = parsedate_to_datetime(_tag(b, "pubDate")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
        except Exception:
            continue
        out.append({"t": title, "src": src, "url": link, "d": d, "theme": theme(title)})
    return out


def _key(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())[:70]


def fetch_one(u: dict, session: requests.Session) -> int:
    sn, sym = short_name(u["name"]), u["symbol"]
    who = f'"{sn}"' if sym.lower() == sn.lower() or not sym.isalpha() or len(sym) < 3 else f'("{sn}" OR "{sym}")'
    q = f'{who} (shares OR stock OR NSE OR BSE OR results OR company) when:30d'
    url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"
    xml = None
    for attempt in range(3):
        try:
            r = session.get(url, headers=UA, timeout=20)
            if r.status_code == 200:
                xml = r.text
                break
            time.sleep(2 + attempt * 4 + random.random())
        except Exception:
            time.sleep(2 + attempt * 4)
    if xml is None:
        return -1
    path = NEWS / (u["symbol"].replace("&", "_") + ".json")
    old = []
    if path.exists():
        try:
            old = json.loads(path.read_text()).get("items", [])
        except Exception:
            old = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M")
    seen, items = set(), []
    for it in sorted(parse(xml, u) + old, key=lambda x: x["d"], reverse=True):
        k = _key(it["t"])
        if k in seen or it["d"] < cutoff:
            continue
        seen.add(k)
        items.append(it)
    items = items[:KEEP_MAX]
    path.write_text(json.dumps({"symbol": u["symbol"], "name": u["name"], "query": q,
                                "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
                                "items": items}, ensure_ascii=False, separators=(",", ":")))
    return len(items)


def fetch_news(universe: list[dict], workers: int = 6, budget_s: int = 900) -> dict:
    t0, s = time.time(), requests.Session()
    ok = fail = 0

    def job(u):
        if time.time() - t0 > budget_s:
            return None
        time.sleep(random.random() * 0.5)
        return fetch_one(u, s)

    with ThreadPoolExecutor(workers) as ex:
        for n in ex.map(job, universe):
            if n is None:
                continue
            if n < 0:
                fail += 1
            else:
                ok += 1
    return {"ok": ok, "failed": fail, "seconds": round(time.time() - t0)}
