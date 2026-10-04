# Strategy Testing Rules

Rules and parameters for strategy testing in the trading terminal.

## 1. Patterns (AND logic)

A strategy is built from one or more patterns. When several patterns are selected,
a signal fires **only when ALL selected patterns trigger on the same bar** (AND logic).
One selected pattern = backtest on that pattern alone.

| Pattern | Condition (BUY) | Notes |
|---|---|---|
| `levels_reversal` | price in 4h support zone + reversal confirmation on selected windows | primary pattern; defines stop (support) and take (resistance) |
| `rsi_oversold` | RSI-14 < 30 | indicator AND-filter |
| `macd_bullish` | MACD histogram > 0 | indicator AND-filter |
| `bb_lower` | close below Bollinger(20,2) lower band | indicator AND-filter |
| SignalEngine ids (`PA_Hammer`, `MR_RSI_Reversal`, ...) | BUY on last closed HTF bar via inline `evaluate` | AND-filter; `timeframe` select (30min/1h/2h/4h/1d/1w, default 4h). Not a `trading.signals` lookup. Not a substitute for `rsi_oversold`. |

`levels_reversal` is required for a trade (it defines stop/take). Indicator and SignalEngine patterns
act as additional AND-filters on top of it. `signal_4h_buy` stays a 4h BUY lookup in `trading.signals`.

In the Strategy Lab constructor, enable a SignalEngine filter by adding its chip (schema from `GET /api/patterns`). The timeframe is chosen in the pattern settings modal (default 4h). Save → `normalize_patterns` → the same config for backtest, paper, and live. Do not overwrite locked `test_20260731`.

## 2. Confirmation windows (for `levels_reversal`)

Available values (minutes): **1, 5, 10, 15, 20, 25, 30, 60, 90, 120**.

When several windows are selected, confirmation is checked **sequentially for each
selected value** and must hold on **all** of them (AND): the last closed candle of
every selected timeframe must close above the support zone.

## 3. Commission (required)

A single round-trip value entered manually, e.g. `0.06` (= 0.06% round trip,
0.03% per side). Applied to every trade (entry + exit).

## 4. Slippage (required)

A single per-side value entered manually, e.g. `0.06` (= 0.06% per side).
Entry executed at `price * (1 + slip)`, exit at `price * (1 - slip)`.

## 5. Risk/Reward (optional)

Two manual values: `risk` (e.g. 1.0) and `reward` (e.g. 2.0). Entry is taken only if
`reward / risk >= ratio` (e.g. 2.0), i.e. `(take - entry) >= ratio * (entry - stop)`
plus commission cost. If not selected, the filter is not applied.

## 6. Test depth

| Depth | History | Tickers | Methods | Runs |
|---|---|---|---|---|
| **Express** | 6 months | 3 (SBER, LKOH, PIKK) | full-sample | ~10 |
| **Serious** | 6 months | 15 | full-sample + walk-forward | 40 |
| **Very serious** | 2 years (full) | all (>250k 1min candles) | full-sample + walk-forward | 100 |

## 7. Test methods

- **Full-sample**: backtest over the entire selected period.
- **Walk-forward**: split into half-year windows (2024-H2, 2025-H1, 2025-H2, 2026-H1);
  compute PF per window, then `PF>1` count, `min PF`, `avg PF`.

Both methods are enabled by default (checkboxes); can be toggled independently.

## 8. Number of runs

Bootstrap iterations over the trade list for stability estimation (default **40**,
same for both methods, entered manually). `1` = deterministic single pass.
Reports mean PF plus `pf_min` / `pf_p25` for robustness.

## 9. A/B factors

Each position is tagged with four independent factors for comparative analysis:

| Factor | Values | Meaning |
|---|---|---|
| `signal_source` | `base` / `imbalance` | level+confirmation vs +volume-imbalance filter |
| `window_mode` | `window` / `always` | entry window 7-19 MSK vs 24/7 |
| `rr_mode` | `all` / `rr15` / `rr2` | no RR filter vs RR>=1.5 vs RR>=2.0 |
| `entry_mode` | `market` / `limit` | fill at best_ask vs limit order at signal price (TTL 20 min) |

## 10. Metrics

| Metric | Definition |
|---|---|
| `n` | number of trades |
| `PF` | profit factor = gross profit / gross loss |
| `Exp %` | expectancy = average net return per trade |
| `WR` | win rate = % of winning trades |
| `MaxDD %` | maximum peak-to-trough drawdown of cumulative net return |

## 11. Strategy storage & reuse

After configuration the strategy is named (English, DB-safe characters) and saved to
the DB (name + parameter set). A saved strategy (single choice from the DB list) can be
applied to any tickers selected from the **TOP-30 universe with >250k 1min candles**
(multiple choice via checkboxes).

## 12. Report formats

Backtest:

| Ticker | n | PF | Exp % | WR | MaxDD % |
|---|---|---|---|---|---|

Walk-forward:

| Ticker | 2024-H2 | 2025-H1 | 2025-H2 | 2026-H1 | PF>1 | min PF | avg PF |
|---|---|---|---|---|---|---|---|
