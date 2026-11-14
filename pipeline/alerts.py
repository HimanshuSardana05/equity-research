"""Setup alerts: today's setups that pass config/setup_rules.json, as a feed plus optional notifications.

The daily build calls build_alerts() and publishes app/data/alerts.json (the feed). A separate workflow step runs
`python pipeline/alerts.py --notify`, which sends that feed through each channel switched on in the rules,
skipping anything already sent (data/raw/alerts_sent.json). Everything is off until alerts.enabled and a channel's
enabled flag are true in the rules AND the channel's secrets exist. Without --send it only prints what it would send.

Add a channel by writing a function notify_<name>(alerts, cfg) -> int and listing it in CHANNELS.
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
import urllib.request
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setups import PATTERNS, load_rules, matches  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FEED = ROOT / "app" / "data" / "alerts.json"
SENT = ROOT / "data" / "raw" / "alerts_sent.json"
SITE_URL = os.environ.get("SITE_URL", "https://his-equity-research.pages.dev")


def _text(a: dict) -> str:
    arrow = "▲" if a["dir"] > 0 else "▼"
    rng = f"{a['lo']:,.2f}" if a["lo"] == a["hi"] else f"{a['lo']:,.2f}–{a['hi']:,.2f}"
    return (f"{arrow} {a['symbol']} · {PATTERNS[a['pattern']][0]} · {a['level']} {rng} · "
            f"close ₹{a['close']:,.2f} · score {a['score']}")


def build_alerts(recent: list[dict], rules: dict, as_of: str | None) -> dict:
    """Feed of the latest day's setups that pass the rules (and the alert-specific limits)."""
    al = rules.get("alerts") or {}
    floor = al.get("min_score")
    only = set(al.get("symbols") or [])
    out = []
    for s in recent:
        if s["date"] != as_of or (only and s["symbol"] not in only):
            continue
        if not matches(s, rules, floor):
            continue
        a = {k: s.get(k) for k in ("symbol", "name", "industry", "date", "pattern", "dir", "kind", "level",
                                   "lo", "hi", "close", "score", "vol", "trend")}
        a["id"] = f"{s['symbol']}|{s['date']}|{s['pattern']}"
        a["text"] = _text(a)
        a["url"] = f"{SITE_URL}/#company-{s['symbol']}"
        out.append(a)
    out.sort(key=lambda a: -a["score"])
    return {"as_of": as_of, "rules_version": rules.get("version"), "enabled": bool(al.get("enabled")),
            "count": len(out), "alerts": out}


# ------------------------------------------------------------------ channels
def notify_email(alerts: list[dict], cfg: dict) -> int:
    need = ["SMTP_HOST", "SMTP_USER", "SMTP_PASS", "ALERT_EMAIL_TO"]
    if any(not os.environ.get(k) for k in need):
        print("email: secrets missing, skipped")
        return 0
    msg = EmailMessage()
    msg["Subject"] = f"{len(alerts)} setup{'s' if len(alerts) != 1 else ''} · {alerts[0]['date']}"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["ALERT_EMAIL_TO"]
    msg.set_content("\n".join(f"{a['text']}\n{a['url']}\n" for a in alerts)
                    + "\nResearch tool for personal use, not investment advice.")
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or 587), timeout=30) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)
    return len(alerts)


def notify_whatsapp(alerts: list[dict], cfg: dict) -> int:
    need = ["WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "WHATSAPP_TO"]
    if any(not os.environ.get(k) for k in need):
        print("whatsapp: secrets missing, skipped")
        return 0
    body = "\n\n".join(a["text"] for a in alerts)[:3800]
    req = urllib.request.Request(
        f"https://graph.facebook.com/v20.0/{os.environ['WHATSAPP_PHONE_ID']}/messages",
        data=json.dumps({"messaging_product": "whatsapp", "to": os.environ["WHATSAPP_TO"], "type": "text",
                         "text": {"body": body}}).encode(),
        headers={"Authorization": f"Bearer {os.environ['WHATSAPP_TOKEN']}", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()
    return len(alerts)


CHANNELS = {"email": notify_email, "whatsapp": notify_whatsapp}


def main(send: bool) -> None:
    rules = load_rules()
    al = rules.get("alerts") or {}
    try:
        feed = json.loads(FEED.read_text())
    except (OSError, ValueError):
        print("no alerts feed")
        return
    sent = set(json.loads(SENT.read_text())) if SENT.exists() else set()
    fresh = [a for a in feed.get("alerts", []) if a["id"] not in sent][: int(al.get("max_per_run") or 25)]
    print(f"{len(fresh)} new alert(s) for {feed.get('as_of')}")
    for a in fresh:
        print("  ", a["text"])
    if not fresh:
        return
    if not al.get("enabled"):
        print("alerts are switched off in config/setup_rules.json (alerts.enabled)")
        return
    if not send:
        print("dry run: pass --send to deliver")
        return
    done = False
    for name, fn in CHANNELS.items():
        cfg = (al.get("channels") or {}).get(name) or {}
        if not cfg.get("enabled"):
            continue
        try:
            n = fn(fresh, cfg)
            print(f"{name}: sent {n}")
            done = done or n > 0
        except Exception as e:  # one failing channel must not stop the others or the build
            print(f"{name}: failed {e!r}")
    if done:
        sent |= {a["id"] for a in fresh}
        SENT.parent.mkdir(parents=True, exist_ok=True)
        SENT.write_text(json.dumps(sorted(sent)[-5000:]))


if __name__ == "__main__":
    main(send="--send" in sys.argv)
