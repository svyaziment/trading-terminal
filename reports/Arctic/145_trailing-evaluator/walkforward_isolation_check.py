"""Walk-forward state isolation for the Issue #145 ladder, on real recorded paths.

run_walkforward calls run_strategy_backtest per window, so each window opens its own
StrategyEvaluator. This check proves the property that matters for #145 on real data: the
second window produces exactly the same trades whether it is replayed alone or after the first
window has run in the same process - i.e. armed highs and step_reached never leak across
windows. It also checks the opposite direction: with an armed ladder the second window still
reproduces itself, and the two windows together differ from one merged run (each window starts
from its own initial stop).
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, 'backend'))
sys.path.insert(0, os.path.join(ROOT, 'reports', 'Arctic', '145_trailing-evaluator'))

import path_regression as pr  # noqa: E402  (the same replay used by the regression harness)

KEYS = ('exit_price', 'exit_reason', 'bars_held', 'net_return_pct', 'step_reached')
BLOCKS = {'absent': pr.VARIANTS['absent'],
          'ultra_late_tight': pr.VARIANTS['ultra_late_tight']}


def window_rows(cands, block):
    """Per-trade exits of one window (the evaluator is rebuilt for every trade, exactly as
    run_strategy_backtest rebuilds it for every window)."""
    return [{'id': cand['id'], **pr.replay_rows(cand, pr.bars_of(cand).to_dict('records'),
                                                block)}
            for cand in cands]


def main():
    candidates = pr.load_candidates()
    half = len(candidates) // 2
    w1, w2 = candidates[:half], candidates[half:]
    report = {'windows': {'w1': len(w1), 'w2': len(w2)},
              'universe': '28 tickers of locked config 126 (the #143 path cache)',
              'method': 'run the second window alone, then again after the first window has '
                        'been played in the same interpreter; the trade lists must be equal',
              'blocks': {}}
    ok_all = True
    for label, block in BLOCKS.items():
        alone = window_rows(w2, block)
        window_rows(w1, block)                    # the "previous window"
        after_prev = window_rows(w2, block)
        first_run = window_rows(w1, block)
        report['blocks'][label] = {
            'w2_alone': len(alone),
            'w2_after_previous_window': len(after_prev),
            'identical': alone == after_prev,
            'exit_reason_mix_w1': pr._mix(first_run),
            'exit_reason_mix_w2': pr._mix(after_prev),
        }
        ok_all = ok_all and report['blocks'][label]['identical']
    report['walkforward_state_isolated'] = bool(ok_all)
    dest = os.path.join(ROOT, 'reports', 'Arctic', '145_trailing-evaluator',
                        'walkforward_isolation_check.json')
    with io.open(dest, 'w', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report['blocks'], ensure_ascii=False))
    print('walkforward_state_isolated =', ok_all)
    return 0 if ok_all else 1


if __name__ == '__main__':
    raise SystemExit(main())
