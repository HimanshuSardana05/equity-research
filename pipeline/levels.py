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
