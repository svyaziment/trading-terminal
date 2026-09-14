# Issue #147 — Production trailing-stop parity: run protocol (v3)

## Purpose
Prove that the PRODUCTION code path (StrategyEvaluator + portfolio_simulator)
reproduces the #139 analytics book B on the ref139 grid, and that the production
default grid (ultra_late_tight from trading_config.TRAILING_STOP) reproduces the
#143 published numbers — at the level each contour can structurally reach.

## Books
| Book | Config | Gate |
|---|---|---|
| A_prod | trailing_stop absent | tight parity vs #139 book A |
| B_prod | trailing_stop.enabled=true, steps=ref139 (injected) | directional/scale vs #139 book B |
| B_default | trailing_stop.enabled=true, steps imported from trading_config.TRAILING_STOP | directional/scale vs #143 ultra_late_tight row |
| B_ref | analytics #139 summary.json / #143 summary.json (frozen) | reference only |

## Gate criteria (documented BEFORE the v3 rerun)
### Gate A (tight)
- final_equity_rub ±206.35 RUB (same as #143 contract.json anchor tolerance)
- n_trades delta 0; profit_factor ±0.05; win_rate ±0.5 pp; daily MaxDD ±0.5 pp
- exit counts exact under IDENTITY mapping (both sides use engine naming stop/take)
- entry set == #139 results.json entry set (0 new / 0 missing)
### Gate B (directional/scale, per Issue #147 acceptance bullet 2)
- equity > A_prod equity
- profit_factor in [1.45, 1.65]
- daily MaxDD <= 4.0 pp and |daily MaxDD - 2.74| <= 1.0 pp
- n_trades within ±20% of 3118
- initial_stop share >= 50% of closes
### Gate D (directional/scale vs #143 ultra_late_tight)
- equity > A_prod equity and within ±10% of 110 433.68 RUB
- |profit_factor - 1.60| <= 0.15
- n_trades within ±20% of 3162
- |daily MaxDD - 3.06| <= 1.0 pp
- exit split order: initial_stop > trailing > take

## Why B/D are not tight (structural, quantified in report.md)
#139/#143 are OVERLAY books: same 3305 entries, same managed paths, only the exit
rule differs (single-difference rule). The production ladder lives INSIDE the
StrategyEvaluator loop: an earlier trailing exit frees the ticker and the engine
takes re-entries the overlay structurally cannot contain. Therefore the ±206.35 RUB
overlay tolerance is unreachable for live-loop books by construction. Per-trade EXIT
mechanics parity is tight and already evidenced by #145 grid_check.json (0 mismatches
on 3305 trades, both grids, fixed paths). The entry-stream delta is quantified in
report.md (new/missing entries vs #139 results.json). Restating B/D criteria requires
TL/PO sign-off (draft comment in report.md); tolerances were NOT fitted to results.

## MaxDD convention
Compared daily-vs-daily: production daily curve recomputed from book trades exactly as
analytics/issue-139.../analysis.py daily_equity + max_drawdown_daily (close-day realized
equity, peak-trough). Event-based _portfolio_metrics MaxDD is reported as secondary.

## Exit-reason mapping
- Book A comparisons: IDENTITY (ref book A uses engine naming stop/take).
- Book B/D comparisons: prod `stop` -> analytics `initial_stop`; take/trailing as is.

## B_default steps source
Steps are IMPORTED from `trading_config.TRAILING_STOP` (single source of truth, no
hardcode). Contract #144 has NO steps fallback: `enabled=true` without usable steps
resolves to `trailing_disabled` and the book collapses to baseline (v2 defect).

## Cache and resume
Per-ticker cache under `cache/<book>/<TICKER>.json` (gitignored). Each book dir carries
`_fingerprint.json` = sha256 of the normalized book config; a fingerprint mismatch
invalidates the whole book cache automatically (fixes stale B_default cache from v2).

## How to run
    python analytics/issue-147-trailing-production-parity/run.py --stage all --workers 8
    python analytics/issue-147-trailing-production-parity/run.py --stage report

## Known expected differences (local production details)
- Lot/price rounding: production rounds to lots and price steps; #139 overlay does not.
  Contribution documented in RUB in report.md when material.
- Commission 0.06% round-trip and slippage 0 on both sides.
- Slot cascade: earlier exits reshuffle volume-priority allocation (skipped counts differ).
