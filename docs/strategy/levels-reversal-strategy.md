# Levels Reversal Strategy

> Status: validated on SBER/GAZP/VTBR (2-year history). Production brain: `StrategyEvaluator.check_entry`. Prototype: `backend/app/analytics/levels_backtest.py`.
> Last refreshed: 2026-08-21 (Issue #108 level_breakout_retest validation).

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
6. **Resistance-zone veto** (Issue #97): 1min close is **not** inside any active resistance zone `[zone_lower, zone_upper]`. This is not role-reversal. Buying inside resistance while the journal records a support stop is a defect (ALRS paper #711). `nearest_level_at(..., 'support')` stays one-sided; the veto is `overlapping_resistance_zone_at` in `levels_engine.py`, applied in `StrategyEvaluator.check_entry` (same path for backtest / paper / live).

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

## 9. Walk-Forward (Out-of-Time) Validation (task-082a, 2026-07-26)

Tests whether the edge is **regime-independent** (not overfit to one market regime). 2-year history split into 4 non-overlapping half-year windows: 2024-H2, 2025-H1, 2025-H2, 2026-H1. Config: 10min/rr=2.0/slip=0.05%/levels_ts1. Run on top-10 tickers by full-period PF.

**Result: edge is regime-independent.**
- **4 tickers hold PF > 1 in ALL 4 windows**: RUAL (min 1.68), GMKN (min 1.08), PIKK (min 1.34), GAZP (min 1.14).
- **All 10 top tickers hold PF > 1 in ≥3 of 4 windows.**

### Stability table (PF per window)

| Ticker | 2024-H2 | 2025-H1 | 2025-H2 | 2026-H1 | PF>1 count | min PF | avg PF |
|---|---|---|---|---|---|---|---|
| RUAL | 2.23 | 1.68 | 2.46 | 1.95 | 4/4 | 1.68 | 2.08 |
| GMKN | 9.53* | 1.59 | 1.18 | 1.08 | 4/4 | 1.08 | 3.35 |
| PIKK | 2.21 | 1.54 | 1.98 | 1.34 | 4/4 | 1.34 | 1.77 |
| GAZP | 1.54 | 2.07 | 1.56 | 1.14 | 4/4 | 1.14 | 1.58 |
| SIBN | 0.47 | 1.57 | 2.75 | 3.23 | 3/4 | 0.47 | 2.00 |
| LKOH | 3.96 | 1.19 | 0.87 | 1.80 | 3/4 | 0.87 | 1.96 |
| PLZL | 6.28* | 0.98 | 1.12 | 1.30 | 3/4 | 0.98 | 2.42 |
| MTSS | 1.79 | 1.78 | 0.72 | 2.95* | 3/4 | 0.72 | 1.81 |
| SBER | 1.97 | 2.35 | 0.74 | 1.40 | 3/4 | 0.74 | 1.62 |
| MTLR | 2.50 | 1.44 | 1.09 | 0.72 | 3/4 | 0.72 | 1.44 |

\* extreme PF (9.53, 6.28, 2.95) — artefact of small n (6–9 trades in window), do not overvalue.

**Notes:**
- 2025-H2 is a weak window for several tickers (SBER 0.74, MTSS 0.72, LKOH 0.87), but even there RUAL 2.46, PIKK 1.98, GAZP 1.56, SIBN 2.75 hold. Not a general collapse.
- Small n per window (~15–50 trades) makes single-window PF noisy; the ≥3-of-4 criterion is the reliable signal.
- **Conclusion:** the levels-reversal edge is stable across market regimes (bull/bear/flat 2024–2026), not overfit to one period.

## 10. Case: ALRS paper #711 — entry inside a resistance zone (Issue #97)

**Decision:** guard in `StrategyEvaluator.check_entry`. An entry whose 1min close sits in an active resistance zone is rejected. Role-reversal ("the old support became resistance, so buying here is fine") is **not** a rule of `levels_reversal`. Locked DB row `test_20260731` is unchanged.

### Trade

| Field | Value |
|---|---|
| `paper_positions.id` | 711 |
| Ticker | ALRS |
| Strategy | `test_20260731` (`levels_reversal` + `signal_4h_buy`, RR 1:2, confirm 10min) |
| Entry | 2026-08-20 11:50:24 MSK @ **19.80** (market, 5 lots) |
| Signal | `trading.alerts.id = 80`, `signal_source=imbalance`, px 19.79, imbalance 1.40 |
| Stop / take | **19.61** / **20.90** |
| Exit | 2026-08-20 12:01:00, `closed_stop`, **−10.09 RUB (−1.02%)**, 11 minutes |
| Active 4h bar | 2026-08-20 08:00:00 (high 19.95; 4h BUY = `PA_Engulfing`) |

Reconstruction: `build_levels` on `trading.candles_aggregated`, timeframe=4h, `swing_window=10`, `zone_atr=0.5`, impulse 0.7 / 1.5×ATR. ATR of the active 4h bar ≈ 0.5714.

### The engine-named support is formally correct

Impulse **green** 4h from **2026-07-27 08:00**: O 19.61 / H 20.43 / L 19.61 / C 20.27 (body/range 0.80, body 2.09×ATR). Per `detect_impulse_levels` a green candle → `type=support`, level = open. The bar is not a swing high/low. Age at entry: **24 days**.

Support zone: **19.45–19.77**. Fill 19.80 is **outside** that zone. It only passes via the `zone_upper + 0.5×ATR` extension (to ~20.05) in `StrategyEvaluator.check_entry`.

### The price was owned by a fresher resistance

Nearest opposing level: impulse **resistance 19.67** from **2026-08-14 12:00** (open of a red 4h). Zone **19.40–19.94** covers fill 19.80. Distance to resistance **0.13** vs **0.19** to the chosen support.

| Level | Type | Method | Defined | Zone | Dist. from 19.80 |
|---|---|---|---|---|---|
| 19.61 | support | impulse | 2026-07-27 08:00 | 19.45–19.77 | 0.19 |
| 19.67 | resistance | impulse | 2026-08-14 12:00 | 19.40–19.94 | 0.13 |
| 20.90 | resistance | swing | 2026-07-06 08:00 | 20.76–21.04 | 1.10 (take) |

Broken swing supports 20.00 / 20.01 sit above the market (local ceiling). Intraday: high 19.95 at 11:30, entry on the pullback at 11:50, 12:00 bucket low 19.58 hit the stop.

### Why `check_entry` / `nearest_level_at` let it through

`nearest_level_at(..., 'support')` takes only `type=support` **below** price. Resistance below the market is never the stop and **does not veto** the entry, even when price is inside its zone. `nearest_level_at(..., 'resistance')` only searches **above** price, so 19.67 is skipped and take becomes 20.90. Old impulse levels are not invalidated by a newer opposite-side level nearby. The 0.5×ATR extension then prints a "support" entry that is structurally a buy into resistance.

Journal (`support_level=19.61`) and strategy meaning ("enter at support") diverged. For `levels_reversal`, trade 711 is an **incorrect entry relative to current structure**. The type of 19.61 is not the bug; the missing opposing-zone veto is.

### Rule going forward

Entry in an active resistance zone while the claimed level is support is a **defect**, not "the role flipped". Guard: `overlapping_resistance_zone_at` in `levels_engine.py`, called from `StrategyEvaluator.check_entry` after the support-zone (plus 0.5×ATR) check. Native resistance zone only — the 0.5×ATR extension is not mirrored on the veto. Fixture / unit: `backend/tests/test_resistance_zone_veto.py` (`regression_match: true` between `StrategyEvaluator` and `LevelsReversalStrategy` on this geometry).

## 11. State machine: breakout and role reversal (Issue #106)

Infrastructure for Epic #105. `levels_reversal` still does **not** treat a broken resistance as a new support for entries. This section describes the in-memory lifecycle so the next issue can add pattern `level_breakout_retest`.

### States

| State | Meaning |
|---|---|
| `active` | Zone is valid and not broken (current Issue #97 veto target). |
| `broken_up` | Confirmed close above a resistance zone. |
| `broken_down` | Confirmed close below a support zone. |
| `flipped_support` | Broken resistance held on the first retest close back inside the native zone. |
| `flipped_resistance` | Symmetric: broken support held on retest. |

Transitions: `active → broken_up/down` on the breakout rules below; `broken_up → flipped_support` and `broken_down → flipped_resistance` on the first subsequent close inside `[zone_lower, zone_upper]`. Failed breakouts are **not** reverted to `active` in this iteration.

### Breakout rules

Config: `LEVEL_STATE_MACHINE` in `trading_config.py` (do not hardcode). Defaults: `breakout_buffer_atr=0.25`, `confirm_bars=2`, `min_penetration_atr=0.5`, `zone_extension_atr=0.5`. Feed `LevelsTracker` bars of the same timeframe as the levels (typically 4h).

Resistance break (support is symmetric below `zone_lower`):
1. Last close `> zone_upper + breakout_buffer_atr × ATR`.
2. All of the last `confirm_bars` closes `> zone_upper`.
3. `max(window) >= zone_upper + min_penetration_atr × ATR`.

### Veto interaction

`overlapping_resistance_zone_at` skips rows whose `state` is not `active`. `build_levels` / `get_levels` do not add `state`. Issue #107 passes a `LevelsTracker` into the veto from `StrategyEvaluator` only when pattern `level_breakout_retest` is enabled (`is_broken(level_id)`). Locked `test_20260731` does not enable it, so default `check_entry` still vetoes every overlapping resistance.

Unit: `backend/tests/test_levels_state_machine.py`. Do not rewrite locked `test_20260731`.

## 12. Role reversal: resistance break + retest (Issue #107)

Configurable Lab pattern `level_breakout_retest`. It is an AND-filter after `levels_reversal` in `StrategyEvaluator`, not a replacement for it and not a SignalEngine inline-evaluate id.

### When it fires

1. `LevelsTracker` has confirmed a resistance break (`broken_up`) or the first hold (`flipped_support`).
2. Price has returned to the retest zone: `level_price ± retest_zone_atr × ATR` (default 0.5).
3. Support holds: 1min close ≥ the broken `level_price`.
4. Window: `bars_since_breakout` (HTF bars) ≤ `retest_window_bars` (default 20).
5. Trigger (when `entry_trigger_bullish=true`): close > previous 1min high, or bullish body (`body/range > 0.6` from `LEVEL_BREAKOUT_RETEST` in `trading_config.py`).

### Stop / take

`stop = entry − stop_atr × ATR` (default 1.0×ATR, below the retest zone on the default geometry). `take = entry + risk_reward × (entry − stop)` (default 2.0). These override levels stop/take when the pattern is enabled.

### Veto

A broken resistance is no longer an opposing zone. The ALRS 2026-08-20 fill at 19.80 is still rejected on locked `test_20260731` (pattern off). Enabling the pattern does not rewrite the locked DB row. Issue #108 (`analytics/issue-108-breakout-retest-validation/`) checks whether 19.94 was a confirmed `LevelsTracker` breakout before 11:50.

### Lab

`GET /api/patterns` exposes the schema (`category=breakout`). Frontend chip work is the next epic issue. File: `backend/app/analytics/patterns/level_breakout_retest.py`. Tests: `backend/tests/test_level_breakout_retest.py`.

## 13. Validation: `level_breakout_retest` (Issue #108)

Full report (EN+RU, plots): `analytics/issue-108-breakout-retest-validation/`.

- **A** = published Lab `test_20260820` (Issue #100): `levels_reversal` swing-only + `signal_4h_buy`, window `2024-08-21`…`2026-08-21`, 28 `get_big_tickers`. Locked `test_20260731` is not rewritten. Baseline matched Issue #100 **28/28** tickers (2556 trades, median PF **1.52**).
- **B** = A + `level_breakout_retest` (Lab defaults: 4h, window 20, zone 0.5×ATR, stop 1.0×ATR, RR 2.0): **257** trades, median PF **0.98**, pooled PF **1.07**. Too sparse and weaker than A.
- Walk-forward: A uses published #100 Lab half-years; B date-slices the FS trade list. 2/4 windows have OOS PF >20% below IS.
- ALRS 2026-08-20: **no** 4h close > 19.94 after the 2026-08-14 impulse level; `LevelsTracker` left 19.67 `active`; session high 19.95 at 11:40 is not a confirmed break. The #97 veto stays correct.
- Recommendation: **refine before Lab UI / paper**. Do not lock or paper-flag a Lab row from this issue.

