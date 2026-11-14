"""Nifty 500 constituents from NSE's public index file."""
import csv
import io

import requests

from common import SITE_DATA, UA, load_universe, write_json

URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
]


def refresh_universe() -> list[dict]:
    last_err = None
    for url in URLS:
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(r.text)))
            uni = [{
                "symbol": row["Symbol"].strip(),
                "name": row["Company Name"].strip(),
                "industry": row["Industry"].strip(),
                "isin": row["ISIN Code"].strip(),
            } for row in rows if row.get("Symbol")]
            if len(uni) > 400:
                write_json(SITE_DATA / "universe.json", uni)
                return uni
        except Exception as e:  # keep the previous list if NSE is down
            last_err = e
    print("universe refresh failed, keeping previous list:", last_err)
    return load_universe()


if __name__ == "__main__":
    print(len(refresh_universe()), "symbols")
