"""Import Screener.in "Export to Excel" files that you download yourself while logged in.

Screener's terms do not allow automated scraping, so this app never fetches screener.in.
Instead: open a company on screener.in -> "Export to Excel" -> save the file into
data/screener_exports/ (any file name; SYMBOL.xlsx is easiest). The next pipeline run parses
the "Data Sheet" tab (10-12 years of P&L, balance sheet and cash flow) and uses it in
preference to Yahoo data for the years it covers. That unlocks the full 10-year Coffee Can test.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from common import SCREENER, SCREENER_INBOX, load_universe, now_iso, write_json

# Screener row label -> normalised field (Rs crore unless noted)
PL = {
    "sales": "revenue", "other income": "other_income", "depreciation": "depreciation",
    "interest": "interest", "profit before tax": "pbt", "tax": "tax", "net profit": "net_income",
    "dividend amount": "dividend_amount",
    "raw material cost": "raw_material", "employee cost": "employee_cost",
    "change in inventory": "inventory_change", "power and fuel": "power_fuel",
    "other mfr. exp": "other_mfr", "selling and admin": "selling_admin", "other expenses": "other_exp",
}
BS = {
    "equity share capital": "share_capital", "reserves": "reserves", "borrowings": "debt",
    "other liabilities": "other_liabilities", "net block": "net_block",
    "capital work in progress": "cwip", "investments": "investments", "other assets": "other_assets",
    "receivables": "receivables", "inventory": "inventory", "cash & bank": "cash",
    "no. of equity shares": "shares",
}
CF = {
    "cash from operating activity": "cfo", "cash from investing activity": "cfi",
    "cash from financing activity": "cff", "net cash flow": "net_cash_flow",
}


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower()).rstrip(":")


def parse_export(path: Path) -> dict:
    df = pd.read_excel(path, sheet_name="Data Sheet", header=None)
    labels = [_norm(x) for x in df.iloc[:, 0].tolist()]
    name = None
    for i, lab in enumerate(labels):
        if lab == "company name":
            name = str(df.iat[i, 1]).strip()
            break

    sections = {"profit & loss": PL, "balance sheet": BS, "cash flow": CF}
    annual: dict[str, dict] = {}
    current, dates = None, None
    for i, lab in enumerate(labels):
        if lab in sections:
            current, dates = sections[lab], None
            continue
        if lab == "quarters":  # quarterly block sits inside the P&L area; skip it
            current, dates = None, None
            continue
        if lab in ("price", "derived"):
            current = None
        if current is None:
            continue
        if lab == "report date":
            dates = []
            for v in df.iloc[i, 1:].tolist():
                try:
                    dates.append(pd.Timestamp(v).strftime("%Y-%m-%d") if pd.notna(v) else None)
                except Exception:
                    dates.append(None)
            continue
        if dates and lab in current:
            field = current[lab]
            for d, v in zip(dates, df.iloc[i, 1:].tolist()):
                if d and pd.notna(v):
                    try:
                        annual.setdefault(d, {"period_end": d})[field] = float(v)
                    except (TypeError, ValueError):
                        pass

    for d, r in annual.items():
        if r.get("share_capital") is not None or r.get("reserves") is not None:
            r["equity"] = (r.get("share_capital") or 0) + (r.get("reserves") or 0)
        if r.get("pbt") is not None:
            r["ebit"] = r["pbt"] + (r.get("interest") or 0)
            if r.get("depreciation") is not None:
                r["ebitda"] = r["ebit"] + r["depreciation"]
        if r.get("cfo") is not None and r.get("cfi") is not None:
            r["fcf_proxy"] = r["cfo"] + r["cfi"]  # Screener has no capex line; CFO+CFI is a conservative proxy
        tot = sum(r.get(k) or 0 for k in ("net_block", "cwip", "investments", "other_assets"))
        if tot:
            r["total_assets"] = tot
        if r.get("shares") and r["shares"] < 1e5:  # some exports give shares in crore
            r["shares"] *= 1e7
        if r.get("dividend_amount") is not None:
            r["dividends"] = r["dividend_amount"]
        if r.get("net_income") and r.get("shares"):
            r["eps"] = r["net_income"] * 1e7 / r["shares"]
    return {"name": name, "annual": dict(sorted(annual.items()))}


def _match_symbol(path: Path, name: str | None, universe: list[dict]) -> str | None:
    stem = path.stem.upper().replace(" ", "")
    syms = {u["symbol"].upper(): u["symbol"] for u in universe}
    if stem in syms:
        return syms[stem]
    def key(s):
        s = re.sub(r"\b(ltd|limited|the|india|co|company)\b\.?", "", (s or "").lower())
        return re.sub(r"[^a-z0-9]", "", s)
    if name:
        k = key(name)
        for u in universe:
            if key(u["name"]) == k:
                return u["symbol"]
    return None


def import_all() -> list[str]:
    uni = load_universe()
    done = []
    for f in sorted(SCREENER_INBOX.glob("*.xls*")):
        try:
            parsed = parse_export(f)
            sym = _match_symbol(f, parsed["name"], uni)
            if not sym:
                print("screener: could not match", f.name, parsed["name"], "- rename it SYMBOL.xlsx")
                continue
            parsed.update({"symbol": sym, "source_file": f.name, "imported": now_iso()})
            write_json(SCREENER / f"{sym}.json", parsed)
            done.append(sym)
        except Exception as e:
            print("screener: failed", f.name, e)
    return done


if __name__ == "__main__":
    print(import_all())
