"""Issue #145 - directed check of the PRODUCTION ladder against the published #143 book.

What it proves, in two layers:

1. per trade - `app.analytics.trailing_stop` (the code that now runs in StrategyEvaluator,
   the levels_reversal plugin and portfolio_simulator) reproduces, trade by trade, the exits
   Issue #139/#143 recorded in `analytics/issue-143-trailing-robustness/exits.jsonl.gz` for
   BOTH grids that matter: the shipped default `ultra_late_tight` and the parity anchor
   `ref139`. Reason names are mapped (`stop` -> `initial_stop`) because production keeps the
   pre-#145 name for an exit whose stop was never raised; the mapping itself is Issue #147's
   formal gate.
2. per book - the production portfolio contour (`_replay_portfolio_trades` +
   `_portfolio_metrics`, 50k / 10k slot / 5 positions, volume priority) replays those trades
   and must land on the published #143 numbers for `ultra_late_tight`
   (110 433.68 RUB / 3 162 trades / PF 1.60 / daily MaxDD 3.06 pp / 1 790-1 296-76) and
   `ref139` (103 216.04 / 3 118 / 1.55 / 2.72 / 1 762-1 151-205).

Offline by construction: it reads the per-ticker path caches #143 committed
(`analytics/issue-143-trailing-robustness/cache/paths_<TICKER>.json.gz`) and the #139 inputs,
so it touches no database and cannot move the published figures.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
from collections import Counter

# The script is a report artifact: it runs the CURRENT checkout's production code, and
# TRAILING_145_BACKEND lets a reviewer point it at another git tree for a before/after run.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
BACKEND = os.environ.get('TRAILING_145_BACKEND') or os.path.join(_ROOT, 'backend')
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.analytics.portfolio_simulator import (  # noqa: E402  (path bootstrap above)
    _portfolio_metrics,
    _replay_portfolio_trades,
)
from app.analytics.trailing_stop import TrailingState  # noqa: E402

EXIT_REASON_MAP = {'stop': 'initial_stop', 'trailing': 'trailing', 'take': 'take'}
PRICE_TOL = 5.05e-5    # the #143 publication quantum: exit prices rounded to 4 decimals
PCT_TOL = 1e-4         # net_return_pct recomputed under that same convention must match
EQUITY_TOL = 5.0       # RUB; #143 rounds equity points to 2 decimals
COUNT_TOL = 0          # the trade count must match exactly
SHARE_TOL = 0.05       # profit factor / win rate / drawdown tolerance in published units


def _load_grids(analytics_dir: str) -> dict:
    with io.open(os.path.join(analytics_dir, 'grids.json'), encoding='utf-8') as handle:
        payload = json.load(handle)
    return {row['id']: row for row in payload['grids']}


def _load_exits(analytics_dir: str) -> dict:
    """{grid_id: {trade_id: exit row}} from the published #143 per-trade book."""
    path = os.path.join(analytics_dir, 'exits.jsonl.gz')
    books = {}
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            books.setdefault(row['grid_id'], {})[row['id']] = row
    return books


def _load_paths(cache_dir: str, tickers: list) -> list:
    candidates = []
    for ticker in tickers:
        path = os.path.join(cache_dir, 'paths_%s.json.gz' % ticker)
        if not os.path.exists(path):
            raise SystemExit('missing path cache for %s - run the #143 extract first' % ticker)
        with gzip.open(path, 'rt', encoding='utf-8') as handle:
            payload = json.load(handle)
        candidates.extend(payload['candidates'])
    return candidates


def production_exit(cand: dict, steps: list, commission_pct: float) -> dict:
    """The production ratchet over one recorded path - the same call on_bar makes."""
    entry = float(cand['entry_price'])
    state = TrailingState.build(steps, entry_exec=entry, initial_stop=float(cand['stop']),
                                take=float(cand['take']))
    if state is None:
        raise AssertionError('grid ladder unusable for %s' % cand['id'])
    path = cand['path']
    for index, (high, low) in enumerate(path):
        decision = state.evaluate(high=high, low=low, bar_key=index)
        if decision.exits:
            return _row(decision.exit_price, decision.exit_reason, index,
                        decision.step_reached, entry, commission_pct)
    last_high = float(path[-1][0]) if path else entry
    return _row(last_high, 'open', max(len(path) - 1, 0), state.pending_reached,
                entry, commission_pct)


def _row(price, reason, index, reached, entry, commission_pct) -> dict:
    """Exit as the engine computes it, plus the #143 convention (4-decimal price).

    Issue #143 published `exit_price` rounded to 4 decimals and took `net_return_pct` from
    that rounded number. Production keeps full precision, so on sub-kopeck tickers (FEES)
    the two return figures differ by the rounding quantum - the exit itself is identical.
    """
    raw = float(price)
    published_style = round(raw, 4)
    return {'exit_price': round(raw, 10), 'exit_price_143': published_style,
            'exit_reason': reason, 'exit_index': index, 'step_reached': float(reached),
            'net_return_pct': round((raw / entry - 1.0) * 100.0 - float(commission_pct), 5),
            'net_return_pct_143': round((published_style / entry - 1.0) * 100.0
                                        - float(commission_pct), 5)}


def _daily_max_drawdown(equity_curve: list) -> float:
    """Daily MaxDD of the replayed equity - the definition the analytics books publish.

    `portfolio_simulator._portfolio_metrics` reports the EVENT-based drawdown (every settle
    point). The published #139/#143 books publish the DAILY one, exactly like the other
    portfolio analyses in this repo (issue-130: daily 6.08 vs event 6.98). Comparing the two
    definitions against each other is what made the first version of this check red.
    """
    import pandas as pd

    if not equity_curve:
        return 0.0
    series = pd.Series({pd.Timestamp(point['ts']): float(point['equity_rub'])
                        for point in equity_curve}).sort_index()
    daily = series.resample('D').last().dropna()
    if daily.empty:
        return 0.0
    peak = daily.cummax()
    drawdown = (peak - daily) / peak * 100.0
    return round(float(drawdown.max()), 2)


def check_grid(grid_id: str, steps: list, candidates: list, published_exits: dict,
               replay, commission_pct: float) -> dict:
    """Layer 1 (per trade) + layer 2 (per book) for one grid.

    Two books are replayed: one with production full precision, one re-rounded to the #143
    publication convention. If only the second one lands on the published figures, the exit
    logic is proven identical and the residual difference is a rounding convention.
    """
    book_raw, book_143 = [], []
    diffs, compared = [], 0
    rounding_only = 0
    max_price_delta = max_net_delta = 0.0
    for cand in candidates:
        got = production_exit(cand, steps, commission_pct)
        want = published_exits.get(cand['id'])
        if want is not None:
            compared += 1
            price_delta = abs(got['exit_price'] - float(want['exit_price']))
            net_delta = abs(got['net_return_pct'] - float(want['net_return_pct']))
            same_conv_delta = abs(got['net_return_pct_143'] - float(want['net_return_pct']))
            max_price_delta = max(max_price_delta, price_delta)
            max_net_delta = max(max_net_delta, net_delta)
            if (EXIT_REASON_MAP.get(got['exit_reason'], got['exit_reason'])
                    != want['exit_reason']
                    or got['exit_index'] != int(want['exit_index'])
                    or abs(got['step_reached'] - float(want['step_reached'])) > 1e-9):
                diffs.append({'id': cand['id'], 'field': 'exit mechanics',
                              'production': [got['exit_index'], got['exit_reason'],
                                             got['step_reached']],
                              'published_143': [want['exit_index'], want['exit_reason'],
                                                want['step_reached']]})
            if price_delta > PRICE_TOL:
                diffs.append({'id': cand['id'], 'field': 'exit_price',
                              'production': got['exit_price'],
                              'published_143': want['exit_price'],
                              'delta': price_delta})
            elif same_conv_delta > PCT_TOL:
                diffs.append({'id': cand['id'], 'field': 'net_return_pct',
                              'production': got['net_return_pct_143'],
                              'published_143': want['net_return_pct'],
                              'delta': same_conv_delta})
            elif net_delta > PCT_TOL:
                rounding_only += 1
        index = got['exit_index']
        path_ts = cand.get('path_ts') or []
        exit_ts = path_ts[index] if index < len(path_ts) else cand['entry_ts']
        common = {'id': cand['id'], 'ticker': cand['ticker'], 'entry_ts': cand['entry_ts'],
                  'exit_ts': exit_ts, 'entry_price': cand['entry_price'],
                  'exit_reason': got['exit_reason'], 'step_reached': got['step_reached'],
                  'bars_held': index + 1}
        book_raw.append(dict(common, exit_price=got['exit_price'],
                             net_return_pct=got['net_return_pct']))
        book_143.append(dict(common, exit_price=got['exit_price_143'],
                             net_return_pct=got['net_return_pct_143']))

    def _book(result):
        counts = Counter(EXIT_REASON_MAP.get(t['exit_reason'], t['exit_reason'])
                         for t in result['trades'])
        metrics = result['metrics']
        return {'n_trades': metrics.get('n_trades'),
                'final_equity_rub': metrics.get('final_equity_rub'),
                'pnl_rub': metrics.get('pnl_rub'),
                'profit_factor': metrics.get('profit_factor'),
                'win_rate_pct': metrics.get('win_rate'),
                'max_drawdown_event_pct': metrics.get('max_drawdown_pct'),
                'max_drawdown_daily_pct': _daily_max_drawdown(result.get('equity_curve') or []),
                'skipped_entries_no_slot': result['skipped_entries_no_slot'],
                'game_over': result['game_over'],
                'exit_type_counts': dict(sorted(counts.items()))}

    return {
        'grid_id': grid_id,
        'steps': steps,
        'per_trade': {
            'compared': compared,
            'mismatches': len(diffs),
            'rounding_convention_only': rounding_only,
            'max_abs_exit_price_delta': max_price_delta,
            'max_abs_net_return_delta_pp': round(max_net_delta, 6),
            'first_mismatches': diffs[:8],
        },
        'books': {'production_precision': _book(replay(book_raw)),
                  'issue143_rounding_convention': _book(replay(book_143))},
        'trades': book_raw,
    }


def _published_rows(analytics_dir: str) -> dict:
    """#143's published per-grid book metrics (the figures this check must land on)."""
    path = os.path.join(analytics_dir, 'report.json.gz')
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        report = json.loads(handle.read())
    return {row['grid_id']: row for row in report['metrics']}


def _diff_book(got: dict, want: dict) -> dict:
    pairs = (('n_trades', got['n_trades'], want.get('n_trades'), COUNT_TOL),
             ('final_equity_rub', got['final_equity_rub'],
              want.get('final_equity_rub'), EQUITY_TOL),
             ('profit_factor', got['profit_factor'], want.get('profit_factor'), SHARE_TOL),
             ('win_rate_pct', got['win_rate_pct'], want.get('win_rate_pct'), SHARE_TOL),
             ('max_drawdown_daily_pct', got['max_drawdown_daily_pct'],
              want.get('max_drawdown_pct'), SHARE_TOL),
             ('skipped_entries_no_slot', got['skipped_entries_no_slot'],
              want.get('skipped_no_slot'), COUNT_TOL))
    rows, ok = {}, True
    for field, mine, theirs, tol in pairs:
        delta = None if mine is None or theirs is None else round(mine - theirs, 4)
        inside = delta is not None and abs(delta) <= tol
        rows[field] = {'production': mine, 'published_143': theirs,
                       'delta': delta, 'within_tolerance': inside}
        ok = ok and inside
    counts_want = dict(sorted((want.get('exit_type_counts') or {}).items()))
    counts_same = got['exit_type_counts'] == counts_want
    rows['exit_type_counts'] = {'production': got['exit_type_counts'],
                                'published_143': counts_want, 'equal': counts_same}
    return {'rows': rows, 'ok': ok and counts_same}


def main() -> int:
    parser = argparse.ArgumentParser(description='Issue #145 production-ladder check vs #143')
    parser.add_argument('--analytics-dir', default=os.path.join(
        _ROOT, 'analytics', 'issue-143-trailing-robustness'))
    parser.add_argument('--issue139-inputs', default=os.path.join(
        _ROOT, 'analytics', 'issue-139-trailing-stop-new-level', 'inputs.json'))
    parser.add_argument('--grids', default='ultra_late_tight,ref139')
    parser.add_argument('--out', default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'grid_check.json'))
    parser.add_argument('--trades-out', default='', help='dump the production trade list here')
    args = parser.parse_args()

    with io.open(args.issue139_inputs, encoding='utf-8') as handle:
        inputs = json.load(handle)
    capital = float(inputs['initial_capital_rub'])
    volume_rank = {ticker: rank for rank, ticker in enumerate(inputs['volume_order'])}
    commission_pct = float((inputs.get('config') or {}).get('commission_pct', 0.06))

    def replay(candidates):
        trades, curve, game_over, game_over_ts, skipped = _replay_portfolio_trades(
            candidates, volume_rank, initial_capital=capital,
            slot_size=float(inputs['slot_size_rub']),
            max_positions=int(inputs['max_positions']))
        return {'trades': trades, 'metrics': _portfolio_metrics(trades, curve, capital),
                'equity_curve': curve,
                'game_over': game_over, 'game_over_ts': game_over_ts,
                'skipped_entries_no_slot': skipped}

    grids = _load_grids(args.analytics_dir)
    published_exits = _load_exits(args.analytics_dir)
    published_rows = _published_rows(args.analytics_dir)
    candidates = _load_paths(os.path.join(args.analytics_dir, 'cache'), inputs['universe'])

    payload = {
        'issue': 145, 'ref_issues': [139, 143],
        'production_code': 'app.analytics.trailing_stop + portfolio_simulator replay',
        'backend': BACKEND,
        'scope': {'universe': len(inputs['universe']), 'period': [inputs['date_from'],
                                                                  inputs['date_to']],
                  'capital_rub': capital, 'slot_rub': inputs['slot_size_rub'],
                  'max_positions': inputs['max_positions'],
                  'candidates': len(candidates), 'commission_pct': commission_pct},
        'grids': {},
    }
    all_ok = True
    for grid_id in [g.strip() for g in args.grids.split(',') if g.strip()]:
        steps = grids[grid_id]['steps']
        result = check_grid(grid_id, steps, candidates, published_exits.get(grid_id, {}),
                            replay, commission_pct)
        trades = result.pop('trades')
        want_row = published_rows.get(grid_id, {})
        result['books_vs_published'] = {
            style: _diff_book(book, want_row) for style, book in result['books'].items()}
        same_convention = result['books_vs_published']['issue143_rounding_convention']
        result['ok'] = result['per_trade']['mismatches'] == 0 and same_convention['ok']
        all_ok = all_ok and result['ok']
        payload['grids'][grid_id] = result
        print('%-18s compared=%-5d real_mismatches=%-3d rounding_only=%-3d | '
              'n=%-5d equity(raw)=%-12s equity(143-style)=%-12s published=%-12s | '
              'dd=%-5s mix=%s | ok=%s'
              % (grid_id, result['per_trade']['compared'], result['per_trade']['mismatches'],
                 result['per_trade']['rounding_convention_only'],
                 result['books']['production_precision']['n_trades'],
                 result['books']['production_precision']['final_equity_rub'],
                 result['books']['issue143_rounding_convention']['final_equity_rub'],
                 want_row.get('final_equity_rub'),
                 result['books']['issue143_rounding_convention']['max_drawdown_daily_pct'],
                 result['books']['production_precision']['exit_type_counts'], result['ok']),
              flush=True)
        if args.trades_out:
            with io.open(args.trades_out, 'w', encoding='utf-8') as handle:
                json.dump({'grid_id': grid_id, 'trades': trades}, handle, ensure_ascii=False)

    payload['grid_check_ok'] = all_ok
    with io.open(args.out, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    print('grid_check_ok =', all_ok, '->', args.out)
    return 0 if all_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())


