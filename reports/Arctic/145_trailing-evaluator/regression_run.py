"""Issue #145 regression harness - baseline (origin/main) vs after, trailing disabled.

Red line #3 of the agents SOP: a `critical` task ships only with a bit-for-bit replay of the
existing book. This script runs the production backtest path (`run_strategy_backtest` ->
`StrategyEvaluator.on_bar`) over the LOCKED strategy configs stored in the database and
dumps the resulting trade lists, so running the same script from two git trees proves that
"arming nothing" changes nothing.

Read-only: it SELECTs strategy configs and candles and never writes a row (no trades, no
backtest_results, no strategies). Credentials come from .env; only the DB host is overridable
because the app default (`postgres`) resolves only inside the compose network.

Usage (from either git tree):
    python regression_run.py --label after --backend <path>/backend --out after.json
    python regression_run.py --compare baseline.json after.json --verdict regression_verdict.json
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import subprocess
import sys
import time

TRADE_KEYS = ('entry_ts', 'exit_ts', 'entry_price', 'exit_price', 'exit_reason',
              'bars_held', 'net_return_pct')

# Ladder variants. Everything but the two armed ladders must reproduce the baseline book.
DEFAULT_LADDER = [{'trigger': 2.0, 'stop': 1.9}, {'trigger': 2.5, 'stop': 2.4},
                  {'trigger': 3.0, 'stop': 2.9}]      # ultra_late_tight - the PO default
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


def load_env(root: str) -> dict:
    """Read .env (repo root) the way the container environment does."""
    values = {}
    path = os.path.join(root, '.env')
    if os.path.exists(path):
        for line in io.open(path, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return values


def _git_rev(backend: str) -> str:
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'],
                              capture_output=True, cwd=backend, timeout=30
                              ).stdout.decode().strip()
    except Exception:
        return 'unknown'


def _trades_of(result) -> list:
    """Canonical trade rows for the diff (step_reached kept separate: it is #145-only)."""
    rows = []
    for trade in result.get('trades', []):
        row = {key: trade.get(key) for key in TRADE_KEYS}
        row['step_reached'] = trade.get('step_reached')
        rows.append(row)
    return rows


def _load_1m(db, pd, ticker: str, date_from, date_to):
    """The candle query of run_strategy_backtest, so one ticker can feed all variants."""
    query = ('SELECT timestamp, open, high, low, close FROM trading.candles_1min_raw '
             'WHERE ticker=%s ')
    params = [ticker]
    if date_from is not None:
        query += ' AND timestamp >= %s '
        params.append(date_from)
    if date_to is not None:
        query += ' AND timestamp < %s '
        params.append(date_to)
    query += ' ORDER BY timestamp '
    frame = db.select(query, tuple(params)).to_dataframe()
    if frame.empty:
        return None
    for column in ('open', 'high', 'low', 'close'):
        frame[column] = pd.to_numeric(frame[column], errors='coerce')
    return frame


def _indicators(config: dict, frame, pd):
    """Mirror run_strategy_backtest's indicator pass (RSI/MACD/BB are AND-filters)."""
    patterns = config.get('patterns') or []
    keys = list(patterns)
    if not any(k in keys for k in ('rsi_oversold', 'macd_bullish', 'bb_lower')):
        return frame
    from app.analytics.strategy_backtest import compute_indicators_1min

    return compute_indicators_1min(frame)


def _replay(ctx, frame):
    """The bar loop of run_strategy_backtest over a prebuilt context (same evaluator)."""
    from app.analytics.strategy_engine import StrategyEvaluator

    evaluator = StrategyEvaluator(ctx['config'])
    evaluator.load_context(levels=ctx['levels'], ts_4h=ctx['ts_htf'],
                           atr_by_ts=ctx['atr_by_ts'], buy_ts=ctx['buy_ts'],
                           confirm_series=ctx['confirm_series'],
                           signal_filter_series=ctx.get('signal_filter_series'),
                           htf_bars=ctx.get('htf_bars'))
    trades = []
    for index in range(len(frame)):
        decision = evaluator.on_bar(frame.iloc[index], idx=index)
        if decision['action'] == 'exit':
            trades.append(decision['trade'])
    return _trades_of({'trades': trades})


def collect(label: str, backend: str, strategy_ids: list, max_tickers: int,
            tickers: list, date_from: str, date_to: str, db_host: str,
            cross_check: int = 1) -> dict:
    """Run every variant over the locked config and return the books as plain JSON.

    One 4h context per ticker feeds all six variants (`trailing_stop` does not touch it), and
    the first `cross_check` tickers are replayed through the untouched
    `run_strategy_backtest` as well - if the shared-context path differed from the production
    function by a single trade, the verdict records it as a mismatch.
    """
    # .env lives in the checkout this script was started from (it is git-ignored, so a second
    # worktree has none); the backend path only selects which code the imports resolve to.
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    env = load_env(repo_root)
    for key in ('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_PORT'):
        if env.get(key):
            os.environ.setdefault(key, env[key])
    os.environ['POSTGRES_HOST'] = db_host
    sys.path.insert(0, backend)

    import pandas as pd

    from app.analytics.strategy_backtest import run_strategy_backtest
    from app.analytics.strategy_context import build_strategy_context
    from app.db.db_manager import DBManager

    db = DBManager()
    payload = {'label': label, 'git_rev': _git_rev(backend),
               'python': sys.version.split()[0], 'strategies': {},
               'cross_check': {'tickers': [], 'mismatches': []}}
    try:
        for sid in strategy_ids:
            row = db.select('SELECT name, config FROM trading.strategies WHERE id=%s',
                            (sid,)).to_dataframe()
            if row.empty:
                raise SystemExit('strategy id %s not found' % sid)
            raw = row.iloc[0]['config']
            config = ast.literal_eval(raw) if isinstance(raw, str) else dict(raw or {})
            name = str(row.iloc[0]['name'])
            params = config.get('run_params') or {}
            scope_tickers = tickers or list(params.get('tickers') or [])
            if max_tickers:
                scope_tickers = scope_tickers[:max_tickers]
            scope_from = date_from or params.get('date_from')
            scope_to = date_to or params.get('date_to')
            book = {'name': name, 'tickers': scope_tickers,
                    'date_from': str(scope_from), 'date_to': str(scope_to),
                    'config_sha256': hashlib_sha(config), 'variants': {}, 'elapsed_s': {}}
            for variant in VARIANTS:
                book['variants'][variant] = {'n_trades': 0, 'trades': [],
                                             'failed_tickers': []}
            print('[%s] strategy %s/%s tickers=%d window=%s..%s'
                  % (label, sid, name, len(scope_tickers), scope_from, scope_to), flush=True)
            started = time.time()
            per_ticker = {variant: {} for variant in VARIANTS}
            for position, ticker in enumerate(scope_tickers, 1):
                frame = _load_1m(db, pd, ticker, scope_from, scope_to)
                if frame is not None:
                    frame = _indicators(config, frame, pd)
                if frame is None:
                    for variant in VARIANTS:
                        book['variants'][variant]['failed_tickers'].append(
                            {'ticker': ticker, 'error': 'no 1min candles'})
                    continue
                ctx_base = build_strategy_context(db, ticker, dict(config), df_1m=frame)
                if ctx_base.get('status') == 'failed':
                    for variant in VARIANTS:
                        book['variants'][variant]['failed_tickers'].append(
                            {'ticker': ticker, 'error': ctx_base.get('error')})
                    continue
                for variant, block in VARIANTS.items():
                    cfg = dict(ctx_base['config'])
                    if block is None:
                        cfg.pop('trailing_stop', None)
                    else:
                        cfg['trailing_stop'] = json.loads(json.dumps(block))
                    ctx = dict(ctx_base)
                    ctx['config'] = cfg
                    per_ticker[variant][ticker] = _replay(ctx, frame)
                mine = per_ticker['absent'][ticker]
                if position <= cross_check:
                    reference = run_strategy_backtest(db, ticker, dict(config),
                                                      date_from=scope_from, date_to=scope_to)
                    direct = (_trades_of(reference)
                              if reference.get('status') == 'success' else None)
                    payload['cross_check']['tickers'].append(
                        {'strategy_id': sid, 'ticker': ticker,
                         'run_strategy_backtest_n': 0 if direct is None else len(direct),
                         'shared_context_n': len(mine), 'identical': direct == mine})
                    if direct != mine:
                        payload['cross_check']['mismatches'].append('%s:%s' % (sid, ticker))
                print('  %2d/%d %-6s trades=%d'
                      % (position, len(scope_tickers), ticker, len(mine)), flush=True)
            for variant in VARIANTS:
                rows = [row for tk in scope_tickers for row in per_ticker[variant].get(tk, [])]
                book['variants'][variant]['trades'] = rows
                book['variants'][variant]['n_trades'] = len(rows)
            book['elapsed_s']['total'] = round(time.time() - started, 1)
            print('  %s/%s done in %ss %s'
                  % (sid, name, book['elapsed_s']['total'],
                     {v: book['variants'][v]['n_trades'] for v in VARIANTS}), flush=True)
            payload['strategies'][str(sid)] = book
    finally:
        db.close_pool()
    return payload


def hashlib_sha(config: dict) -> str:
    import hashlib
    blob = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]


def _reason_mix(trades: list) -> dict:
    mix = {}
    for trade in trades:
        mix[str(trade.get('exit_reason'))] = mix.get(str(trade.get('exit_reason')), 0) + 1
    return dict(sorted(mix.items()))


def compare(baseline: dict, after: dict) -> dict:
    """Bit-for-bit verdict across the two trees + the directed effect of the live ladder."""
    checks, mismatches = [], []
    for sid, base in sorted(baseline['strategies'].items()):
        post = after['strategies'].get(sid)
        if post is None:
            mismatches.append({'strategy_id': sid, 'reason': 'missing in the after run'})
            continue
        # 1. Inside each tree, a variant that must not arm has to equal that tree's book.
        for tree_label, tree in (('baseline', base), ('after', post)):
            for variant in MUST_MATCH:
                if tree['variants'][variant]['trades'] != tree['variants']['absent']['trades']:
                    mismatches.append({'strategy_id': sid, 'tree': tree_label,
                                       'variant': variant,
                                       'reason': 'differs from the same tree without a ladder'})
        # 2. Across the trees, the non-arming books must be identical trade for trade.
        for variant in MUST_MATCH:
            mine = base['variants'][variant]['trades']
            theirs = post['variants'][variant]['trades']
            checks.append({'strategy_id': sid, 'strategy': base['name'], 'variant': variant,
                           'n_trades': len(mine), 'match': mine == theirs,
                           'exit_reason_mix': _reason_mix(mine)})
            if mine != theirs:
                first = next((i for i, (a, b) in enumerate(zip(mine, theirs)) if a != b), None)
                mismatches.append({'strategy_id': sid, 'variant': variant,
                                   'first_diff_index': first,
                                   'reason': 'baseline trade list differs from the after one'})
        # 3. Directed check: the ladder changes the book only in the after tree.
        for variant in ARMED:
            base_same = base['variants'][variant]['trades'] == base['variants']['absent']['trades']
            after_diff = post['variants'][variant]['trades'] != post['variants']['absent']['trades']
            armed = post['variants'][variant]
            checks.append({
                'strategy_id': sid, 'strategy': base['name'],
                'variant': '%s (feature effect)' % variant,
                'n_trades': armed['n_trades'],
                'baseline_ignores_the_block': base_same,
                'after_replays_differently': after_diff,
                'exit_reason_mix': _reason_mix(armed['trades']),
            })
            if not base_same:
                mismatches.append({'strategy_id': sid, 'variant': variant,
                                   'reason': 'the baseline tree must not read the block'})
    for tree_label, tree in (('baseline', baseline), ('after', after)):
        for bad in (tree.get('cross_check') or {}).get('mismatches', []):
            mismatches.append({'tree': tree_label, 'variant': 'shared_context_replay',
                               'ticker': bad,
                               'reason': 'the harness replay differs from '
                                         'run_strategy_backtest'})
    return {
        'issue': 145,
        'regression_match': not mismatches,
        'baseline_rev': baseline.get('git_rev'),
        'after_rev': after.get('git_rev'),
        'python': after.get('python'),
        'shared_context_cross_check': {
            label: {'tickers': (tree.get('cross_check') or {}).get('tickers', []),
                    'mismatches': (tree.get('cross_check') or {}).get('mismatches', [])}
            for label, tree in (('baseline', baseline), ('after', after))},
        'scope': {sid: {'strategy': book['name'], 'config_sha256': book['config_sha256'],
                        'n_tickers': len(book['tickers']), 'tickers': book['tickers'],
                        'date_from': book['date_from'], 'date_to': book['date_to'],
                        'elapsed_s': book['elapsed_s']}
                  for sid, book in sorted(baseline['strategies'].items())},
        'checks': checks,
        'mismatches': mismatches,
        'db_write_policy': 'read-only - this harness only SELECTs; no trade or result rows '
                           'were created, updated or deleted',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Issue #145 trailing-stop regression harness')
    parser.add_argument('--label', default='after')
    parser.add_argument('--backend', default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))), 'backend'))
    parser.add_argument('--out')
    parser.add_argument('--strategy', type=int, action='append', default=[])
    parser.add_argument('--max-tickers', type=int, default=0)
    parser.add_argument('--tickers', default='')
    parser.add_argument('--date-from', default='')
    parser.add_argument('--date-to', default='')
    parser.add_argument('--db-host', default='127.0.0.1')
    parser.add_argument('--cross-check', type=int, default=1,
                        help='tickers per strategy also replayed through run_strategy_backtest')
    parser.add_argument('--compare', nargs=2)
    parser.add_argument('--verdict')
    args = parser.parse_args()

    if args.compare:
        with io.open(args.compare[0], encoding='utf-8') as handle:
            baseline = json.load(handle)
        with io.open(args.compare[1], encoding='utf-8') as handle:
            after = json.load(handle)
        verdict = compare(baseline, after)
        text = json.dumps(verdict, ensure_ascii=False, indent=2)
        if args.verdict:
            io.open(args.verdict, 'w', encoding='utf-8').write(text + os.linesep)
        print(text[:6000])
        print('regression_match =', verdict['regression_match'])
        return 0 if verdict['regression_match'] else 1

    ids = args.strategy or [126, 36]
    payload = collect(args.label, os.path.abspath(args.backend), ids, args.max_tickers,
                      [t for t in args.tickers.split(',') if t],
                      args.date_from, args.date_to, args.db_host, args.cross_check)
    if args.out:
        with io.open(args.out, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False)
        print('wrote', args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


