# Levels Reversal Strategy

> Status: validated on SBER/GAZP/VTBR (2-year history). Prototype engine: `backend/app/analytics/levels_backtest.py`.
> Production integration (DB persistence, matrix API) — in progress (task-078..080).
> Last updated: 2026-07-26.

## 1. Overview

A long-only, medium-term rule-based strategy for MOEX blue chips. Core idea: **enter near a support level only after a higher-timeframe reversal is confirmed**, exit at the nearest resistance (take) or below support (stop). Simulated on 1-minute candles for precise entry/exit control, while structure (levels, signals) is taken from the 4h timeframe.

This is the first strategy in the project that is **profitable after commission and slippage on out-of-sample tickers** (GAZP, VTBR were not used for parameter selection — parameters were fixed on SBER).

## 2. Logic

### 2.1 Support/Resistance levels (4h)
- **Swing levels:** fractals on 4h candles — a bar whose high/low is extreme relative to `swing_window` bars left and right. Clustered into zones of `±zone_atr × ATR(14)`.
- **Impulse candle levels:** filled impulse candles (body/range > 0.7 AND |close−open| > 1.5×ATR); candle open is the level (green → support, red → resistance).
- **Rolling window (no look-ahead):** a swing level defined on bar `i` is available only from bar `i + swing_window`; an impulse level — from bar `i`.
- ATR is computed on the fly from 4h candles (independent of the `indicators` table).

### 2.2 Entry conditions (all must hold)
1. **Signal gate:** a 4h BUY signal is active (`entry_mode`): `levels_ts1` (total_signals ≥ 1) or `levels_ts2` (≥ 2) or `levels_only` (no signal required).
2. **Price near support:** 1min close is in the support zone `[zone_lower, zone_upper]` or just above it (within 0.5×ATR).
3. **Reversal confirmation** (`confirm_tf`): the last **closed** higher-TF candle (5/10/30 min) closed **above** the support zone (`close > zone_upper`). This filters out "whipsaw" — false touches that hit the stop on the next bar.
4. **Entry window:** 7:00–19:00 MSK (by 1min bar hour).
5. **Risk-reward filter** (`risk_reward`, variant "a"): enter only if `(resistance − entry) ≥ risk_reward × (entry − stop) + commission_cost`. `risk_reward = 0` disables the filter.

### 2.3 Exit conditions
- **Stop:** nearest support level below entry; triggered by 1min **low** (conservative fill at stop).
- **Take:** nearest resistance level above entry; triggered by 1min **high** (fill at take).
- **Overnight/weekend:** position is held (no session-only exit). Blue chips, minimal gaps/slippage.

### 2.4 Costs
- **Commission:** 0.03% per side (0.06% round-trip).
- **Slippage:** `slippage_per_side` applied to executed entry (buy higher) and exit (sell lower) prices.

## 3. Parameters (tunable)

| Parameter | Values | Default | Effect |
|---|---|---|---|
| `swing_window` | 5 / 10 / 15 / 20 | **10** | Fractal window for swing levels. Smaller = more (noisier) levels; larger = fewer (stronger) levels. |
| `zone_atr` | 0.3 / 0.5 / 0.8 | **0.5** | Support/resistance zone width in ATR. Wider = more entries, less precise. |
| `confirm_tf` | none / 5min / 10min / 30min | **10min** | Reversal confirmation candle timeframe. `none` = enter on touch (whipsaw, PF ~0.97). 5/10/30min cut whipsaw → PF 1.4–2.0. |
| `risk_reward` | 0.0 / 1.5 / 2.0 | **2.0** | Min reward:risk to enter. 0 = no filter (more trades, lower PF). 2.0 = strict (fewer trades, higher PF). |
| `entry_mode` | levels_only / levels_ts1 / levels_ts2 | **levels_ts1** | Signal gate. `levels_only` = pure levels (PF ~0.5, bad). `levels_ts1`/`ts2` = + pattern signal (PF ~0.95+). |
| `entry_window_start` / `_end` | 0–23 | **7 / 19** | Entry window in MSK hours. |
| `slippage_per_side` | 0.0 / 0.0005 / 0.001 | **0.0** | Slippage per side. Realistic for liquid names: 0.0005 (0.05%). |
| `commission_per_side` | float | **0.0003** | Commission per side (0.03%). |
| `body_ratio` | float | **0.7** | Impulse candle "filled" threshold (body/range). |
| `impulse_atr_mult` | float | **1.5** | Impulse candle size threshold (× ATR). |

**Recommended baseline:** `swing_window=10, zone_atr=0.5, confirm_tf=10min, risk_reward=2.0 (or 1.5), entry_mode=levels_ts1, slippage=0.0005`.

## 4. Results

### 4.1 Parameter matrix on SBER (task-075, 2 years, commission 0.06%, no slippage)

| confirm_tf | risk_reward | n | PF | Exp % | WR | Net % |
|---|---|---|---|---|---|---|
| none | 2.0 | 1054 | 0.966 | −0.004 | 14.0 | −3.9 |
| none | 1.5 | 1028 | 1.013 | +0.001 | 16.5 | +1.5 |
| none | 0.0 | 1694 | 0.824 | −0.017 | 23.0 | −29.0 |
| 5min | 2.0 | 125 | 1.852 | +0.238 | 36.8 | +29.7 |
| 10min | 2.0 | 125 | **1.937** | +0.250 | 37.6 | +31.2 |
| 10min | 1.5 | 139 | 1.865 | +0.234 | 41.0 | **+32.5** |
| 10min | 0.0 | 508 | 1.421 | +0.054 | 48.8 | +27.5 |
| 30min | 1.5 | 151 | 1.883 | +0.207 | 40.4 | +31.3 |

**Key:** without confirmation (`none`) the strategy is ~breakeven/loss (whipsaw). With 5/10/30min confirmation — all PF > 1.4.

### 4.2 Out-of-sample validation (task-076a, SBER/GAZP/VTBR, configs 10min/rr=2.0 & 10min/rr=1.5, slippage 0/0.05/0.1%)

| Ticker | PF>1 (of 6) | Best PF | Worst PF | Net % range | MaxDD % | Verdict |
|---|---|---|---|---|---|---|
| **GAZP** | 6/6 | 2.061 | 1.255 | +14.0 … +34.9 | 6.9–8.9 | Strong, robust |
| **SBER** | 6/6 | 1.937 | 1.086 | +4.7 … +32.5 | 5.8–19.8 | Solid |
| **VTBR** | 4/6 | 1.279 | 0.885 | −11.0 … +19.1 | 11.1–27.8 | Weak link (PF<1 at slippage 0.1%) |

**Key:** edge holds on GAZP and SBER at all slippage levels (0–0.1%). VTBR is profitable only at slippage ≤ 0.05%. Realistic slippage for liquid names is 0.02–0.05%, where all three tickers have PF > 1.

## 5. How to run

Prototype (in-memory, results to stdout/report, **not yet persisted to DB**):

```python
from app.db.db_manager import DBManager
from app.analytics.levels_backtest import run_levels_backtest
db = DBManager()
res = run_levels_backtest(
    db, ticker='SBER', entry_mode='levels_ts1',
    swing_window=10, zone_atr=0.5, confirm_tf='10min',
    risk_reward=2.0, slippage_per_side=0.0005,
    entry_window_start=7, entry_window_end=19,
)
print(res['metrics'])  # n_trades, profit_factor, expectancy, win_rate, total_net_pct, max_drawdown_pct
```

Production matrix runner (API + DB persistence) — see task-078..080 (in progress).

## 6. Known limitations

- **VTBR is a weak link** — PF drops below 1 at slippage 0.1%, higher drawdown (up to 27.8%). Possibly less "level-friendly" / noisier.
- **Slippage is critical** — net result drops sharply with slippage (SBER: +31% → +6% from 0% to 0.1%). Use realistic slippage (0.05%) for liquid names.
- **GAZP trade count is borderline** (82–104) — statistically significant but expand the universe for reliability.
- **Not yet persisted to DB** — prototype returns JSON; production integration (task-078..080) adds `backtest_*` writes.
- **Fixed top-3 universe so far** — expand to 30 tickers (requires loading 1min history for the other 27).

## 7. Roadmap

- **task-078:** persist results to DB (`backtest_runs/trades/equity/metrics`).
- **task-079:** matrix orchestrator + API (`POST /api/levels-backtest/run`, `GET .../status`), shared lock, background run.
- **task-080:** matrix run test via API + DB verification.
- **Expand universe:** load 1min history for remaining 27 top-30 tickers, validate on full universe.
- **ML (optional):** use levels + indicators as features in CatBoost/LightGBM to strengthen the edge.

## 8. Full Universe Validation (task-080e, 2026-07-26)

Config: 28 tickers (count>250000 in candles_1min_raw) × single config (swing10/zone0.5/confirm10min/rr=2.0/slippage0.05%/levels_ts1), 2-year history, commission 0.06% round-trip.

**Result: edge is universe-wide, NOT a SBER/GAZP artefact.**
- **25 of 28 tickers (89%) have PF > 1** at realistic slippage 0.05%.
- **Median PF 1.322, mean PF 1.324** (they nearly coincide → flat distribution, the typical ticker is profitable, not a few winners skewing the mean).
- **All 28 statistically significant** (n_trades 82–267, reliable=true).

### Top 10 by PF

| Ticker | n | PF | Exp % | WR | MaxDD % |
|---|---|---|---|---|---|
| RUAL | 117 | 2.07 | +0.505 | 46.2 | 5.5 |
| GMKN | 96 | 1.91 | +0.416 | 35.4 | 7.8 |
| PIKK | 133 | 1.74 | +0.465 | 35.3 | 13.0 |
| GAZP | 82 | 1.71 | +0.319 | 42.7 | 7.9 |
| SIBN | 91 | 1.65 | +0.329 | 34.1 | 12.9 |
| LKOH | 107 | 1.62 | +0.266 | 34.6 | 9.2 |
| PLZL | 111 | 1.48 | +0.248 | 33.3 | 8.6 |
| MTSS | 106 | 1.47 | +0.218 | 39.6 | 7.8 |
| SBER | 125 | 1.45 | +0.149 | 36.0 | 9.1 |
| MTLR | 89 | 1.41 | +0.363 | 30.3 | 12.3 |

### Weak / unprofitable (3)

| Ticker | n | PF | Exp % | MaxDD % | Note |
|---|---|---|---|---|---|
| AFKS | 94 | 0.98 | −0.011 | 12.0 | ~breakeven |
| CHMF | 97 | 0.86 | −0.125 | 31.7 | steel, high DD |
| NLMK | 124 | 0.80 | −0.142 | 27.8 | steel, high DD |

Both losers are steelmakers (CHMF, NLMK) — hypothesis: strong trends, few clean level bounces. Borderline (PF 1.03–1.06, high DD): MGNT, MOEX, VTBR, SNGS.

**Conclusion:** the levels-reversal edge is real and distributed across the MOEX universe (25/28 tickers, median PF 1.32, all n≥30), robust to realistic slippage. Next: walk-forward (out-of-time) validation to confirm the edge is not regime-specific.
