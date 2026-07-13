# NSE Dashboard — User Guide & Calculations Reference

> **This document explains what the app does, how to use it, and exactly how every score/prediction is calculated — with source file references so you can verify anything yourself.**

---

## 1. What this app is

This is a **research and signal-generation platform** for NSE-listed stocks. It ingests daily OHLCV price data, computes technical/fundamental/options/sentiment factors, and produces explainable scores and probability estimates for different holding periods (days to months).

**It is not a broker, it does not place trades, and it does not guarantee returns.** Every module in the codebase carries its own disclaimer to this effect (collected verbatim in [§6](#6-disclaimers-collected-from-the-code)). Treat every score as a research input, not a signal to act on blindly — always combine it with your own judgement and risk management.

### Tech stack

| Layer | Technology |
|---|---|
| API | FastAPI (Python), versioned routes under `/api/v1/...` |
| Database | PostgreSQL with TimescaleDB extension (time-series snapshots) |
| Cache / Pub-Sub | Redis (TTL cache + event broker for live updates) |
| Background jobs | Celery (with Redis broker), scheduled via Celery Beat (e.g. weekly predictions run 16:00 IST weekdays, monthly on the 1st, growth scanner 16:05 IST weekdays) |
| Frontend | Next.js 15 / React 19 / TypeScript |
| ML | LightGBM available for future model upgrades; current production models are deterministic, weighted "explainable baseline" models (see below) |

### Data source

All live market data — OHLCV history and quotes — comes from **Yahoo Finance**, through a single adapter class `YahooFinanceAdapter` (`nse_dashboard/infrastructure/yahoo.py`). This is the sole market data provider today; the codebase defines a provider-neutral `MarketDataAdapter` interface (`nse_dashboard/domain/market_data.py`) specifically so another provider could be swapped in later without touching any scoring logic.

- Prices are auto-adjusted for splits/dividends.
- Historical data is end-of-day; quotes fall back to a secondary method if the primary real-time endpoint fails.
- There's no explicit rate-limit handling — treat data freshness as "best effort," particularly intraday.

---

## 2. How to use the dashboard — a practical walkthrough

A sensible order to use the modules in, from "is this a good time to be in the market at all" down to "which stock, and how much":

1. **Check the Market Regime** (shown on the main dashboard). This tells you how aggressively the *whole system* should be positioned, before you even look at individual stocks.
2. **Scan the main dashboard** (`/api/v1/dashboard`, `app/page.tsx`) for BUY/SELL signals by sector — this is the fastest, most technical-only view.
3. **Check Weekly or Monthly Predictions** for a probability- and expected-return-ranked list of stocks, depending on your intended holding period (5 sessions vs. 1–12 months).
4. **Check the Five-Percent Strategy or AI Growth Scanner** if you want a specific target/stop-loss framework (does this stock reach +X% before -Y% within N days), with an explicit probability and risk-reward number.
5. **Cross-check with Growth Radar / Alpha Rankings** for a fundamentals- and ownership-quality lens (helps avoid "good chart, bad company" traps), and **Smart Money** for options-flow confirmation on liquid names.
6. **Only after a stock clears technical + fundamental + regime checks**, use the position-sizing formula (§3.9) to decide how much capital to risk.

### Market Regime badge

Two binary conditions decide the regime (`trading/indicators.py::market_regime`):
- Price above the 200-day EMA?
- Price above a *rising* 10-month EMA (monthly close above its own 10-period EMA, and that EMA itself trending up)?

| Regime | Conditions true | Max exposure | Risk per trade |
|---|---|---|---|
| RISK_ON | both | 80% | 0.5% |
| NEUTRAL | one | 40% | 0.25% |
| RISK_OFF | none | 0% | 0% |

Read this as a portfolio-level throttle: in RISK_OFF, the system is telling you the broad market trend doesn't support aggressive new positions, regardless of how good an individual stock's score looks.

### "Entry Ready" flag

A stock is flagged entry-ready (`trading/indicators.py::entry_indicators`) only when **all six** of these are true:
1. Supertrend(10, 3.0) is bullish
2. Either a recent bullish Supertrend flip (last 5 bars) or a fresh 20-day breakout
3. RSI(14) is between 50 and 70 (healthy momentum, not overbought/oversold)
4. Volume ≥ 1.5× the 20-day average (confirmation)
5. Price is within 8% of the 20-EMA (not overextended)
6. The implied stop (Supertrend line, or close − 2×ATR, whichever is higher) is between 1% and 8% away from price (a sane, tradeable stop distance)

If any condition fails, the stock is not "ready" and the failing condition(s) are listed as rejection reasons in the API response — useful for understanding *why* a good-looking stock isn't flagged.

---

## 3. Module-by-module formulas

### 3.1 Weekly Predictions (`services/weekly_predictions.py`)

Answers: *"Over the next 5 trading sessions, how likely is this stock to move favorably, and by how much?"*

Requires ≥210 sessions of history. Starting from a base score of 50:

```
score  = 50
score += clamp(momentum_5d  * 2.0, -15, 15)
score += clamp(momentum_20d * 0.6, -12, 12)
score += clamp(ema20_50_spread * 2.0, -8, 8)
score += clamp(ema50_200_spread, -6, 6)
score += clamp((volume_ratio - 1) * 5, -5, 5)

if RSI14 > 75:            score -= 8   # overbought
elif 52 <= RSI14 <= 68:   score += 6   # healthy zone
elif RSI14 < 35:          score -= 8   # oversold / weak

score -= max(0, volatility_20d - 6) * 1.2
score  = clamp(score, 0, 100)

probability = 1 / (1 + e^(-(score - 55) / 11))     # sigmoid, so probability >50% once score exceeds ~55

predicted_return_pct = clamp(
    momentum_5d * 0.30 + momentum_20d * 0.12 + ema20_50_spread * 0.25
    + max(0, volume_ratio - 1) * 0.55 - max(0, volatility_20d - 5) * 0.12,
    -12, 12
)

risk_score = clamp(volatility_20d * 7 + max(0, RSI14 - 70), 0, 100)
```

**Default filters** used when generating the ranked list: probability ≥ 0.60, expected return ≥ 2.0%, average traded value ≥ ₹1 crore, top 5 picks per sector.

**How to read it**: `probability` is the model's estimate of a favorable 5-day move given current technicals; `predicted_return_pct` is the expected magnitude; `risk_score` (0–100, higher = riskier) flags stretched/volatile names even if the probability looks good.

### 3.2 Monthly Predictions (`services/monthly_predictions.py`)

Answers the same question as weekly, but for a configurable 1–12 month horizon, using monthly-resampled data (needs ≥300 daily rows / ~18 completed monthly bars).

Score is a 100-point breakdown:

| Component | Max points | Rule |
|---|---|---|
| Trend | 30 | +10 if price > EMA12(monthly); +10 if EMA6 > EMA12; +10 if EMA3 > EMA6 |
| Momentum | 30 | `clamp(5 + momentum_1m*0.7, 0, 10)` + `clamp(10 + (horizon_momentum/√horizon_months)*0.65, 0, 20)` |
| Volume | 10 | `clamp(5 + (vol_3m_avg/vol_12m_avg - 1)*12, 0, 10)` |
| RSI quality | 10 | 10 pts if RSI∈[50,68]; 6 pts if RSI∈[40,50)∪(68,75]; 3 pts if RSI∈[30,40); else 0 |
| Risk control | 20 | volatility points `clamp(10 - max(0, ann_vol-20)*0.25, 0, 10)` + drawdown points `clamp(10 + drawdown*0.5, 0, 10)` |

```
total_score = trend + momentum + volume + rsi_quality + risk_control   (clamped 0–100)
probability = 1 / (1 + e^(-(total_score - 58) / 11))

predicted_return_pct = clamp(
    horizon_momentum * 0.35 + momentum_1m*√horizon_months*0.25
    + max(0, trend-15)*0.10*√horizon_months
    - max(0, ann_vol-30)*0.05*√horizon_months,
    -40, 60
)
risk_score = clamp(ann_vol * 1.5 + |min(0, drawdown)| * 0.8, 0, 100)
```

Default filter: total score ≥ 60; same liquidity floor and top-5-per-sector cap as weekly.

### 3.3 Conservative Monthly Strategy (`trading/monthly.py`)

A stricter, gate-based variant used for longer-term "stay invested" signals:

| Component | Max points | Formula |
|---|---|---|
| Relative strength (6m) | 30 | `clamp(15 + rel_strength_6m*1.5, 0, 30)` |
| Momentum 12–1 month | 25 | `clamp(momentum_12_to_1m * 0.8, 0, 25)` |
| Momentum 6 month | 20 | `clamp(momentum_6m * 0.8, 0, 20)` |
| Trend strength | 15 | `clamp(7.5 + (monthly_spread+weekly_spread)*0.5, 0, 15)` |
| Liquidity/volatility | 10 | `clamp(10 - max(0, volatility-25)*0.2, 0, 10)` |

A stock only qualifies if **all** of these gates pass: price above a rising 10-month EMA (monthly), price above a rising 30-week EMA (weekly), positive 6-month momentum, positive 12-to-1-month momentum, positive relative strength, entry-ready (§2), and market regime is not RISK_OFF. This is the most conservative screen in the app — treat a "qualified" flag here as a higher-confidence signal than a bare score elsewhere.

### 3.4 Fundamentals Score (`services/fundamentals.py`)

Answers: *"Is this a good business, independent of its chart?"* Converts 10 financial ratios into a 0–100 composite via linear normalization between a "poor" and "good" bound per metric, then weights them:

| Metric | Poor→Good range | Weight |
|---|---|---|
| ROE | 5% → 25% | 15% |
| ROCE | 6% → 30% | 15% |
| Operating margin | 5% → 30% | 10% |
| Net margin | 2% → 20% | 10% |
| Revenue growth (TTM) | −5% → 25% | 10% |
| Profit growth (TTM) | −10% → 30% | 10% |
| FCF growth | −10% → 30% | 10% |
| Debt/Equity (inverse) | 2.0 → 0.0 | 10% |
| Current ratio | 0.7 → 2.0 | 5% |
| Promoter holding change QoQ | −2% → 1% | 5% |

**Value-trap penalty**: if PE is rich relative to sector PE — `premium = PE/sector_PE − 1` — a penalty of `clamp(50 × (premium − 0.30), 0, 15)` is subtracted from the base score. This discourages high scores driven purely by an expensive valuation.

**Grades**: A ≥ 80, B ≥ 65, C ≥ 50, D ≥ 35, F < 35. Coverage is "FULL" if ≥8 of the 10 metrics were available, else "PARTIAL" — check coverage before trusting a grade on a thinly-covered stock.

### 3.5 Multi-Factor Alpha Ranking (`services/alpha_ranking.py`)

Blends whichever of these factors are available for a stock: **Technical 30%, Options/Smart Money 20%, Fundamental 30%, Sentiment 10%**. If a factor is missing, its weight is redistributed proportionally across the remaining available factors — but a stock needs at least 60% of the total base weight covered by available factors, or it's excluded entirely (never silently scored on partial junk data).

```
combined_score = clamp(0.90 * weighted_average_of_available_factors + legal_credit - legal_penalty, 0, 100)
```
- `legal_credit` = +10 if legal risk is known at all (rewards transparency/coverage)
- `legal_penalty` = 0.10 × legal_risk_quotient

Legal risk flag: Low (<35), Medium (35–70), High (≥70), Unknown (not covered). A hard 20%-per-sector exposure cap is applied when constructing the ranked list, so no single sector dominates the top picks.

### 3.6 Growth Radar (`services/growth_radar.py`)

Answers: *"Is this company inflecting — accelerating earnings, deleveraging, or executing a large order book — before the market has fully priced it in?"* Combines six independent lenses (each 0–100, averaged from their own sub-scores):

1. **Earnings inflection** — revenue/profit acceleration, margin expansion, return quality (ROCE + cash conversion)
2. **Order book & capex** — order book/revenue ratio, book-to-bill, order growth, execution growth, sector capex exposure (penalized if receivable days > 150)
3. **Turnaround/deleveraging** — EBITDA/profit inflection (loss→profit scores 100), net-debt/EBITDA improvement, interest coverage, operating cash flow
4. **Valuation** — PE, EV/EBITDA, Price/Sales, PEG, each relative to sector
5. **Ownership** — institutional holding change, promoter stability, promoter pledge (inverse)
6. **Catalyst score** — passed through directly from upstream feature data

**Penalties** (capped at 40 total): promoter pledge > 10% (+up to 15), 12-month equity dilution > 5% (+up to 10), legal risk (+up to 15), negative operating cash flow (+10), auditor qualification (+15).

```
strength_score = clamp(average of the 6 lenses − total penalty)
```

**States**: REJECTED (penalty ≥30 or score <35), EARLY_WATCH (55–68), BUILDING_STRENGTH (68–78), QUALIFIED (≥68), BREAKOUT_CONFIRMED (≥78 and accumulation ≥75). Backtested validation targets used to judge the model itself: a "compounder" needs 12-month return ≥50% *and* ≥30% excess return over the index; a "multibagger" needs 24-month return ≥100% and ≥50% excess return.

### 3.7 Smart Money / Options Flow (`options/smart_money.py`)

Answers: *"Is options positioning showing unusual institutional activity in this name?"* Five factors, each min-max normalized against their own 20-day trailing range, then weighted:

| Factor | Weight | Meaning |
|---|---|---|
| Volume ratio | 30% | contract volume ÷ open interest — high = fresh positioning, not just rollovers |
| Open interest change | 25% | day-over-day OI build |
| IV momentum | 20% | rate of change in implied volatility — rising IV can precede a move |
| GEX contribution | 15% | this contract's share of total gamma exposure — flags where dealer hedging flows concentrate |
| Bid-ask tightness | 10% | `1 − (ask−bid)/midpoint` — tighter spreads suggest more liquid, "watched" contracts |

Only meaningful for symbols with active option chains — most small/mid-cap equities won't have this data, and other modules (like the Growth Scanner) fall back to a neutral 50 score rather than penalizing a stock for lacking options liquidity.

### 3.8 Five-Percent Growth Strategy (`five_percent_strategy/`)

Answers exactly: *"Will this stock hit +5% before −2% within 5 trading days?"* (all three numbers are configurable). Six weighted components:

| Component | Weight |
|---|---|
| Momentum | 25% |
| Trend | 20% |
| Volume | 15% |
| Breakout | 15% |
| Relative strength (vs Nifty) | 15% |
| Risk | 10% |

```
momentum_score = clamp(50 + momentum_5d*4 + momentum_20d*1.2)
trend_score:    +12 if close>EMA20, +8 if close>EMA50, +10 if EMA9>EMA20, +10 if EMA20>EMA50, then clamp(50+bias-15)
volume_score    = clamp(50 + (volume_ratio-1)*40)
breakout_score  = clamp(50 + 20[if 20d-high breakout] + distance-from-52w-high adjustment)
relative_strength_score = clamp(50 + relative_strength_vs_nifty*5)
risk_score: -15 if RSI>78, -10 if RSI<30, minus volatility penalty; clamp(100 - penalties)

probability_score = clamp(Σ component_score × weight)     # 0–100
ai_score           = probability_score / 10                # 0–10, same number just rescaled
```

Candidates below a probability threshold (default 65%) are dropped. **Position sizing** (`risk.py`):
```
risk_per_share      = entry_price − stop_loss_price
shares_by_risk       = (capital × max_risk_per_trade_pct) / risk_per_share
shares_by_capital     = (capital × max_capital_per_trade_pct) / entry_price
shares                = floor(min(shares_by_risk, shares_by_capital))
```
Default limits: max 20% of capital per trade, max 1.0% capital risked per trade, 2% daily / 5% weekly max loss, min turnover ₹1 crore, max volatility 90%.

**Backtester** (`backtester.py`) reports: win rate (%), average win/loss %, profit factor (gross profit ÷ gross loss), max drawdown % (from the equity curve), longest win/loss streaks, and total return — with a cost model applying slippage (bps) to entry/exit prices and a flat cost (bps) to every trade, so results reflect realistic (not frictionless) execution.

### 3.9 AI Growth Probability Scanner (`growth_scanner/`)

The newest module — same target/stop/holding-period question as §3.8 (defaults +5% / −2% / 5 days), but built on a broader, six-group Feature Engine so business quality, sector, and market context are explicit inputs rather than being implicit in price action alone:

| Component | Weight |
|---|---|
| Business Quality | 30% |
| Technical | 20% |
| Smart Money | 15% |
| Risk | 15% |
| Sector | 10% |
| Market | 10% |

- **Business Quality** = 60% the existing Fundamentals Score (§3.4) + 40% a supplemental blend of PEG (30%), Price/Sales (15%), EV/EBITDA (15%), promoter pledge (20%), PE-vs-industry (10%), market cap (10%).
- **Technical** = weighted blend of trend (25%), momentum (20%), breakout (15%), volume (15%), ADX (10%), RSI (10%), VWAP/relative-strength (5%).
- **Smart Money** = the options-flow score from §3.7 when available, else a neutral 50 (never hard-filtered out).
- **Sector** = 40% sector momentum + 40% sector relative strength vs. Nifty + 20% sector breadth (share of sector peers with positive 5-day return).
- **Market** = 40% Nifty trend (price vs. 200-EMA + 20-day momentum) + 30% India VIX level (lower VIX → higher score) + 30% market-wide breadth/advance-decline.
- **Risk** = liquidity (25%), ATR-relative-to-price (20%), drawdown from recent high (15%), gap risk (15%), volatility (15%), risk-reward ratio (10%).

```
overall_score     = Σ component_score × weight        # 0–100
ai_score           = overall_score / 10                 # 0–10
probability_score  ≈ overall_score                       # same weighted formula, produced by the baseline model
```
Risk label: LOW if risk_score ≥65, MEDIUM if ≥40, HIGH otherwise. The scoring model sits behind a swappable interface (`GrowthPredictionModel` protocol) so a trained XGBoost/LightGBM model can later replace the deterministic baseline without changing the API.

---

## 4. Quick-reference table

| Module | What it answers | Score formula summary | Range | Default gate |
|---|---|---|---|---|
| Market Regime | Should the whole system be aggressive? | 2 binary EMA conditions | RISK_ON/NEUTRAL/RISK_OFF | n/a |
| Entry Ready | Is now a tactically good entry? | 6 conditions, all must pass | Boolean | all pass |
| Weekly Predictions | 5-day favorable-move probability | weighted momentum/trend/RSI/volatility → sigmoid | probability 0–1, score 0–100 | prob ≥0.60 |
| Monthly Predictions | 1–12 month favorable-move probability | Trend30+Momentum30+Volume10+RSI10+Risk20 → sigmoid | 0–100 | score ≥60 |
| Conservative Monthly | Long-term "stay invested" signal | 5-component (30/25/20/15/10) + hard gates | 0–100 | all gates pass |
| Fundamentals | Business quality independent of chart | 10-component weighted, value-trap penalty | 0–100, A–F grade | n/a |
| Alpha Ranking | Combined multi-factor rank | Technical30/Options20/Fundamental30/Sentiment10 + legal adj. | 0–100 | ≥60% factor coverage |
| Growth Radar | Early-stage business inflection | 6-lens average − penalties | 0–100 | QUALIFIED ≥68 |
| Smart Money | Unusual options positioning | 5-factor weighted normalization | 0–100 | n/a (options-only) |
| Five-Percent Strategy | P(+5% before −2% in 5d) | 6-component weighted (25/20/15/15/15/10) | 0–100 | prob ≥65% |
| AI Growth Scanner | P(target before stop, configurable) | 6-component weighted (30/20/15/15/10/10) | 0–100 | configurable |

---

## 5. Interpreting a score responsibly

- **Probability ≠ certainty.** A 70% weekly probability means the model's historical calibration suggests roughly 7-in-10 similar setups moved favorably — not that this specific trade will.
- **Check coverage before trusting fundamentals/alpha scores.** Both explicitly report coverage (FULL/PARTIAL, or factor-weight redistribution) — a high score built on partial data is weaker evidence than the same score with full coverage.
- **Regime overrides everything.** A high-probability stock pick in a RISK_OFF regime is a lower-conviction bet than the same pick in RISK_ON — the app tells you this explicitly via the regime badge and exposure caps.
- **Use position sizing, don't eyeball it.** The Five-Percent Strategy's position-sizing formula (§3.8) exists specifically so a single losing trade can't blow past your risk tolerance.
- **This is a research tool, not a broker.** Nothing in this app executes trades; every module says so in its own disclaimer.

---

## 6. Disclaimers (collected from the code)

- **Weekly Predictions**: "Model estimates are research signals, not guaranteed returns or investment advice."
- **Monthly Predictions**: "Scores are model estimates for research, not guaranteed returns or investment advice."
- **Five-Percent Strategy**: "This strategy is for educational purposes and research. It is not a guarantee of returns, and past performance does not imply future results. Always conduct your own due diligence and consult with a financial advisor before making investment decisions."
- **AI Growth Probability Scanner**: "This module is a research engine only. It estimates the probability that a stock reaches a target return before a stop loss within a holding period, using historical technical, fundamental, smart-money, sector, and market factors. It does not place or recommend trades and does not guarantee outcomes."
- **Growth Radar**: "Scenario prices are quantitative research estimates, not assured targets or investment advice."
- **Alpha Ranking**: "Rankings are quantitative research signals, not investment advice or a guarantee of returns."
- **Main dashboard**: "Research-only content; not investment advice."
