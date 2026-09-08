"""Issue #145: stepped trailing stop in the production exit path (Epic #142, Block W).

No database, no network: every test drives the real code (`trailing_stop`, `on_bar`, the
`levels_reversal` plugin, `portfolio_simulator`) over hand-built bars, and covers BOTH
grids that matter - the shipped Product Owner default `ultra_late_tight` (three rungs with
a 0.1R gap, `TRAILING_STOP['steps']`) and the parity anchor `ref139` (two rungs) that
#147 measures against #139.

The geometry of the pure-function tests is entry 100 / initial stop 90 / take 130, so one
R is 10 and the rungs land on 120->119, 125->124, 130->129 (`ultra_late_tight`) and
120->115, 125->120 (`ref139`).
"""
from __future__ import annotations

import copy

import pandas as pd
import pytest

from app.analytics.backtest_models import EXIT_STOP, EXIT_TAKE, EXIT_TRAILING
from app.analytics.portfolio_simulator import _portfolio_metrics, _replay_portfolio_trades
from app.analytics.strategies.base import Position
from app.analytics.strategies.context import MarketContext
from app.analytics.strategies.levels_reversal import LevelsReversalStrategy
from app.analytics.strategy_engine import StrategyEvaluator
from app.analytics.trailing_stop import (
    TrailingState,
    apply_trailing_path,
    evaluate_bar,
    ladder_stop,
    trailing_from_config,
)
from app.analytics.trading_config import TRAILING_STOP
from tests.test_levels_sr_support import (
    EVAL_TS,
    SUPPORT_PRICE,
    TAKE_PRICE,
    _load,
    _path_a_context,
    _support_bar,
    _support_config,
)

ENTRY = 100.0
INITIAL_STOP = 90.0
TAKE = 130.0
RISK = ENTRY - INITIAL_STOP  # 10.0

# The shipped default (Product Owner, 2026-09-08) and the #139 parity anchor.
ULTRA = [{'trigger': 2.0, 'stop': 1.9},
         {'trigger': 2.5, 'stop': 2.4},
         {'trigger': 3.0, 'stop': 2.9}]
REF139 = [{'trigger': 2.0, 'stop': 1.5},
          {'trigger': 2.5, 'stop': 2.0}]
LADDERS = pytest.mark.parametrize('steps,name', [
    (ULTRA, 'ultra_late_tight'),
    (REF139, 'ref139'),
], ids=['ultra_late_tight', 'ref139'])


def _state(steps=None, *, entry=ENTRY, stop=INITIAL_STOP, take=TAKE):
    return TrailingState.build(steps or ULTRA, entry_exec=entry, initial_stop=stop, take=take)


def _block(steps, enabled=True):
    return {'trailing_stop': {'enabled': enabled, 'steps': copy.deepcopy(steps)}}


# ---------------------------------------------------------------------------
# 1. ladder_stop - the pure step function every contour calls
# ---------------------------------------------------------------------------

def test_ladder_holds_the_initial_stop_below_the_first_trigger():
    assert ladder_stop(ENTRY, INITIAL_STOP, ULTRA, 119.99) == (INITIAL_STOP, 0.0)
    assert ladder_stop(ENTRY, INITIAL_STOP, ULTRA, None) == (INITIAL_STOP, 0.0)
    assert ladder_stop(ENTRY, INITIAL_STOP, ULTRA, float('nan')) == (INITIAL_STOP, 0.0)


@LADDERS
def test_ladder_arms_exactly_on_the_trigger(steps, name):
    stop_price, reached = ladder_stop(ENTRY, INITIAL_STOP, steps, ENTRY + 2.0 * RISK)
    assert stop_price == pytest.approx(ENTRY + steps[0]['stop'] * RISK)
    assert reached == pytest.approx(steps[0]['stop'])


@LADDERS
def test_ladder_takes_the_highest_eligible_rung(steps, name):
    for step in steps:
        stop_price, reached = ladder_stop(
            ENTRY, INITIAL_STOP, steps, ENTRY + step['trigger'] * RISK)
        assert stop_price == pytest.approx(ENTRY + step['stop'] * RISK), step
        assert reached == pytest.approx(step['stop']), step


def test_ladder_is_a_monotone_maximum_never_below_the_initial_stop():
    state = _state()
    state.arm(121.0)          # +2.0R -> +1.9R (119)
    assert state.pending_stop == pytest.approx(119.0)
    state.arm(118.0)          # a lower high cannot undo the rung
    assert state.pending_stop == pytest.approx(119.0)
    state.arm(131.0)          # +3.0R -> +2.9R (129)
    assert state.pending_stop == pytest.approx(129.0)
    assert state.pending_reached == pytest.approx(2.9)
    assert state.pending_stop >= state.initial_stop


def test_ladder_ignores_steps_it_cannot_read():
    broken = [{'trigger': 2.0, 'stop': 1.9}, 'not-a-step',
              {'trigger': float('nan'), 'stop': 1.0}, {'trigger': 3.0}]
    stop_price, reached = ladder_stop(ENTRY, INITIAL_STOP, broken, 140.0)
    assert stop_price == pytest.approx(119.0), 'only the readable rung may move the stop'
    assert reached == pytest.approx(1.9)


# ---------------------------------------------------------------------------
# 2. TrailingState - the bar sequence (stop -> take -> arm)
# ---------------------------------------------------------------------------

@LADDERS
def test_a_rung_never_bites_on_the_bar_that_armed_it(steps, name):
    """No intra-bar look-ahead: the high of bar 0 arms a stop that bar 1 can hit."""
    rung = ENTRY + steps[0]['stop'] * RISK
    state = _state(steps)
    first = state.evaluate(high=121.0, low=rung - 1.0, bar_key=0)
    assert first.exit_reason is None, name
    assert first.armed is False, name
    # The armed stop is already booked for the next bar...
    assert first.stop == pytest.approx(rung), name
    # ...but the SAME bar must not be stopped out by it.
    again = state.evaluate(high=121.0, low=rung - 1.0, bar_key=0)
    assert again.exit_reason is None, 'replaying the same bar must not promote the stop'
    second = state.evaluate(high=120.5, low=rung - 0.5, bar_key=1)
    assert second.exit_price == pytest.approx(rung), name
    assert second.exit_reason == EXIT_TRAILING, name
    assert second.armed is True, name


def test_the_unraised_stop_keeps_the_baseline_reason():
    state = _state()
    decision = state.evaluate(high=101.0, low=89.0, bar_key=0)
    assert decision.exit_reason == EXIT_STOP          # regression parity: not 'trailing'
    assert decision.exit_price == pytest.approx(INITIAL_STOP)
    assert decision.step_reached == pytest.approx(0.0)
    assert decision.armed is False


@LADDERS
def test_the_stop_wins_when_stop_and_take_share_a_bar(steps, name):
    rung = ENTRY + steps[0]['stop'] * RISK
    state = _state(steps)
    state.evaluate(high=121.0, low=rung + 0.5, bar_key=0)        # arm the first rung
    decision = state.evaluate(high=140.0, low=rung - 1.0, bar_key=1)
    assert decision.exit_reason == EXIT_TRAILING, name
    assert decision.exit_price == pytest.approx(rung), name


@LADDERS
def test_two_r_without_a_pullback_to_the_rung_still_exits_at_the_take(steps, name):
    """Issue #145 section 4: price rides through +2R to the take when it never dips."""
    path = [(121.0, 119.5), (124.0, 120.0), (131.0, 125.0)]
    state = _state(steps)
    decisions = [state.evaluate(high=h, low=l, bar_key=i)
                 for i, (h, l) in enumerate(path)]
    assert [d.exit_reason for d in decisions[:-1]] == [None, None], name
    assert decisions[-1].exit_reason == EXIT_TAKE, name
    assert decisions[-1].exit_price == pytest.approx(TAKE), name   # the take never moves


def test_break_even_rung_reports_trailing_at_the_entry_price():
    """`breakeven_2_0` of the 143-trailing-v3 lattice parks the stop on 0R."""
    state = _state([{'trigger': 2.0, 'stop': 0.0}])
    armed = state.evaluate(high=121.0, low=119.5, bar_key=0)
    assert armed.stop == pytest.approx(ENTRY)
    hit = state.evaluate(high=101.0, low=99.5, bar_key=1)
    assert hit.exit_price == pytest.approx(ENTRY)
    assert hit.exit_reason == EXIT_TRAILING, '0R is a raised stop, not the initial one'
    assert hit.step_reached == pytest.approx(0.0)
    assert hit.armed is True


def test_evaluate_bar_is_the_shared_entry_point_and_passes_none_through():
    assert evaluate_bar(None, high=120.0, low=100.0, bar_key=0) is None
    state = _state()
    decision = evaluate_bar(state, high=121.0, low=119.5, bar_key=0)
    assert decision.stop == pytest.approx(119.0)
    assert state.pending_stop == pytest.approx(decision.stop)


def test_snapshot_exposes_the_fields_the_issue_asks_for():
    state = _state()
    state.evaluate(high=126.0, low=119.5, bar_key=0)
    snap = state.snapshot()
    assert snap['initial_stop'] == pytest.approx(INITIAL_STOP)
    assert snap['risk_r'] == pytest.approx(RISK)
    assert snap['trailing_steps'] == ULTRA
    assert snap['current_stop'] == pytest.approx(INITIAL_STOP)  # not promoted yet
    assert snap['step_reached'] == pytest.approx(0.0)
    assert snap['armed'] is False


@LADDERS
def test_state_needs_a_usable_ladder_and_a_positive_risk(steps, name):
    assert TrailingState.build(steps, entry_exec=ENTRY, initial_stop=ENTRY,
                               take=TAKE) is None, 'risk <= 0 cannot be armed'
    assert TrailingState.build(steps, entry_exec=ENTRY, initial_stop=float('nan'),
                               take=TAKE) is None
    assert TrailingState.build([], entry_exec=ENTRY, initial_stop=INITIAL_STOP,
                               take=TAKE) is None, name
    assert TrailingState.build([{'trigger': 2.0}], entry_exec=ENTRY,
                               initial_stop=INITIAL_STOP, take=TAKE) is None


# ---------------------------------------------------------------------------
# 3. config -> ladder: the shipped default is off, a refused ladder changes nothing
# ---------------------------------------------------------------------------

def test_absent_disabled_or_empty_block_keeps_the_baseline():
    for config in (None, {}, {'patterns': ['levels_sr_support']},
                   {'trailing_stop': None}, {'trailing_stop': {}},
                   {'trailing_stop': {'enabled': True}},
                   _block(ULTRA, enabled=False),
                   {'trailing_stop': {'enabled': 'false', 'steps': ULTRA}}):
        assert trailing_from_config(config, entry_exec=ENTRY, initial_stop=INITIAL_STOP,
                                    take=TAKE) is None, config


def test_enabled_with_nothing_to_arm_is_refused():
    assert trailing_from_config(_block([]), entry_exec=ENTRY,
                                initial_stop=INITIAL_STOP, take=TAKE) is None


@pytest.mark.parametrize('steps,reason', [
    ([{'trigger': 2.0, 'stop': 1.9}, {'trigger': 2.5, 'stop': 1.2}], 'trailing_not_monotonic'),
    ([{'trigger': 4.0, 'stop': 3.9}], 'trailing_step_invalid'),
    ([{'trigger': 2.0, 'stop': 3.5}], 'trailing_step_invalid'),
    (['nope'], 'trailing_step_invalid'),
    ([{'trigger': float(i) * 0.1 + 0.1, 'stop': 0.05} for i in range(7)],
     'trailing_too_many_steps'),
])
def test_the_engine_refuses_a_ladder_the_contract_validator_rejects(steps, reason):
    """Fail-safe until #146/#149 gate the write paths: an invalid block arms nothing."""
    from app.analytics.trading_config import resolve_trailing_stop

    assert reason in resolve_trailing_stop(_block(steps))['reasons']
    assert trailing_from_config(_block(steps), entry_exec=ENTRY,
                                initial_stop=INITIAL_STOP, take=TAKE) is None


def test_shipped_default_grid_arms_when_the_flag_is_flipped():
    """`ultra_late_tight` is the PO default ladder; it is the contract's own steps."""
    state = trailing_from_config(
        _block(TRAILING_STOP['steps']),
        entry_exec=ENTRY, initial_stop=INITIAL_STOP, take=TAKE)
    assert state is not None
    assert state.steps == copy.deepcopy(TRAILING_STOP['steps'])
    assert state.risk == pytest.approx(RISK)
    assert state.live_stop == pytest.approx(INITIAL_STOP)
    # Flip the shipped default back on and the ladder is live from the first rung up.
    assert state.evaluate(high=121.0, low=119.5, bar_key=0).stop == pytest.approx(119.0)


# ---------------------------------------------------------------------------
# 4. apply_trailing_path - the production ladder over a recorded path
# ---------------------------------------------------------------------------

def test_apply_trailing_path_reproduces_the_issue_139_self_check():
    """Same numbers analytics/issue-139.../trailing.py prints in its __main__ block."""
    out = apply_trailing_path(entry_exec=100.0, initial_stop=90.0, take=130.0,
                              path=[(121, 118), (120, 114), (140, 113)], steps=REF139)
    assert out['baseline']['exit_reason'] == EXIT_TAKE
    assert out['trailing']['exit_reason'] == EXIT_TRAILING
    assert out['trailing']['exit_price'] == pytest.approx(115.0)
    assert out['trailing']['step_reached'] == pytest.approx(1.5)
    assert out['trailing']['exit_index'] == 1


@LADDERS
def test_apply_trailing_path_rung_without_pullback_leaves_the_take_intact(steps, name):
    out = apply_trailing_path(entry_exec=ENTRY, initial_stop=INITIAL_STOP, take=TAKE,
                              path=[(121.0, 119.5), (124.0, 120.0), (131.0, 125.0)],
                              steps=steps)
    assert out['baseline']['exit_reason'] == EXIT_TAKE, name
    assert out['trailing']['exit_reason'] == EXIT_TAKE, name
    assert out['trailing']['net_return_pct'] == pytest.approx(
        out['baseline']['net_return_pct']), name


# ---------------------------------------------------------------------------
# 5. StrategyEvaluator.on_bar - the brain applies the ladder
# ---------------------------------------------------------------------------
# levels_sr_support geometry (tests/test_levels_sr_support.py): the entry bar closes at
# 96.5, the stop is the support level (96.0) and the take the resistance (120.0), so one
# R is 0.5 and the +2.0R trigger sits on 97.5 (the +1.9R rung on 97.45).

E_ENTRY = 96.5
E_STOP = SUPPORT_PRICE
E_TAKE = TAKE_PRICE
E_RISK = E_ENTRY - E_STOP


def _managed_bar(minutes_after_entry: int, *, high: float, low: float, close: float):
    ts = EVAL_TS + pd.Timedelta(minutes=minutes_after_entry)
    return pd.Series({'timestamp': ts, 'open': close, 'high': high, 'low': low,
                      'close': close})


def _run_engine(config, bars):
    """Feed bars to a FRESH evaluator (the first one is the entry bar) -> decisions."""
    ev = StrategyEvaluator(config)
    _load(ev, _path_a_context())
    return ev, [ev.on_bar(bar, idx=i) for i, bar in enumerate(bars)]


def _trades(decisions):
    return [d['trade'] for d in decisions if d['action'] == 'exit']


def _expect_rung(steps, trigger_index=0):
    step = steps[trigger_index]
    return (E_ENTRY + step['trigger'] * E_RISK, E_ENTRY + step['stop'] * E_RISK)


@LADDERS
def test_engine_emits_trailing_once_the_raised_stop_is_hit(steps, name):
    trigger, rung = _expect_rung(steps)
    bars = [
        _support_bar(),  # idx 0: entry
        _managed_bar(1, high=trigger + 0.1, low=E_STOP + 0.2, close=105),  # idx 1: arm
        _managed_bar(2, high=rung + 0.1, low=rung - 0.05, close=100),      # idx 2: hit
    ]
    _ev, decisions = _run_engine(_support_config(**_block(steps)), bars)
    assert decisions[0]['action'] == 'enter', name
    assert decisions[1]['action'] == 'hold', name
    trade = _trades(decisions)[0]
    assert trade['exit_reason'] == EXIT_TRAILING, name
    assert trade['exit_price'] == pytest.approx(rung), name
    assert trade['step_reached'] == pytest.approx(steps[0]['stop']), name
    expected_net = round((rung / E_ENTRY - 1.0) * 100.0 - 0.06, 5)
    assert trade['net_return_pct'] == pytest.approx(expected_net), name
    assert trade['bars_held'] == 2, name


@LADDERS
def test_engine_reports_the_ladder_fields_on_the_position(steps, name):
    trigger, rung = _expect_rung(steps)
    bars = [_support_bar(),
            _managed_bar(1, high=trigger + 0.1, low=E_STOP + 0.2, close=105)]
    ev, decisions = _run_engine(_support_config(**_block(steps)), bars)
    assert decisions[1]['action'] == 'hold', name
    pos = ev.position
    assert pos['initial_stop'] == pytest.approx(E_STOP), name
    assert pos['risk_r'] == pytest.approx(E_RISK), name
    assert pos['trailing_steps'] == copy.deepcopy(steps), name
    # stop / step_reached are the pair that becomes effective from the NEXT bar...
    assert pos['stop'] == pytest.approx(rung), name
    assert pos['step_reached'] == pytest.approx(steps[0]['stop']), name
    # ...while the ladder keeps the stop the exit check of this bar actually used.
    assert pos['trailing_state'].live_stop == pytest.approx(E_STOP), name
    assert pos['take'] == pytest.approx(E_TAKE), name


def test_engine_does_not_arm_from_the_entry_bar():
    """#139 excludes the entry bar from the managed path; so does the engine."""
    early = [{'trigger': 0.5, 'stop': 0.4}]        # trigger 96.75, rung 96.4
    ev = StrategyEvaluator(_support_config(**_block(early)))
    _load(ev, _path_a_context())
    assert ev.on_bar(_support_bar(), idx=0)['action'] == 'enter'   # high 96.8 >= trigger
    assert ev.position['trailing_state'].pending_stop == pytest.approx(E_STOP), \
        'the entry bar high must not arm a rung'
    assert ev.position['stop'] == pytest.approx(E_STOP)
    # Bar 1: the low is below the rung the entry bar WOULD have armed, above the initial
    # stop - a look-ahead engine would close here.
    held = ev.on_bar(_managed_bar(1, high=96.9, low=96.3, close=100), idx=1)
    assert held['action'] == 'hold', 'a stop armed by the entry bar would bite here'
    assert ev.position['stop'] == pytest.approx(E_ENTRY + 0.4 * E_RISK)
    trade = ev.on_bar(_managed_bar(2, high=96.9, low=96.3, close=100), idx=2)['trade']
    assert trade['exit_reason'] == EXIT_TRAILING
    assert trade['exit_price'] == pytest.approx(E_ENTRY + 0.4 * E_RISK)
    assert trade['step_reached'] == pytest.approx(0.4)


def test_engine_rides_to_the_take_over_an_armed_stop():
    """The take is level-based and wins as long as the raised stop was not touched."""
    bars = [_support_bar(),
            _managed_bar(1, high=101.0, low=99.0, close=100.5),
            _managed_bar(2, high=E_TAKE + 1.0, low=100.0, close=105.0)]
    ev, decisions = _run_engine(_support_config(**_block(ULTRA)), bars)
    trade = _trades(decisions)[0]
    assert trade['exit_reason'] == EXIT_TAKE
    assert trade['exit_price'] == pytest.approx(E_TAKE)
    assert trade['step_reached'] == pytest.approx(2.9), 'the ladder booked every rung'
    assert ev.position is None


def test_engine_ladder_state_is_per_position():
    """After a trailing exit the next entry starts from its own initial stop."""
    bars = [_support_bar(),
            _managed_bar(1, high=97.6, low=96.3, close=105),
            _managed_bar(2, high=97.5, low=96.2, close=99.0),
            _managed_bar(3, high=96.8, low=96.0, close=96.5)]   # back inside the support zone
    ev, decisions = _run_engine(_support_config(**_block(ULTRA)), bars)
    assert _trades(decisions)[0]['exit_reason'] == EXIT_TRAILING
    assert decisions[3]['action'] == 'enter', 'the support re-entry must still work'
    state = ev.position['trailing_state']
    assert state.live_stop == pytest.approx(E_STOP)
    assert state.pending_stop == pytest.approx(E_STOP)
    assert state.step_reached == pytest.approx(0.0)
    assert state.armed is False
    assert ev.position['step_reached'] == pytest.approx(0.0)


@pytest.mark.parametrize('label,config', [
    ('no-key', _support_config()),
    ('disabled-with-ladder', _support_config(**_block(ULTRA, enabled=False))),
    ('enabled-empty-ladder', _support_config(**_block([]))),
    ('non-monotone-ladder', _support_config(**_block(
        [{'trigger': 2.0, 'stop': 1.9}, {'trigger': 2.5, 'stop': 1.2}]))),
    ('out-of-bounds-ladder', _support_config(**_block([{'trigger': 9.0, 'stop': 8.0}]))),
])
def test_engine_is_bit_for_bit_the_baseline_when_the_ladder_does_not_arm(label, config):
    """Issue #145 section 2: enabled=false or a refused ladder - not one deviation."""
    bars = [_support_bar(),
            _managed_bar(1, high=97.6, low=96.3, close=105),
            _managed_bar(2, high=97.5, low=96.2, close=100),
            _managed_bar(3, high=121.0, low=100.0, close=120.5)]
    _ev, expected = _run_engine(_support_config(), bars)
    _ev2, actual = _run_engine(config, bars)
    assert actual == expected, label
    assert [d['action'] for d in actual] == ['enter', 'hold', 'hold', 'exit'], label
    assert 'step_reached' not in _trades(actual)[0], label
    assert _trades(actual)[0]['exit_reason'] == EXIT_TAKE, label


# ---------------------------------------------------------------------------
# 6. levels_reversal plugin - the documented mirror of on_bar
# ---------------------------------------------------------------------------

def _plugin_context(bars):
    ctx = _path_a_context()
    frame = pd.DataFrame([{key: bar[key] for key in
                           ('timestamp', 'open', 'high', 'low', 'close')} for bar in bars])
    return MarketContext(timestamp=frame['timestamp'].iloc[-1], candles_1min=frame,
                         levels=ctx['levels'], ts_4h=ctx['ts_4h'],
                         atr_by_ts=ctx['atr_by_ts'], buy_ts=ctx['buy_ts'],
                         confirm_series=ctx['confirm_series'], signal_filter_series=[],
                         htf_bars=ctx['htf_bars'])


def _open_plugin_position(config, entry_bar):
    """Open a position through the plugin exactly as portfolio_backtest does."""
    plugin = LevelsReversalStrategy(config)
    market = _plugin_context([entry_bar])
    plugin.load_market_context(market)
    signal = plugin.check_entry(market)
    assert signal is not None
    entry_exec = signal.entry_price  # slippage_pct = 0 in the fixture config
    return plugin, Position(
        entry_price=signal.entry_price,
        entry_ts=signal.timestamp,
        stop=signal.stop,
        take=signal.take,
        size=1.0,
        bars_held=0,
        metadata={'entry_exec': entry_exec},
        initial_stop=signal.stop,
        trailing=trailing_from_config(config, entry_exec=entry_exec,
                                      initial_stop=signal.stop, take=signal.take),
    )


@LADDERS
def test_plugin_exit_mirrors_the_brain_bar_by_bar(steps, name):
    """Same bars -> same exit: the plugin never drifts from the evaluator."""
    config = _support_config(**_block(steps))
    trigger, rung = _expect_rung(steps)
    bars = [_support_bar(),
            _managed_bar(1, high=trigger + 0.1, low=E_STOP + 0.2, close=105),
            _managed_bar(2, high=rung + 0.1, low=rung - 0.05, close=100)]

    _ev, decisions = _run_engine(config, bars)
    brain = [None if d['action'] != 'exit' else
             (d['trade']['exit_price'], d['trade']['exit_reason'],
              d['trade']['step_reached']) for d in decisions]

    plugin, position = _open_plugin_position(config, bars[0])
    assert position.trailing is not None, name
    plugin_out = []
    for i in range(1, len(bars)):
        signal = plugin.check_exit(position, _plugin_context(bars[:i + 1]))
        plugin_out.append(None if signal is None else
                          (signal.exit_price, signal.reason, position.step_reached))
        position.bars_held += 1
    assert plugin_out == brain[1:], name


@LADDERS
def test_manage_position_before_check_exit_cannot_look_ahead(steps, name):
    """The plugin lifecycle may run manage_position first - the ratchet is bar-keyed."""
    config = _support_config(**_block(steps))
    trigger, rung = _expect_rung(steps)
    plugin, position = _open_plugin_position(config, _support_bar())
    arm_bar = _managed_bar(1, high=trigger + 0.1, low=E_ENTRY + 0.05, close=105)
    market = _plugin_context([arm_bar])
    assert plugin.manage_position(position, market) is not None   # HOLD, ratchets
    assert position.stop == pytest.approx(rung), name
    assert plugin.check_exit(position, market) is None, \
        'the stop armed by this bar must not close this bar'
    hit_bar = _managed_bar(2, high=rung + 0.1, low=rung - 0.05, close=100)
    signal = plugin.check_exit(position, _plugin_context([arm_bar, hit_bar]))
    assert signal is not None and signal.reason == EXIT_TRAILING, name
    assert signal.exit_price == pytest.approx(rung), name
    assert signal.metadata['step_reached'] == pytest.approx(steps[0]['stop']), name


def test_plugin_without_a_ladder_keeps_the_plain_stop_take_mirror():
    plugin, position = _open_plugin_position(_support_config(), _support_bar())
    assert position.trailing is None
    market = _plugin_context([_managed_bar(1, high=97.0, low=E_STOP - 0.1, close=95.9)])
    signal = plugin.check_exit(position, market)
    assert signal.reason == EXIT_STOP
    assert signal.exit_price == pytest.approx(E_STOP)
    assert 'step_reached' not in signal.metadata


# ---------------------------------------------------------------------------
# 7. portfolio_simulator - the reason travels, the slots free up earlier
# ---------------------------------------------------------------------------

def _candidate(ticker, entry, exit_, reason, net, step=None):
    cand = {'ticker': ticker, 'entry_ts': entry, 'exit_ts': exit_,
            'entry_price': 100.0, 'exit_price': 101.0, 'exit_reason': reason,
            'net_return_pct': net, 'bars_held': 5}
    if step is not None:
        cand['step_reached'] = step
    return cand


def test_replay_passes_the_trailing_reason_and_the_rung_through():
    trades, _curve, game_over, _go_ts, skipped = _replay_portfolio_trades(
        [_candidate('AAA', '2026-01-01 10:00:00', '2026-01-01 11:00:00',
                    EXIT_TRAILING, 1.5, 2.4)],
        {'AAA': 0}, 50_000, 10_000, 5)
    assert not game_over and skipped == 0
    assert trades[0]['exit_reason'] == EXIT_TRAILING
    assert trades[0]['step_reached'] == pytest.approx(2.4)


def test_replay_leaves_a_ladder_free_trade_shape_untouched():
    trades, *_rest = _replay_portfolio_trades(
        [_candidate('AAA', '2026-01-01 10:00:00', '2026-01-01 11:00:00', EXIT_STOP, -1.0)],
        {'AAA': 0}, 50_000, 10_000, 5)
    assert 'step_reached' not in trades[0], 'no ladder -> no new key in the trade record'


def test_an_earlier_trailing_exit_frees_a_slot_for_the_next_candidate():
    """Issue #145 section 4: the book gets MORE trades because closes arrive earlier."""
    shared = [
        _candidate('BBB', '2026-01-01 10:30:00', '2026-01-01 12:00:00', EXIT_STOP, -1.0),
        _candidate('CCC', '2026-01-01 11:30:00', '2026-01-01 13:00:00', EXIT_TAKE, 2.0),
    ]
    fixed = [_candidate('AAA', '2026-01-01 10:00:00', '2026-01-01 14:00:00',
                        EXIT_STOP, -1.0)] + shared
    trailed = [_candidate('AAA', '2026-01-01 10:00:00', '2026-01-01 11:00:00',
                          EXIT_TRAILING, 1.5, 1.9)] + shared
    rank = {'AAA': 0, 'BBB': 1, 'CCC': 2}

    fixed_trades, _c1, _g1, _t1, fixed_skips = _replay_portfolio_trades(
        fixed, rank, 20_000, 10_000, 1)
    trailed_trades, _c2, _g2, _t2, trailed_skips = _replay_portfolio_trades(
        trailed, rank, 20_000, 10_000, 1)

    assert len(fixed_trades) == 1 and fixed_skips == 2
    assert [t['ticker'] for t in trailed_trades] == ['AAA', 'CCC']
    assert trailed_skips == 1
    assert len(trailed_trades) > len(fixed_trades), 'the slot must open earlier'


def test_portfolio_metrics_split_the_book_by_exit_reason():
    trades = [
        {'pnl_rub': 100.0, 'exit_reason': EXIT_TRAILING},
        {'pnl_rub': 50.0, 'exit_reason': EXIT_TRAILING},
        {'pnl_rub': -80.0, 'exit_reason': EXIT_STOP},
        {'pnl_rub': 200.0, 'exit_reason': EXIT_TAKE},
    ]
    metrics = _portfolio_metrics(trades, [{'equity_rub': 50_000}, {'equity_rub': 50_270}],
                                 50_000)
    assert metrics['exit_reason_counts'] == {'stop': 1, 'take': 1, 'trailing': 2}
    assert metrics['trailing_exits'] == 2
    assert metrics['initial_stop_exits'] == 1
    assert metrics['take_exits'] == 1
    assert metrics['trailing_exit_share_pct'] == pytest.approx(50.0)


def test_portfolio_metrics_of_an_empty_book_still_report_the_mix():
    metrics = _portfolio_metrics([], [], 50_000)
    assert metrics['exit_reason_counts'] == {}
    assert metrics['trailing_exits'] == 0
    assert metrics['trailing_exit_share_pct'] == pytest.approx(0.0)






