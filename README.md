# Nifty 500 Research Desk

A personal equity research app for Nifty 500 companies. It keeps a growing history of prices,
fundamentals, filings and results expectations, then flags companies that look over- or
undervalued and says why. Research tooling only; this is not investment advice.

## What's in it

| Tab | What it answers |
|---|---|
| Screen | Which companies pass the Coffee Can filter, how they score on Buffett-style quality, and which look cheap or expensive |
| Company | Price chart, valuation history (PE, P/B, EV/EBITDA, P/S) against its own median and the sector median, fundamentals by year, reverse DCF, fair-value range, Coffee Can grid, Buffett checks, where the return came from, how it differs from peers, results expectations, filings |
| Sector peers | Sector median valuation through time, what the market rewards in the sector, peer fundamentals by year, a peer table with quality-justified PE |
| Factor vs factor | Scatter any two measures across the universe or one sector, coloured by valuation verdict |
| Announcements | NSE filings for Nifty 500 companies, accumulated daily |
| Method | The valuation logic and its assumptions |

Charts use TradingView's open-source [lightweight-charts](https://github.com/tradingview/lightweight-charts).

## Data sources and terms of use

* **NSE**: the Nifty 500 constituents CSV, the daily bhavcopy (official closing prices) and the public RSS feeds for announcements, results and board meetings. These are static public files.
* **Yahoo Finance via yfinance**: split-adjusted price history, about 4 annual years plus recent quarters of statements, and analyst estimates. It's unofficial and meant for personal research.
* **Screener.in**: Screener's terms don't allow automated scraping, so the app never fetches it. To get 10-12 years of history (needed for the full Coffee Can test), open a company on screener.in while logged in, click **Export to Excel**, and commit the file to `data/screener_exports/` (naming it `SYMBOL.xlsx` is easiest). The next run imports it, and those years take priority over Yahoo's.

## How it updates

`.github/workflows/update.yml` runs:

* **18:00 IST, Monday to Friday**: appends the day's NSE bhavcopy closes, collects new filings, refreshes fundamentals for companies that just reported plus a rotating slice of the others, rebuilds every metric and redeploys the site.
* **Saturday morning**: a full price re-sync from Yahoo (which picks up split adjustments) and a larger fundamentals refresh.
* **On push** of a Screener export or app change: a rebuild.

Raw history (`data/raw/`) is committed so the record keeps growing. The site's JSON is regenerated on every run and deployed to GitHub Pages; it isn't committed.

## Run locally

```bash
pip install -r requirements.txt
python pipeline/run.py full        # first time: full backfill
python pipeline/run.py daily       # everyday update
python pipeline/build_site.py && cd dist && python -m http.server 8000
```

Valuation assumptions (risk-free rate, equity risk premium, terminal growth, Coffee Can thresholds) are in `pipeline/common.py` under `CONFIG`.

## Valuation logic

1. **Coffee Can (primary)**: revenue growth above 10% and ROCE above 15% in every one of the last 10 years (ROE for lenders).
2. **Buffett-style quality**: durable ROE, low debt, stable margins, free cash generation, cash conversion, and the retained-earnings test.
3. **Damodaran-style valuation**: reverse DCF (the growth the price implies), a two-stage FCFE value range, and relative value against the company's own history and a quality-adjusted sector regression.

The verdict blends the three valuation signals and lists the reasons behind it.
