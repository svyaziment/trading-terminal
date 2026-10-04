# Operating the Live Trading Universe

> **Source:** handover.md sections 18, 31, 32
> **Last refreshed:** 2026-10-04, task-346

## 18. Operating the Live Trading Universe

- Paper ranking stays `get_trading_universe()` (top-15 from `trading.trading_universe`). Do not shrink that table.
- Streaming and data refresh use `get_streaming_universe()` = top-15 ∪ `LIVE_UNIVERSE`.
- Sandbox execution uses `LIVE_UNIVERSE` / `get_live_trading_universe()`: ROSN, IRAO, AFKS, NVTK, SBER, MTSS, PHOR, MOEX, FLOT, FEES, GAZP, PLZL (Issue #135 PO list plus 2026-09-02). The getter does **not** clip names that sit outside the paper top-15. `LiveExecutor.initialize()` intersects paper-strategy tickers with this list.
- Historical Issue #66 ranking (SBER, LKOH, RUAL, NVTK, GAZP) lives in `analytics/issue-66-live-universe/`. Do not rewrite that package to match the current PO list.
- Locked paper name for preflight is `EXPECTED_LOCKED_STRATEGY` = `test_20260830_new_level`.
- Tests: `cd backend && python -m pytest -q tests/test_trading_config.py tests/test_live_universe_analysis.py tests/test_live_executor.py`.

## 31. Operating the isolated support-with-tracker universe

- Package: `analytics/issue-129-sr-support-universe/`. Isolated 28-ticker Lab universe (`get_big_tickers`), same period as #124. Not live top-5 and not `run_params.tickers`.
- C = only `levels_sr_support` + `signal_4h_buy` (SHA `3b7864c4de2cb2c7d271be8c21c7d99c29bfd8a7dd05980b3c5497b6b2aedb1b`). Engine is `run_strategy_backtest` so trades keep `source=levels_sr_support`.
- Exclusive B-support in #124 is a **composite label** (path B steals dual bars and occupies the single slot). Isolated C is the **runnable** support-only book: n=4380 PF 1.45. Do not treat exclusive 3811 / 1.51 as bit-for-bit C.
- Extra 611 vs exclusive: 610 occupancy (C enters while the composite is in a path-B or other trade), leftover 1 (PHOR `2026-08-14 14:48`). Missing 42: cascade (a C extra occupies the slot so a later B-support trade cannot fire). Extra PF 0.95 — isolated extras are worse than exclusive 1.51.
- AFKS: C 89 / 1.49; exclusive 78 ⊆ C; not mix 116 / 1.46. ALRS `2026-08-20 11:50:24` @ 19.80 blocked. Resistance-source n=0.
- Issue #130 must use C (4380 / 1.45), not exclusive 3811 / 1.51. Do not mix isolated PF with a 50k portfolio. Portfolio package: handover §32.
- Do not lock/paper-flag or overwrite `test_20260731`, `test_20260820`, `test_20260821`.
- Replay without a new backtest: `python analytics/issue-129-sr-support-universe/analysis.py`. Full re-run: `python analytics/issue-129-sr-support-universe/extract_inputs.py` (resumable).
- Units: `cd backend && python -m pytest -q tests/test_issue129_analysis.py`.

## 32. Operating the support-with-tracker portfolio

- Package: `analytics/issue-130-sr-support-portfolio/`. Slot replay of isolated C from #129, same 28 names / volume-order as #103/#44. Not live top-5.
- C = only `levels_sr_support` + `signal_4h_buy` (SHA `3b7864c4de2cb2c7d271be8c21c7d99c29bfd8a7dd05980b3c5497b6b2aedb1b`). Candidates come from published #129 `results.json` via `run_strategy_backtest` (`source=levels_sr_support`). Do not filter #124 B-mix by `source`.
- Slots: 50,000 RUB / 10,000 / max 5. Period `2024-08-01` … `timestamp < 2026-08-21`. Daily equity is realized closes, no mark-to-market.
- Published C book: n=3237 PF 1.33 equity 96,204.63 daily Max DD 6.08% event Max DD 6.98% skipped 1143 no GAME OVER. Isolated C remains 4380 / 1.45 — do not mix those PF numbers.
- Comparison (other books, not substitutes): #44 equity 96,343.49 n=3500 PF 1.31; #103 equity 89,055.31 n=2070 PF 1.34; #124 B-mix equity 98,432.94 n=2837 PF 1.32 (support+resistance candidates).
- ALRS `2026-08-20 11:50:24` @ 19.80 is absent from candidates and portfolio entries. Resistance-source n=0.
- Verdict: not paper (default without an explicit PO decision). Do not lock/paper-flag or overwrite `test_20260731`, `test_20260820`, `test_20260821`. Lab draft if needed: `test_YYYYMMDD_sr_support`.
- Replay without a new backtest: `python analytics/issue-130-sr-support-portfolio/analysis.py`. Slot JSON: `python analytics/issue-130-sr-support-portfolio/generate_inputs.py --source 129`. Notebook: `python analytics/issue-130-sr-support-portfolio/build_notebook.py --execute`.
- Units: `cd backend && python -m pytest -q tests/test_issue130_analysis.py`.
