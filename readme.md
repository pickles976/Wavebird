# Wavebird

Cache-first comparative DCF analysis for mature US-listed companies. Wavebird fetches
fundamentals from Alpha Vantage and market rates from Damodaran's published workbooks,
runs three independent discounted-cash-flow models, and reports them side by side with a
conservative screen — never an averaged value.

## Requirements

- Python 3.12, 3.13, or 3.14
- [uv](https://docs.astral.sh/uv/)

```bash
uv sync --group dev
```

## API key

```bash
export ALPHAVANTAGE_API_KEY=...
```

Free keys: <https://www.alphavantage.co/support/#api-key>. The free tier allows 25
requests per day. A completely cold single-symbol analysis makes exactly six Alpha
requests: income statement, cash flow, balance sheet, earnings, company weekly prices,
and shared SPY weekly prices. All responses are cached in SQLite, so subsequent runs are
free. SPY weekly prices are cached for 7 days (the index series moves slowly and is
shared by every analysis), so steady-state repeated use costs about one Alpha request per
symbol per day. Alternatively, put the key in a `.env` file next to `pyproject.toml`:

```dotenv
ALPHAVANTAGE_API_KEY=...
```

Wavebird calls the documented endpoints directly
(<https://www.alphavantage.co/documentation/>) instead of using the third-party
`alpha-vantage` package, which is unnecessary and provides no verified Python 3.12–3.14
compatibility contract.

## Assumptions file

All rates are decimal fractions. Copy `examples/assumptions.toml` and edit it; those
values are examples, not recommendations — supply your own thesis.

| Field | Meaning | Example |
| --- | --- | --- |
| `forecast_years` | Explicit projection years | `5` |
| `high_growth_rate` | Growth during the explicit stage | `0.06` |
| `stable_growth_rate` | Perpetuity growth; must be below stable ROE/ROC and discount rates | `0.025` |
| `stable_roe` | Steady-state return on equity | `0.12` |
| `stable_roc` | Steady-state return on capital | `0.10` |
| `stable_beta` | Beta used for the stable-stage discount rate | `1.0` |
| `pretax_cost_of_debt` | Pre-tax borrowing cost | `0.05` |
| `target_debt_weight` | Target debt weight in capital | `0.20` |
| `margin_of_safety` | Required discount to value for the screen | `0.25` |
| `capex_growth_rate` | Optional; defaults to `high_growth_rate` | `0.06` |
| `depreciation_growth_rate` | Optional; defaults to `high_growth_rate` | `0.06` |
| `revenue_growth_rate` | Optional; defaults to `high_growth_rate` | `0.06` |
| `equity_option_value` | Option/warrant overlay deducted in FCFF | `0` |

## Usage
```bash
uv run wavebird            # guided session: ticker -> run (no editor)
uv run wavebird --edit-assumptions   # same, opening the assumptions editor first
uv run wavebird analyze IBM --assumptions examples/assumptions.toml --plot ibm.png
uv run wavebird cache refresh IBM
uv run wavebird rates refresh
```

`analyze` prints a deterministic text report; `--json` switches stdout to JSON; `--plot`
writes the six-panel figure; `--window 52w|3y|5y|10y|all` limits the plotted time span
(default: all); `--as-of YYYY-MM-DD` pins the valuation date; `--refresh` bypasses fresh
cache entries. The interactive session asks for a plot window before writing a plot.

The figure's panels: share price (split-adjusted) against each model's fair value per
share; market capitalization against intrinsic equity value; the market-wide Shiller PE
(cyclically adjusted P/E, monthly, from multpl.com, on its own ~150-year axis); operating
performance; capital structure; and debt/operating growth. The weekly price and market-cap
series pair split-adjusted closes with share counts as reported in each period, so the
series stay continuous across stock splits.

## Interactive mode

```bash
uv run wavebird            # same as: uv run wavebird interactive
```

Launch shows the Alpha Vantage daily allowance (25 requests/day, 5/minute) and how many
of those requests Wavebird itself made today, counted in the local cache. Note that only
requests made through Wavebird are counted — other tools sharing the key are invisible to
the counter. Ticker autocomplete offers the ~10,000-symbol SEC directory merged with
cached and built-in symbols; the SEC directory is fetched keylessly (no Alpha quota) and
cached for 30 days.

By default the session runs the assumptions from `examples/assumptions.toml` without
opening the editor. Pass `--edit-assumptions` to open the field editor first. Because a
radiolist always keeps a selection, "Review & run analysis" is the first item: Enter-Enter
runs immediately, arrow keys pick a field, and a blank value keeps the current value and
returns to the menu.

## Models and tradeoffs

- **Stable FCFE** — `V = EPS × (1+g) × (1 − g/ROE) / (k_e − g)`. A steady-state sanity
  check for mature firms; compact but highly sensitive to stable growth and ROE, and it
  cannot represent a transition period.
- **Two-stage FCFE** — projects per-share FCFE through the explicit stage (EPS, CapEx,
  depreciation, revenue, and working capital each grown at their configured rates,
  reinvestment weighted by the target debt ratio), then a Gordon-growth terminal value at
  the stable-stage cost of equity. It models the transition but is sensitive to all four
  projection assumptions.
- **Two-stage FCFF** — projects NOPAT-based free cash flow to the firm, discounts at
  WACC, deducts debt and option overlays, and divides by shares. It is the
  operating/capital-structure cross-check and is preferable when borrowing distorts
  equity cash flow; it adds WACC, target-debt, tax, cash/debt, and option-value
  assumptions.

Discount rates use the contemporaneous Damodaran risk-free rate and implied equity risk
premium; the high stage uses the measured beta, the stable stage uses `stable_beta`.
The effective tax rate is the median of up to three recent positive
`tax expense / pre-tax income` observations and its source periods are disclosed.

## Screen rule

The screen passes only when **all three** models produce a value and every model's
discount to value meets your margin of safety. If any model is unavailable the screen is
`INCONCLUSIVE`. Wavebird never averages the three values and never issues a
recommendation.

## Cache

Responses live in a versioned SQLite database (default
`$XDG_CACHE_HOME/wavebird/cache.sqlite3`, override with `WAVEBIRD_CACHE_PATH`).
Statements and earnings are kept for 7 days, weekly prices for 1 day, the Damodaran
annual workbook and the multpl Shiller PE table for 30 days, and the Damodaran monthly
workbook for 7 days. `--refresh` and the `cache refresh` / `rates refresh` commands force
downloads. If a download fails or the quota is exhausted, an expired row may still be used
and is explicitly labeled `STALE` in the report. API keys are never written to the cache.

## Data sources

- Alpha Vantage API: <https://www.alphavantage.co/documentation/>
- Damodaran annual implied ERP: <https://pages.stern.nyu.edu/~adamodar/pc/datasets/histimpl.xls>
- Damodaran monthly ERP: <https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx>
- Shiller PE monthly history: <https://www.multpl.com/shiller-pe/table/by-month>

Wavebird gratefully acknowledges Aswath Damodaran's publicly published datasets.

Three keyless (no-quota) sources supplement Alpha Vantage:

- **SEC ticker directory**
  (<https://www.sec.gov/files/company_tickers.json>) — the full US-listed universe for
  ticker autocomplete, cached 30 days. Requires an identifying User-Agent per SEC
  fair-access policy.
- **multpl Shiller PE table**
  (<https://www.multpl.com/shiller-pe/table/by-month>) — monthly cyclically adjusted P/E
  back to 1871 for the market-context panel, cached 30 days, plotted on its own axis.
- Stooq and Yahoo Finance were evaluated as keyless price sources and rejected: Stooq
  serves a JavaScript proof-of-work challenge to non-browser clients and Yahoo returns
  HTTP 429 without a cookie/crumb session. Weekly prices therefore remain on Alpha
  Vantage.

## History, timing, and restatements

Annual and rolling four-quarter (TTM) histories are reconstructed separately and never
mixed. Availability dates prefer the matching fourth-quarter earnings `reportedDate`,
then any annual reported date, then explicit fallbacks (fiscal year-end +90 days for
annual points, quarter-end +45 days for quarters). These are earnings-release or proxy
dates, not SEC filing dates, and provider history can include later restatements. The
figure and reports state that this is an analytical reconstruction, **not a
look-ahead-free backtest**.

## Debt/growth panel

The fourth plot panel shows year-over-year revenue, EBIT, FCFF, and net-debt growth plus
debt-to-capital. The financing-assisted indicator flags co-movement when net debt grows
faster than both revenue and EBIT while leverage rises.
**This shows co-movement, not that debt caused growth.**

## Scope

Wavebird targets mature, USD-reporting, US-listed companies with positive earnings.
Financials, REITs, banks, negative-earnings firms, and non-USD reporters are out of
scope and will be rejected or marked model-unavailable rather than silently distorted.

## Verification

```bash
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
```

Not investment advice.
