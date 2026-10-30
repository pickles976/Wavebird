# Wavebird Modernization Design

## Purpose

Modernize Wavebird into a reproducible Python 3.12-3.14 command-line tool for screening mature US-listed companies during broad market selloffs. Wavebird will compare three Damodaran-style intrinsic-value models rather than present one unexplained fair-value number.

Wavebird is an analytical screen, not investment advice. Market price is used to calculate discount or premium to intrinsic value and, for plotting, market capitalization. It must never be used to tune growth, discount rates, or other valuation assumptions.

## Current State

The repository currently contains three coupled scripts:

- `main.py` fetches weekly Alpha Vantage prices and plots market history against calculated values.
- `alpha.py` fetches cash-flow and balance-sheet reports and calculates a simplified FCFE value.
- `discount.py` uses `pandas_datareader` Yahoo data for beta and risk-free rates and reads `HistoricalRates.csv`.

The application does not run in the available Python 3.12 environment because dependencies are not installed and there is no lockfile. The ignored `alphakey.txt` file is absent. The existing calculation also has material correctness problems: dividends and repurchases enter FCFE, depreciation is omitted, the first projected cash flow is discounted at period zero, and terminal value is calculated as `FCFE / ERP`.

## Goals

1. Support and verify CPython 3.12, 3.13, and 3.14.
2. Use `uv` for dependency and environment management.
3. Use Ruff for formatting and linting and `ty` for static type analysis.
4. Use Pydantic v2 models for configuration, provider responses, normalized financial records, valuation assumptions, and results.
5. Cache stock and reference data in SQLite before making network requests.
6. Refresh historical and current risk-free-rate and equity-risk-premium data from Aswath Damodaran's published workbooks.
7. Keep Alpha Vantage as the stock-data provider, using direct HTTP rather than an unverified wrapper SDK.
8. Compare stable-growth FCFE, two-stage FCFE, and two-stage FCFF results with explicit assumptions and model-specific explanations.
9. Plot historical market capitalization and annual/quarterly intrinsic equity values, plus debt, cash, operating growth, and cash-flow trends.
10. Make the financial equations and Damodaran workbook regression values executable acceptance criteria.

## Non-goals

- Web application or hosted service.
- International equities, currency conversion, or country-risk premiums.
- Early-stage, negative-earnings, distressed, bank, insurance, natural-resource, or highly unstable firms.
- SEC EDGAR or multi-provider fallback in this version.
- Automatic buy or sell recommendations.
- Automatic analyst-growth forecasts.
- Causal claims that debt caused growth; Wavebird will show co-movement and leverage, not infer causality.
- Monte Carlo analysis, portfolio optimization, or unrelated README backlog items.

## External Data Status

### Alpha Vantage

Alpha Vantage remains operational. Its live demo endpoints returned current cash-flow, balance-sheet, weekly, and weekly-adjusted IBM data during this audit. The selected endpoints are:

- `INCOME_STATEMENT`
- `CASH_FLOW`
- `BALANCE_SHEET`
- `EARNINGS`
- `TIME_SERIES_WEEKLY_ADJUSTED` for the company and SPY

The three statement endpoints return both `annualReports` and `quarterlyReports`; quarterly statements therefore do not consume extra requests. `EARNINGS` supplies annual/quarterly reported EPS and quarterly earnings-release dates.

A normal free key currently permits 25 requests per day. A cold first analysis requires five company requests plus a shared SPY request, so cache-first behavior is mandatory. Alpha Vantage states that verified open-source or educational projects may request unlimited access. The repository's previous key cannot be tested because `alphakey.txt` is absent.

The third-party `alpha-vantage` package does not declare modern-Python compatibility and is unnecessary. Wavebird will use a small `httpx.Client` wrapper with explicit timeouts and Pydantic response validation. The key comes only from `ALPHAVANTAGE_API_KEY` and is never logged or stored in SQLite.

### Damodaran data

Wavebird will use these public workbooks:

- Annual historical ERP and Treasury rates, 1960-current: `https://pages.stern.nyu.edu/~adamodar/pc/datasets/histimpl.xls`
- Monthly implied ERP and Treasury rates: `https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx`

The annual workbook replaces the repository's copy of `HistoricalRates.csv`, which stops in 2021. The monthly workbook supplies the latest valuation date. Workbook parsing uses pandas with `python-calamine`; column names and date conversions are validated rather than selected by position.

Damodaran describes these downloads as having no strings attached and appreciates, but does not require, acknowledgement. Reports retain source URLs and retrieval timestamps as provenance.

## Package and Tooling Layout

```text
pyproject.toml
uv.lock
src/wavebird/
  __init__.py
  cli.py
  config.py
  models.py
  cache.py
  alpha_vantage.py
  damodaran.py
  valuation.py
  service.py
  reporting.py
  plotting.py
tests/
  fixtures/
  test_cache.py
  test_alpha_vantage.py
  test_damodaran.py
  test_valuation.py
  test_reporting.py
  test_cli.py
```

`pyproject.toml` declares Python `>=3.12`. CI or the local verification script runs on 3.12, 3.13, and 3.14. Runtime dependencies are limited to `httpx`, `pydantic`, `pydantic-settings`, `pandas`, `python-calamine`, and `matplotlib`. Development dependencies include `pytest`, `ruff`, and `ty`.

`argparse` supplies the CLI to avoid another framework. Core calculations use typed Python collections and Pydantic models. pandas is confined to workbook import and plotting boundaries.

## Command Surface

```text
uv run wavebird analyze AAPL --assumptions assumptions.toml
uv run wavebird analyze AAPL --assumptions assumptions.toml --plot report.png
uv run wavebird analyze AAPL --assumptions assumptions.toml --as-of 2025-12-31
uv run wavebird analyze AAPL --assumptions assumptions.toml --refresh
uv run wavebird analyze AAPL --assumptions assumptions.toml --json
uv run wavebird cache refresh AAPL
uv run wavebird rates refresh
```

The assumptions file is mandatory. No generic growth or return assumption runs silently.

Example schema:

```toml
forecast_years = 5
high_growth_rate = 0.06
stable_growth_rate = 0.025
stable_roe = 0.12
stable_roc = 0.10
stable_beta = 1.0
pretax_cost_of_debt = 0.05
target_debt_weight = 0.20
margin_of_safety = 0.25

# Optional; each defaults to high_growth_rate and is still printed.
# capex_growth_rate = 0.06
# depreciation_growth_rate = 0.06
# revenue_growth_rate = 0.06

# Optional adjustment not available from Alpha Vantage. Always printed.
# equity_option_value = 0.0
```

Validation requires:

- `forecast_years >= 1`;
- every growth and discount rate is a finite decimal rate greater than `-1`;
- `0 <= target_debt_weight < 1`;
- `0 <= margin_of_safety < 1`;
- `stable_roe > stable_growth_rate`;
- `stable_roc > stable_growth_rate`;
- each terminal discount rate exceeds `stable_growth_rate`.

## Data Models and Normalization

Pydantic models separate provider payloads from normalized domain records. Alpha Vantage's string values and literal `"None"` are handled in provider validators. Missing values remain missing; they are not silently converted to zero.

Reports from different endpoints are joined by `fiscalDateEnding`, never by array position. The normalized annual or quarterly record contains at least:

- fiscal date and reported currency;
- revenue, EBIT, pretax income, tax expense, interest expense, and net income;
- capital expenditure and depreciation/amortization;
- current assets, cash and short-term investments, current liabilities, and current interest-bearing debt;
- total interest-bearing debt and book equity;
- dividends;
- annual and quarterly reported EPS and earnings-release date;
- current shares outstanding.

Only USD records are accepted in this version. Capital expenditure and depreciation are normalized as positive amounts according to the provider field contract. Stable FCFE starts from Alpha Vantage `reportedEPS`. Other per-share inputs and FCFF equity value use the matching balance-sheet `commonStockSharesOutstanding`; the report labels this as a basic, point-in-time denominator rather than a diluted weighted-average count. Market capitalization uses the raw contemporaneous close and that shares-outstanding observation; adjusted close remains the return-series input.

Non-cash working capital is:

$$
NWC_t=(CurrentAssets_t-Cash_t)-(CurrentLiabilities_t-CurrentDebt_t)
$$

where `CurrentDebt` uses Alpha Vantage `currentDebt` when present and `shortTermDebt` as the documented fallback. The change is:

$$
\Delta NWC_t=NWC_t-NWC_{t-1}
$$

Balance-sheet diagnostics use these definitions:

$$
GrossDebt=CurrentInterestBearingDebt+LongTermInterestBearingDebt
$$

$$
Cash=CashAndCashEquivalents+ShortTermInvestments
$$

$$
NetDebt=GrossDebt-Cash
$$

$$
DebtToCapital=\frac{GrossDebt}{GrossDebt+BookEquity}
$$

Alpha Vantage's combined cash or total-debt fields are preferred when present; otherwise the non-overlapping components above are summed. Provider validation rejects a negative denominator or an internally inconsistent combination rather than double-counting it. Annual growth compares consecutive annual records. Quarterly growth compares TTM values or quarter-end balance values with the same quarter one year earlier. Percentage growth and the financing-assisted indicator are unavailable when any compared prior-period denominator is zero or negative; absolute changes are still reported.

A missing required input makes that model unavailable with an exact field/period error. It never changes the input to zero.

The effective tax rate for FCFF defaults to the median of up to three most recent positive `taxExpense / pretaxIncome` observations. The derived value and source years are printed.

### Annual and quarterly observations

Annual DCF points use one annual record and its preceding annual balance-sheet record. Quarterly points use trailing-twelve-month values, never a single quarter multiplied by four:

- TTM revenue, EBIT, pretax income, tax expense, interest, net income, CapEx, and depreciation are sums of the latest four quarters.
- TTM reported EPS is the sum of the latest four quarterly `reportedEPS` values; annual reported EPS comes from `annualEarnings`.
- TTM change in NWC equals current quarter-end NWC minus NWC four quarters earlier.
- Cash, debt, book equity, current liabilities/assets, and shares outstanding are point-in-time values from the current quarter.
- A quarterly value is unavailable until four contiguous statement quarters, four matching EPS quarters, and the prior-year balance-sheet quarter exist.

Annual and quarterly series are distinct result records. They are not mixed when calculating growth rates.

## SQLite Cache

The default database is `${XDG_CACHE_HOME:-~/.cache}/wavebird/cache.sqlite3`. `WAVEBIRD_CACHE_PATH` overrides it for tests and user control.

A versioned `cache_entries` table contains:

- provider;
- dataset;
- symbol, empty only for global datasets;
- canonical request key;
- JSON payload;
- fetched timestamp;
- expiry timestamp;
- source URL;
- primary key over provider, dataset, symbol, and request key.

SQLite uses transactions, atomic upserts, foreign keys, and WAL mode. No ORM is introduced.

Default TTLs:

- annual and quarterly financial statements: seven days;
- weekly adjusted prices: one day;
- annual Damodaran workbook: 30 days;
- monthly Damodaran workbook: seven days.

`--refresh` bypasses freshness checks. Expired data may be used only when refresh fails because of transport failure or provider quota. Such results are visibly labeled `STALE` with the retrieval time. Authentication errors, invalid tickers, provider error envelopes, malformed payloads, and schema drift never become stale-success fallbacks. Error payloads are never cached as successful data.

## Shared Rate Calculations

All rates are annual decimal fractions: `0.05` means 5%. No formula accepts mixed percentage units. Calculations retain full precision and round only for display.

Weekly beta uses company and SPY adjusted-close observations aligned by date over the trailing two years ending at the valuation date. At least 52 paired returns are required.

$$
R_t=\frac{P_t}{P_{t-1}}-1
$$

$$
\beta=\frac{Cov(R_{stock},R_{SPY})}{Var(R_{SPY})}
$$

Zero benchmark variance, non-finite values, or insufficient observations are errors. Beta is not silently clamped.

Cost of equity follows CAPM:

$$
k_e=r_f+\beta\times ERP
$$

For current analysis, Wavebird selects the latest Damodaran long-term Treasury rate and implied ERP available at or before `--as-of` or today's date. For each historical point, it selects the latest values available at or before that point's earnings-release or fallback availability date. Monthly observations are preferred where the monthly workbook has coverage; the annual workbook is the dated fallback for older points. The long-term Treasury rate is used consistently; the old T-bill/10-year mixture is removed.

## Model A: Stable-growth FCFE

This is the conservative anchor for a mature firm already in steady state. It is inappropriate when a distinct moderate-growth period is still expected.

$$
EquityReinvestmentRate=\frac{g}{ROE_{stable}}
$$

$$
FCFE_1=EPS_0(1+g)\left(1-\frac{g}{ROE_{stable}}\right)
$$

$$
V_0=\frac{FCFE_1}{k_e-g}
$$

For the current and quarterly models, $EPS_0$ is the sum of the latest four quarterly Alpha Vantage `reportedEPS` observations. For an annual historical point, it is the matching `annualEarnings.reportedEPS`. Preconditions include positive base-period earnings, `stable_roe > stable_growth_rate`, and `cost_of_equity > stable_growth_rate`.

## Model B: Two-stage FCFE

This is the primary equity model for mature companies with stable leverage and a finite period of moderate growth.

For years $t=1,\ldots,n$:

$$
EPS_t=EPS_0(1+g_{EPS})^t
$$

Capital expenditure, depreciation, and revenue use their configured rates. When no override is present, each equals `high_growth_rate` and the report states that choice. FCFE is calculated consistently per share: base-period CapEx, depreciation, revenue, and NWC are each divided by the matching point-in-time shares-outstanding count before projection. The report labels the count as basic shares; it does not call the result diluted EPS.

$$
RevenuePS_t=RevenuePS_0(1+g_{Revenue})^t
$$

$$
\Delta NWCPS_t=\frac{NWC_0}{Revenue_0}(RevenuePS_t-RevenuePS_{t-1})
$$

$$
FCFEPS_t=EPS_t-(CapExPS_t-DepPS_t)(1-DR_{capex})-\Delta NWCPS_t(1-DR_{wc})
$$

Both debt-financing ratios use `target_debt_weight`. This assumption is printed and is not inferred from market price.

Stable year:

$$
EPS_{n+1}=EPS_n(1+g_s)
$$

$$
FCFEPS_{n+1}=EPS_{n+1}\left(1-\frac{g_s}{ROE_s}\right)
$$

$$
TVPS_n=\frac{FCFEPS_{n+1}}{k_{e,s}-g_s}
$$

$$
VPS_0=\sum_{t=1}^{n}\frac{FCFEPS_t}{(1+k_{e,h})^t}+\frac{TVPS_n}{(1+k_{e,h})^n}
$$

`k_e,h` uses observed beta. `k_e,s` uses the explicit stable beta from the assumptions file with the same contemporaneous risk-free rate and ERP.

## Model C: Two-stage FCFF

This is the capital-structure cross-check. It is preferred over FCFE when borrowing materially distorts equity cash flow, but it needs an explicit debt weight and pre-tax cost of debt.
The high-growth stage projects total operating values:

$$
EBIT_t=EBIT_0(1+g_{EPS})^t
$$

$$
Revenue_t=Revenue_0(1+g_{Revenue})^t
$$

$$
\Delta NWC_t=\frac{NWC_0}{Revenue_0}(Revenue_t-Revenue_{t-1})
$$

CapEx and depreciation use their configured growth rates. `high_growth_rate` is both the EPS-growth rate for FCFE and EBIT-growth rate for FCFF; `revenue_growth_rate`, `capex_growth_rate`, and `depreciation_growth_rate` default to it only when omitted and are always printed.

$$
NOPAT_t=EBIT_t(1-T)
$$

$$
FCFF_t=NOPAT_t-(CapEx_t-Dep_t)-\Delta NWC_t
$$

Given target debt weight $w_d$:

$$
WACC=k_e(1-w_d)+k_d(1-T)w_d
$$

Stable year:
High-stage WACC uses observed beta. Stable WACC replaces observed beta with `stable_beta`; both stages use the configured pre-tax cost of debt, derived effective tax rate, and target debt weight. Stable NOPAT is:

$$
NOPAT_{n+1}=NOPAT_n(1+g_s)
$$

$$
FCFF_{n+1}=NOPAT_{n+1}\left(1-\frac{g_s}{ROC_s}\right)
$$

$$
TV_n=\frac{FCFF_{n+1}}{WACC_s-g_s}
$$

$$
EnterpriseValue=\sum_{t=1}^{n}\frac{FCFF_t}{(1+WACC_h)^t}+\frac{TV_n}{(1+WACC_h)^n}
$$

$$
EquityValue=EnterpriseValue+Cash-Debt-EquityOptionValue
$$

$$
ValuePerShare=\frac{EquityValue}{SharesOutstanding}
$$

The report distinguishes book-debt and option-value proxies from market values. An omitted option value is displayed as `not supplied; treated as zero`, not hidden.

## Comparison and Bear-market Interpretation

The report presents, for each model:

- applicability or exact reason it is unavailable;
- value per share and total intrinsic equity value;
- current adjusted market price and market capitalization;
- discount or premium to value;
- present value of the explicit stage;
- present value and percentage contribution of terminal value;
- all model inputs and derived rates;
- data and cache timestamps.

It also prints five-year EPS, revenue, EBIT, FCFE, FCFF, gross debt, net debt, leverage, ROE, ROC, and margin history, plus discount-rate-versus-terminal-growth sensitivity grids.

Wavebird never averages model values into one number. A user-supplied margin-of-safety threshold produces:

- `PASSES SCREEN` only when all three models are applicable, current price is below every model value, and every discount meets the threshold;
- `DOES NOT PASS` when all models are valid but at least one misses the threshold;
- `INCONCLUSIVE` when any model is unavailable.

For each model:

$$
DiscountToValue=1-\frac{MarketPrice}{ValuePerShare}
$$

The margin-of-safety condition is `DiscountToValue >= margin_of_safety`.

Interpretation text explains:

- Stable FCFE is a steady-state anchor. If only the two-stage models show upside, the thesis depends on moderate growth or recovery.
- Two-stage FCFE directly values shareholders but is sensitive to leverage and equity-reinvestment assumptions.
- Two-stage FCFF isolates operations from financing but is sensitive to WACC, debt, cash, and option adjustments.
- A large FCFE/FCFF gap points to financing or capital-structure assumptions.
- A high terminal-value share means the headline value depends heavily on long-duration assumptions.
- A falling market price is not evidence for lowering discount rates or raising growth.

## Plotting and Historical Reconstruction

`--plot PATH` writes one four-panel figure with a shared time axis:

1. **Market capitalization versus intrinsic equity value.** Raw weekly close multiplied by the latest shares-outstanding observation available by the earnings-release or fallback availability date forms market capitalization. Stable and two-stage FCFE per-share values are multiplied by that same shares-outstanding observation for plotting; FCFF uses its calculated total equity value. Annual and quarterly values from all three DCF models are separate labeled series. Missing model points remain gaps; they are not interpolated.
2. **Operating growth.** Revenue, EBIT/NOPAT, and FCFF are plotted as trailing-twelve-month values for quarterly points and annual values for annual points. A secondary indexed-to-100 view is not mixed into the same axes; the report table supplies percentage growth rates.
3. **Capital structure.** Gross interest-bearing debt, cash and short-term investments, and net debt are plotted in currency units.
4. **Debt versus operating growth.** Year-over-year revenue growth, EBIT growth, FCFF growth, net-debt growth, and debt-to-capital are plotted as rates. When prior-period net debt is zero or negative, net-debt growth is undefined: Wavebird shows the absolute change and omits the rate and indicator for that point. Otherwise, text uses `financing-assisted growth indicator` when net debt grows faster than both revenue and EBIT while leverage rises. This panel identifies co-movement only and explicitly states that the indicator is not causal evidence.

Quarterly `EARNINGS.reportedDate` is the preferred availability date. The matching fourth-quarter date is also used for an annual point. If it is absent, annual points use fiscal year-end plus 90 days and quarterly points use fiscal quarter-end plus 45 days as explicit fallback proxies. These are earnings-release or proxy dates, not original SEC filing dates, and provider history can include later restatements. The figure and CLI state that this is an analytical reconstruction, not a look-ahead-free backtest.
Every historical point uses the same user-supplied forward assumptions so the time series measures changing fundamentals, rates, beta, and market capitalization rather than silently changing the valuation thesis.

Plot tests assert the number of panels, line labels, gap behavior, earnings-release/fallback dates, and successful file creation. They do not use image-pixel snapshots.

## Error Handling

The CLI uses typed domain errors and stable nonzero exit codes for:

- missing API key;
- invalid assumptions;
- invalid ticker;
- authentication or quota response;
- transport timeout;
- provider schema error;
- missing required financial field;
- insufficient annual or quarterly history;
- insufficient market observations;
- invalid mathematical state;
- no valid valuation results.

No bare `except`, silent skipped period, or fallback constant ERP is allowed. Historical results list every unavailable period and reason.

## Verification Contract

### Golden valuation tests

The implementation must reproduce Damodaran's published workbook outputs within a documented floating-point tolerance:
All golden numeric assertions use `pytest.approx` with relative tolerance `1e-10` and absolute tolerance `1e-8`.

1. `https://pages.stern.nyu.edu/~adamodar/pc/fcfest.xls`
   - EPS `5.45`, stable growth `0.06`, stable ROE `0.12`, beta `1.1`, risk-free rate `0.07`, ERP `0.055`.
   - Cost of equity `0.1305`.
   - Expected stable FCFE value per share: `40.9716312056738`.

2. `https://pages.stern.nyu.edu/~adamodar/pc/fcfe2st.xls`
   - Expected annual FCFE values: `1.44934531768689`, `1.89792749443693`, `2.47104150291537`, `3.20127887639708`, `4.12945483295115`.
   - Terminal FCFE: `5.89724099266713`.
   - Stable cost of equity: `0.1305`.
   - Terminal value at year five: `83.648808406626`.
   - Present value of explicit FCFE: `8.40368507114114`.
   - Present value of terminal value: `43.1598751388765`.
   - Value per share: `51.5635602100177`.

3. `https://pages.stern.nyu.edu/~adamodar/pc/fcff2st.xls`
   - WACC: `0.0961694755567984`.
   - Expected annual FCFF values: `2681.99139547176`, `2965.30174776604`, `3278.53939805712`, `3624.86569628555`, `4007.77593946084`.
   - Terminal FCFF: `3247.37712358278`.
   - Enterprise value: `69175.1523336163`.
   - Equity value per share after cash, debt, option value, and share count: `66.7825642215609`.

Source workbook URLs are stored beside the test vectors. The tests encode formulas independently rather than copy intermediate results into production code.

### Other tests

- Provider fixtures cover valid annual and quarterly responses, Alpha Vantage `"None"`, misaligned fiscal periods, invalid symbols, quota envelopes, and schema changes.
- Damodaran fixtures cover Excel serial dates, percentage normalization, required columns, and annual/monthly selection by date.
- Cache tests cover cold miss, fresh hit with zero HTTP calls, expiry, refresh, atomic replacement, symbol isolation, and visibly stale fallback.
- Formula tests cover FCFE signs, working capital, TTM aggregation, beta alignment, CAPM, WACC, projection timing, terminal discounting, per-share conversion, and every guard condition.
- Reporting tests cover screen outcomes, model-unavailable reasons, terminal-value contribution, debt/growth indicators, and annual-versus-quarterly labeling.
- CLI tests cover deterministic text output, machine-readable JSON output, exit codes, `--as-of`, `--refresh`, assumptions validation, and plot creation.
- A live smoke test uses the Alpha Vantage demo key and IBM. When a user key is available, a second ticker verifies normal-key behavior. A repeated run must use SQLite and make no provider call.

Required local/CI gates:

```text
uv lock --check
ruff format --check .
ruff check .
ty check
pytest
```

The full suite runs on Python 3.12, 3.13, and 3.14.

## Clean Cutover

Implementation proceeds in vertical slices: project/tooling foundation, cache and providers, normalized annual/quarterly models, pure valuation engine, comparison report/CLI, plotting, then migration cleanup. Once the new smoke test succeeds:

- remove `main.py`, `alpha.py`, `discount.py`, `requirements.txt`, and `HistoricalRates.csv`;
- remove generated JSON, image, bytecode, and obsolete experiments unless converted into named test fixtures;
- update the existing README with installation, API-key, assumptions, commands, model explanations, data provenance, plot interpretation, and limitations;
- retain no compatibility aliases or duplicate legacy calculation path.

## Acceptance Criteria

The modernization is complete only when:

1. A clean `uv sync` installs on Python 3.12, 3.13, and 3.14.
2. A user can analyze a supported USD-reporting US-listed blue chip from the CLI with an explicit assumptions file.
3. Cold execution fetches and caches required provider data; warm execution performs no redundant provider calls.
4. Annual and monthly Damodaran data are downloaded, validated, cached, and selected by valuation date.
5. Annual and quarterly/TTM records are constructed without mixing period types or positional joins.
6. All three models either return fully explained results or an exact model-specific unavailable reason.
7. The all-model margin-of-safety screen follows the approved conservative rule.
8. Golden workbook outputs pass at the documented tolerance.
9. The plot shows market capitalization, each annual/quarterly DCF equity-value series, operating results, debt/cash/net debt, and leverage/growth rates with timing and causality limitations labeled.
10. Ruff formatting/linting, ty analysis, tests, and the live IBM smoke test pass.
11. Legacy incorrect calculations and obsolete dependency/configuration paths are removed.
