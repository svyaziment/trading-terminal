"""Issue #145 cross-tree regression over REAL recorded price paths (SOP red line #3).

The paths are the committed cache of Issue #143
(`analytics/issue-143-trailing-robustness/cache/paths_<TICKER>.json.gz`): one record per trade
of the locked config id=126 with the sequence of 1-minute (high, low) bars the production brain
walked - 3 305 trades, 28 tickers, 2024-08-01..2026-08-21. That is real warehouse data,
captured from the DB by #143, so this harness needs no database and cannot write one.

What it proves: the exit branch of `StrategyEvaluator.on_bar`, replayed bar by bar in the tree
selected by `--backend`, must return exactly the same trade (price, reason, bars held, net
return) for every config that does not arm a ladder - block absent, `enabled=false`, an empty
ladder, and a ladder the Issue #144 validator refuses. The two armed ladders
(`ultra_late_tight`, `ref139`) may change the book only in the #145 tree.

    python path_regression.py --label baseline --backend <main worktree>/backend \
        --git-rev <rev> --out path_book_baseline.json
    python path_regression.py --label after --backend <this branch>/backend \
        --git-rev <rev> --out path_book_after.json
    python path_regression.py --compare path_book_baseline.json path_book_after.json \
        regression_verdict.json
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
ANALYTICS = os.path.join(ROOT, 'analytics', 'issue-143-trailing-robustness')

DEFAULT_LADDER = [{'trigger': 2.0, 'stop': 1.9}, {'trigger': 2.5, 'stop': 2.4},
                  {'trigger': 3.0, 'stop': 2.9}]
REF139_LADDER = [{'trigger': 2.0, 'stop': 1.5}, {'trigger': 2.5, 'stop': 2.0}]
NON_MONOTONE = [{'trigger': 2.0, 'stop': 1.9}, {'trigger': 2.5, 'stop': 1.2}]

VARIANTS = {
    'absent': None,
    'disabled': {'enabled': False, 'steps': DEFAULT_LADDER},
    'enabled_empty': {'enabled': True, 'steps': []},
    'invalid_non_monotone': {'enabled': True, 'steps': NON_MONOTONE},
    'ultra_late_tight': {'enabled': True, 'steps': DEFAULT_LADDER},
    'ref139': {'enabled': True, 'steps': REF139_LADDER},
}
MUST_MATCH = ['absent', 'disabled', 'enabled_empty', 'invalid_non_monotone']
ARMED = ['ultra_late_tight', 'ref139']
TRADE_KEYS = ('exit_price', 'exit_reason', 'bars_held', 'net_return_pct', 'step_reached')


def load_candidates(limit: int = 0) -> list:
    cache = os.path.join(ANALYTICS, 'cache')
    candidates = []
    for name in sorted(os.listdir(cache)):
        if not (name.startswith('paths_') and name.endswith('.json.gz')):
            continue
        with gzip.open(os.path.join(cache, name), 'rt', encoding='utf-8') as handle:
            candidates.extend(json.load(handle)['candidates'])
        if limit and len(candidates) >= limit:
            break
    return candidates[:limit] if limit else candidates


def bars_of(cand: dict) -> pd.DataFrame:
    """The entry bar (skipped by the walk) + the recorded managed bars."""
    entry_price = float(cand['entry_price'])
    ts0 = pd.Timestamp(cand['entry_ts'])
    rows = [{'timestamp': ts0, 'open': entry_price, 'high': entry_price,
             'low': entry_price, 'close': entry_price}]
    path_ts = cand.get('path_ts') or []
    for index, (high, low) in enumerate(cand['path']):
        ts = (pd.Timestamp(path_ts[index]) if index < len(path_ts)
              else ts0 + pd.Timedelta(minutes=index + 1))
        close = (float(high) + float(low)) / 2.0
        rows.append({'timestamp': ts, 'open': close, 'high': float(high),
                     'low': float(low), 'close': close})
    return pd.DataFrame(rows)


def config_for(block) -> dict:
    config = {'patterns': ['levels_sr_support'], 'commission_pct': 0.06,
              'slippage_pct': 0.0, 'entry_window': [0, 24]}
    if block is not None:
        config['trailing_stop'] = json.loads(json.dumps(block))
    return config


def open_position(evaluator, cand: dict, config: dict) -> None:
    """Put the evaluator in the state its own entry branch would have produced.

    `check_entry` is not under test (Issue #145 does not touch it) and the candidate already
    carries the entry price, the initial stop and the level take. The ladder is attached with
    the tree's own `trailing_from_config` - the very call `on_bar` makes at entry - and the
    pre-#145 tree simply has no such module, which is the difference being measured.
    """
    entry_exec = float(cand['entry_price'])
    evaluator.position = {'entry_ts': pd.Timestamp(cand['entry_ts']),
                          'entry_price': entry_exec, 'entry_exec': entry_exec,
                          'stop': float(cand['stop']), 'take': float(cand['take']),
                          'idx': 0, 'source': 'levels_sr_support'}
    try:
        from app.analytics.trailing_stop import trailing_from_config
    except ImportError:
        return
    state = trailing_from_config(config, entry_exec=entry_exec,
                                 initial_stop=float(cand['stop']), take=float(cand['take']))
    if state is not None:
        evaluator.position['trailing_state'] = state
        evaluator.position.update(state.snapshot())


def replay(cand: dict, block) -> dict:
    from app.analytics.strategy_engine import StrategyEvaluator

    config = config_for(block)
    evaluator = StrategyEvaluator(config)
    open_position(evaluator, cand, config)
    frame = bars_of(cand)
    for index in range(1, len(frame)):
        decision = evaluator.on_bar(frame.iloc[index], idx=index)
        if decision['action'] == 'exit':
            trade = decision['trade']
            return {key: trade.get(key) for key in TRADE_KEYS}
    return {'exit_price': None, 'exit_reason': 'open', 'bars_held': len(frame) - 1,
            'net_return_pct': None, 'step_reached': None}


def _mix(rows: list) -> dict:
    mix = {}
    for row in rows:
        key = str(row.get('exit_reason'))
        mix[key] = mix.get(key, 0) + 1
    return dict(sorted(mix.items()))


def collect(label: str, backend: str, out: str, git_rev: str, limit: int) -> int:
    sys.path.insert(0, os.path.abspath(backend))
    candidates = load_candidates(limit)
    # One frame per trade, reused by all six variants: the walk itself is what is compared.
    # The exit branch of on_bar reads row['timestamp'] / row['high'] / row['low'] only, so the
    # plain dict records of the same 1min bars are equivalent input (Series are covered by the
    # unit tests, tests/test_trailing_stop.py, which drive on_bar with real Series rows).
    prepared = [(cand, bars_of(cand).to_dict('records')) for cand in candidates]
    payload = {'label': label, 'git_rev': git_rev, 'backend': os.path.abspath(backend),
               'n_candidates': len(candidates), 'variants': {}}
    for variant, block in VARIANTS.items():
        rows = []
        for cand, records in prepared:
            rows.append({'id': cand['id'], **replay_rows(cand, records, block)})
        payload['variants'][variant] = rows
        print('%-22s rows=%-5d mix=%s'
              % (variant, len(rows), json.dumps(_mix(rows), ensure_ascii=False)), flush=True)
    with io.open(out, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False)
    print('wrote', out, flush=True)
    return 0


def replay_rows(cand: dict, records: list, block) -> dict:
    """Same walk as replay(), over pre-built bar records (see collect for the rationale)."""
    from app.analytics.strategy_engine import StrategyEvaluator

    config = config_for(block)
    evaluator = StrategyEvaluator(config)
    open_position(evaluator, cand, config)
    for index in range(1, len(records)):
        decision = evaluator.on_bar(records[index], idx=index)
        if decision['action'] == 'exit':
            trade = decision['trade']
            return {key: trade.get(key) for key in TRADE_KEYS}
    return {'exit_price': None, 'exit_reason': 'open', 'bars_held': len(records) - 1,
            'net_return_pct': None, 'step_reached': None}


def replay(cand: dict, block) -> dict:
    from app.analytics.strategy_engine import StrategyEvaluator

    config = config_for(block)
    evaluator = StrategyEvaluator(config)
    open_position(evaluator, cand, config)
    frame = bars_of(cand)
    for index in range(1, len(frame)):
        decision = evaluator.on_bar(frame.iloc[index], idx=index)
        if decision['action'] == 'exit':
            trade = decision['trade']
            return {key: trade.get(key) for key in TRADE_KEYS}
    return {'exit_price': None, 'exit_reason': 'open', 'bars_held': len(frame) - 1,
            'net_return_pct': None, 'step_reached': None}



def compare(baseline_path: str, after_path: str, verdict_path: str) -> int:
    """Bit-for-bit verdict across the two trees + the directed effect of the armed ladder."""
    with io.open(baseline_path, encoding='utf-8') as handle:
        base = json.load(handle)
    with io.open(after_path, encoding='utf-8') as handle:
        post = json.load(handle)
    checks, mismatches = [], []
    for variant in MUST_MATCH:
        inside = base['variants'][variant] == base['variants']['absent']
        inside_after = post['variants'][variant] == post['variants']['absent']
        across = base['variants'][variant] == post['variants'][variant]
        checks.append({'variant': variant,
                       'trades': base['variants'][variant],
                       'n_trades': len(base['variants'][variant]),
                       'exit_reason_mix': _mix(post['variants'][variant]),
                       'identical_inside_baseline_tree': inside,
                       'identical_inside_after_tree': inside_after,
                       'identical_baseline_vs_after': across})
        for flag, tree in ((inside, 'baseline'), (inside_after, 'after'), (across, 'cross')):
            if not flag:
                mismatches.append({'variant': variant, 'comparison': tree,
                                   'first_diff': _first_diff(base, post, variant)})
    armed = {}
    for variant in ARMED:
        changed = sum(1 for a, b in zip(post['variants'][variant], post['variants']['absent'])
                      if a != b)
        armed[variant] = {
            'trades': post['variants'][variant],
            'n_trades': len(post['variants'][variant]),
            'differs_from_no_ladder_in_after_tree': changed,
            'baseline_tree_ignores_the_block':
                base['variants'][variant] == base['variants']['absent'],
            'exit_reason_mix': _mix(post['variants'][variant]),
        }
        if armed[variant]['baseline_tree_ignores_the_block'] is False:
            mismatches.append({'variant': variant, 'comparison': 'baseline_reads_block'})
    verdict = {
        'issue': 145,
        'regression_match': not mismatches,
        'method': 'The exit branch of StrategyEvaluator.on_bar replayed bar by bar over the '
                  'real recorded 1-minute paths of the locked config id=126 (the #143 path '
                  'cache), once per git tree and once per trailing_stop variant.',
        'baseline_rev': base.get('git_rev'),
        'after_rev': post.get('git_rev'),
        'scope': {'trades': base['n_candidates'], 'period': '2024-08-01..2026-08-21',
                  'universe': '28 tickers of locked config 126',
                  'path_cache': 'analytics/issue-143-trailing-robustness/cache/',
                  'variants': sorted(VARIANTS)},
        'db_write_policy': 'no database was touched: the paths are the cache #143 committed, '
                           'so nothing here can write a trade, a result or a strategy row',
        'checks': [{k: v for k, v in c.items() if k != 'trades'} for c in checks],
        'armed_ladders': {k: {kk: vv for kk, vv in v.items() if kk != 'trades'}
                          for k, v in armed.items()},
        'mismatches': mismatches,
    }
    with io.open(verdict_path, 'w', encoding='utf-8') as handle:
        json.dump(verdict, handle, ensure_ascii=False, indent=2)
    print('regression_match =', verdict['regression_match'])
    for check in verdict['checks']:
        print('  %-22s %s' % (check['variant'], json.dumps(check, ensure_ascii=False)[:220]))
    for variant, data in armed.items():
        print('  %-22s %s' % (variant, json.dumps(
            {k: v for k, v in data.items() if k != 'trades'}, ensure_ascii=False)[:260]))
    if mismatches:
        print('MISMATCHES', json.dumps(mismatches, ensure_ascii=False)[:2000])
    return 0 if verdict['regression_match'] else 1


def _first_diff(base: dict, post: dict, variant: str):
    for key, rows_a, rows_b in (('baseline_vs_after', base['variants'][variant],
                                 post['variants'][variant]),
                                ('inside_baseline', base['variants'][variant],
                                 base['variants']['absent']),
                                ('inside_after', post['variants'][variant],
                                 post['variants']['absent'])):
        for left, right in zip(rows_a, rows_b):
            if left != right:
                return {'comparison': key, 'baseline_row': left, 'other_row': right}
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default='after')
    parser.add_argument('--backend', default=os.path.join(ROOT, 'backend'))
    parser.add_argument('--git-rev', default='')
    parser.add_argument('--out')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--compare', nargs=3,
                        metavar=('BASELINE', 'AFTER', 'VERDICT'))
    args = parser.parse_args()
    if args.compare:
        return compare(*args.compare)
    return collect(args.label, args.backend, args.out, args.git_rev, args.limit)


if __name__ == '__main__':
    raise SystemExit(main())


