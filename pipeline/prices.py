"""Daily prices.

* Backfill / weekly re-sync: Yahoo Finance via yfinance, full history. open/high/low/close are adjusted for splits
  and bonus issues (what charts show). The "adj" column is Yahoo's dividend factor (Adj Close / Close): multiply
  prices by it for total-return figures (the backtest and research do this).
* A daily close that jumps like a split or bonus triggers a Yahoo re-download for that stock the same day.
* Daily append: NSE's official end-of-day bhavcopy (public archive file).
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, timedelta

import pandas as pd
import requests

from common import PRICES, UA, yf_symbol

BHAV_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv.zip"


COLS = ["open", "high", "low", "close", "volume", "adj"]


def price_path(sym):
    return PRICES / f"{sym}.csv"


def load_prices(sym) -> pd.DataFrame:
    p = price_path(sym)
    if not p.exists():
        return pd.DataFrame(columns=COLS)
    df = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
    for c in COLS:
        if c not in df:
            df[c] = float("nan")
    df["adj"] = df["adj"].fillna(1.0)
    return df[~df.index.duplicated(keep="last")][COLS]


def save_prices(sym, df: pd.DataFrame):
    df = df[~df.index.duplicated(keep="last")].sort_index()
    out = df.reindex(columns=COLS).copy()
    out.index.name = "date"
    for c in ("open", "high", "low", "close"):
        out[c] = out[c].round(2)
    out["volume"] = out["volume"].fillna(0).astype("int64")
    out["adj"] = out["adj"].fillna(1.0).round(6)
    out.to_csv(price_path(sym), date_format="%Y-%m-%d")


def backfill_yf(symbols: list[str], period="max", batch=50):
    """Download (or re-download, re-adjusting for splits) history from Yahoo."""
    import yfinance as yf
    done = []
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        tickers = [yf_symbol(s) for s in chunk]
        data = yf.download(tickers, period=period, interval="1d", auto_adjust=False,
                           group_by="ticker", progress=False, threads=True)
        for s, t in zip(chunk, tickers):
            try:
                d = data[t] if len(tickers) > 1 else data
                d = d[["Open", "High", "Low", "Close", "Volume"] + (["Adj Close"] if "Adj Close" in d else [])].dropna(subset=["Close"])
                if d.empty:
                    continue
                d = d.copy()
                d["adj"] = (d["Adj Close"] / d["Close"]).clip(0.01, 1.5) if "Adj Close" in d else 1.0
                # yfinance's Close here is split-adjusted only; scale O/H/L consistently with it
                d = d.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}).drop(columns=["Adj Close"], errors="ignore")
                d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
                if period != "max":  # merge recent rows into stored history
                    old = load_prices(s)
                    d = pd.concat([old, d])
                save_prices(s, d)
                done.append(s)
            except Exception as e:
                print("yf price fail", s, e)
    return done


def fetch_bhavcopy(d: date) -> pd.DataFrame | None:
    url = BHAV_URL.format(d=d.strftime("%Y%m%d"))
    try:
        r = requests.get(url, headers=UA, timeout=30)
        if r.status_code != 200 or not r.content.startswith(b"PK"):
            return None
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            df = pd.read_csv(z.open(z.namelist()[0]))
        df = df[df["SctySrs"].isin(["EQ", "BE", "BZ"])]
        return df[["TckrSymb", "OpnPric", "HghPric", "LwPric", "ClsPric", "TtlTradgVol"]].rename(
            columns={"TckrSymb": "symbol", "OpnPric": "open", "HghPric": "high", "LwPric": "low",
                     "ClsPric": "close", "TtlTradgVol": "volume"})
    except Exception as e:
        print("bhavcopy fail", d, e)
        return None


def append_bhavcopies(symbols: list[str], lookback_days=10) -> list[str]:
    """Append official NSE closes for every trading day missing from stored history."""
    last_dates = {}
    for s in symbols:
        df = load_prices(s)
        last_dates[s] = df.index.max().date() if len(df) else None
    known = [d for d in last_dates.values() if d]
    if not known:
        return []
    start = max(min(known), date.today() - timedelta(days=lookback_days))
    days = [start + timedelta(days=i) for i in range(1, (date.today() - start).days + 1)]
    fetched = []
    for d in days:
        if d.weekday() >= 5:
            continue
        bc = fetch_bhavcopy(d)
        if bc is None:
            continue
        fetched.append(d.isoformat())
        bc = bc.set_index("symbol")
        for s in symbols:
            if s in bc.index and (last_dates[s] is None or last_dates[s] < d):
                row = bc.loc[s]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                df = load_prices(s)
                df.loc[pd.Timestamp(d)] = [float(row["open"]), float(row["high"]), float(row["low"]),
                                           float(row["close"]), int(row["volume"]), 1.0]
                save_prices(s, df)
                last_dates[s] = d
    return fetched


def corporate_action_suspects(symbols: list[str]) -> list[str]:
    """Stocks whose latest close moved like a split or bonus (more than -40% / +70% in a day) and need re-adjusting."""
    out = []
    for s in symbols:
        df = load_prices(s)
        if len(df) < 2:
            continue
        r = df["close"].iloc[-1] / df["close"].iloc[-2]
        if r < 0.6 or r > 1.7:
            out.append(s)
    return out
