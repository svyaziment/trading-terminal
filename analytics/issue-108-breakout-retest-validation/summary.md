# Issue #108 summary

**Recommendation: refine before Lab UI / paper**

A (`test_20260820` / Issue #100): trades=2556, median PF=1.52, pooled PF=1.50.
B (`+ level_breakout_retest`): trades=257, median PF=0.98, pooled PF=1.07.

Issue #100 match: 28/28 tickers, bit_for_bit=True.
ALRS confirmed breakout before 11:50: False.

## Why

- Median PF fell from 1.52 (A) to 0.98 (B).
- 2/4 walk-forward windows show OOS PF >20% below IS (defaults frozen, not a tuned grid).
- ALRS 19.94 was not a confirmed LevelsTracker breakout before 11:50; the #97 veto remains the correct decision on that bar.

## Files

- `full_sample_comparison.md` / `.ru.md`
- `walk_forward_results.md` / `.ru.md`
- `alrs_case_study.md` / `.ru.md`
- `rejection_analysis.md` / `.ru.md`
- `plots/`
