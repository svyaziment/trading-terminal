"""
Central trading configuration - SINGLE SOURCE OF TRUTH for:
  1) the traded universe (tickers) - read from trading.trading_universe (rank order);
  2) the sandbox live subset (LIVE_UNIVERSE / get_live_trading_universe);
  3) strategy definitions (name -> params) - so backtest and paper trading never diverge;
  4) T-Bank sandbox execution and retry policy;
  5) live order-book imbalance defaults;
  6) live position-sizing risk limits;
  7) live sandbox executor policy;
  7b) MOEX main-session calendar for overnight LiveExecutor (Issue #137);
  8) levels state-machine breakout thresholds (Issue #106 / Epic #105);
  9) level_breakout_retest trigger constant (Issue #107; Lab params live in pattern_registry);
 10) stepped trailing-stop exit contract (Issue #144 / Epic #142 Block W - schema,
     defaults, normalization and validation; the engine applies it in #145).

Every module (data_refresher, online_data, online_signals, paper_trader, strategy_backtest)
must import get_trading_universe() / get_strategy() from here instead of hardcoding
ticker lists or strategy parameters. LiveExecutor uses get_live_trading_universe().
"""
from __future__ import annotations
import math
from typing import Any, Dict, List, Optional


# Non-secret defaults for the real-time order-book filter. A strategy can override
# only imbalance_threshold; stream depth and freshness remain infrastructure policy.
ORDERBOOK_IMBALANCE: Dict[str, Any] = {
    'depth': 10,
    'max_age_minutes': 5,
    'default_threshold': 1.0,
}


def get_orderbook_imbalance_config() -> Dict[str, Any]:
    """Return an isolated copy of the live order-book imbalance policy."""
    return dict(ORDERBOOK_IMBALANCE)


# Non-secret defaults for live position sizing. Callers may override them for
# simulations, while live trading uses these values as the single source of truth.
POSITION_SIZING: Dict[str, float] = {
    'risk_per_trade_pct': 1.0,
    'max_position_pct': 20.0,
}


def get_position_sizing_config() -> Dict[str, float]:
    """Return an isolated copy of the live position-sizing policy."""
    return dict(POSITION_SIZING)


# Sandbox-only live execution policy. Risk limits are composed from
# POSITION_SIZING by the getter to avoid two independently editable defaults.
LIVE_TRADING: Dict[str, Any] = {
    'enabled': True,
    'max_open_positions': 5,
    'imbalance_threshold': 1.0,
    'api_rate_limit': 10.0,
    'close_positions_on_shutdown': False,
    'check_interval_seconds': 30,
    'context_refresh_seconds': 900,
}

# Issue #137: MOEX main-session clock for overnight LiveExecutor.
# Wall clock is converted to MSK (UTC+3). Entries are [10:00, 19:00) weekdays.
# start_processes.sh uses this to size paper duration; LiveExecutor waits until
# session open when until_session_end=True. Holidays are not modelled in v1.
MOEX_SESSION: Dict[str, Any] = {
    'tz_offset_hours': 3,
    'entry_start_hour': 10,
    'entry_end_hour': 19,
    'session_end_hour': 19,
    'weekdays': (0, 1, 2, 3, 4),
    'wait_poll_seconds': 60,
    'wait_log_seconds': 900,
    'duration_margin_minutes': 15,
}


def get_moex_session_config() -> Dict[str, Any]:
    """Return an isolated copy of the MOEX main-session calendar."""
    cfg = dict(MOEX_SESSION)
    cfg['weekdays'] = tuple(cfg['weekdays'])
    return cfg


def get_live_trading_config() -> Dict[str, Any]:
    """Return the complete sandbox live-executor policy."""
    return {
        'risk_per_trade_pct': POSITION_SIZING['risk_per_trade_pct'],
        'max_position_pct': POSITION_SIZING['max_position_pct'],
        **LIVE_TRADING,
    }


# Secrets and the sandbox account id are intentionally loaded by config_manager from
# TINVEST_SANDBOX / TINVEST_SANDBOX_ACC. TINVEST_TOKEN / TINVEST_ACC remain
# market-data-only.
# Only non-secret execution policy lives here.
SANDBOX_TRADING: Dict[str, Any] = {
    'enabled': True,
    'allow_real_trading': False,
    'initial_capital_rub': 50_000,
    'default_currency': 'rub',
    'retry_attempts': 3,
    'retry_base_delay_seconds': 0.5,
    'discover_account_when_missing': True,
}


def get_sandbox_trading_config() -> Dict[str, Any]:
    """Return an isolated copy of the T-Bank sandbox execution policy."""
    return dict(SANDBOX_TRADING)


# Issue #106: in-memory support/resistance lifecycle (breakout + role reversal).
# LevelsTracker reads these thresholds. StrategyEvaluator wires the tracker
# when `level_breakout_retest` (Issue #107), `levels_sr_breakout` (#117),
# or `levels_sr_support` (#127) is on.
# zone_extension_atr documents the current build_levels zone width (zone_atr_mult);
# the tracker does not recompute zones — it uses zone_lower / zone_upper as given.
LEVEL_STATE_MACHINE: Dict[str, Any] = {
    'breakout_buffer_atr': 0.25,
    'confirm_bars': 2,
    'min_penetration_atr': 0.5,
    'zone_extension_atr': 0.5,
}


def get_level_state_machine_config() -> Dict[str, Any]:
    """Return an isolated copy of the levels state-machine policy."""
    return dict(LEVEL_STATE_MACHINE)


# Issue #107: non-Lab trigger constant for the bullish-body entry trigger.
# Lab-tunable params (retest window, zone, stop_atr, RR) live in pattern_registry.
LEVEL_BREAKOUT_RETEST: Dict[str, Any] = {
    'bullish_body_ratio': 0.6,
}


def get_level_breakout_retest_config() -> Dict[str, Any]:
    """Return an isolated copy of the breakout-retest trigger policy."""
    return dict(LEVEL_BREAKOUT_RETEST)


# --- Stepped trailing stop (Issue #144 / Epic #142, Block W) -------------------
# Contract only: this block defines the schema, the defaults and the validation of
# `strategies.config['trailing_stop']`. Nothing here changes exit behaviour -
# StrategyEvaluator starts applying the ladder in #145, the API surfaces the reason
# codes in #149, paper in #148 and the sandbox executor in #151. No production path
# calls these helpers yet - this repo has no validate_config() to hook into - so a
# malformed ladder is still accepted at save time until the #146 editor and the #149
# API gate on require_valid_trailing_stop(). A config without the key (or with
# `enabled: false`, the shipped state) normalizes exactly as it did before #144
# (Issue #144 section 5).
# These numbers are the single source of truth: the engine, the API and the frontend
# must never restate them (the frontend receives them through the API schema).
#
# A step is expressed in R multiples measured from the entry (the red line of #139):
#   {"trigger": 2.0, "stop": 1.9}  ->  at +2.0R of floating profit the stop moves to +1.9R.
# The default ladder is the Product Owner approved `ultra_late_tight` grid of the
# 143-trailing-v3 robustness lattice (#143 / #155, decision of 2026-09-08); it is
# cross-checked against analytics/issue-143-trailing-robustness/grids.json by
# backend/tests/test_trailing_contract.py. The #139 grid (`ref139`) is NOT a default -
# it stays the parity anchor that #147 injects explicitly.
TRAILING_STOP: Dict[str, Any] = {
    'enabled': False,
    'steps': [
        {'trigger': 2.0, 'stop': 1.9},
        {'trigger': 2.5, 'stop': 2.4},
        {'trigger': 3.0, 'stop': 2.9},
    ],
    # Ladder length. The approved default has three steps, so this must stay >= 3.
    'max_steps': 6,
    # Bounds in R: `min_trigger` is exclusive, the others are inclusive. They cover
    # every grid of the published 143-trailing-v3 lattice (highest trigger 3.5R,
    # highest stop 3.0R, lowest stop 0.0R = break-even). There is deliberately NO
    # minimum gap between trigger and stop: the production ladder lives on a 0.1R gap,
    # and any "trigger - stop >= 0.5" heuristic would reject the shipped default.
    'min_trigger': 0.0,
    'max_trigger': 3.5,
    'min_stop': 0.0,
    'max_stop': 3.0,
}

# Stable rejection reasons. The API (#149), paper (#148) and live (#151) surface these
# strings verbatim, so renaming one is a contract break.
TRAILING_REASON_DISABLED = 'trailing_disabled'              # enabled but nothing to arm
TRAILING_REASON_STEP_INVALID = 'trailing_step_invalid'      # shape / bounds / non-finite
TRAILING_REASON_NOT_MONOTONIC = 'trailing_not_monotonic'    # stop falls as trigger rises
TRAILING_REASON_TOO_MANY_STEPS = 'trailing_too_many_steps'  # ladder longer than max_steps


def get_trailing_stop_config() -> Dict[str, Any]:
    """Return an isolated copy of the trailing-stop contract (defaults + bounds)."""
    cfg = dict(TRAILING_STOP)
    cfg['steps'] = [dict(step) for step in TRAILING_STOP['steps']]
    return cfg


def _trailing_number(value: Any) -> Optional[float]:
    """Coerce a step value to a finite float, or None when it is not a usable number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _trailing_enabled(value: Any) -> bool:
    """Strict `enabled` flag: only a real bool or an explicit truth-word arms the ladder."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return False


def _coerce_trailing_step(raw: Any) -> Optional[Dict[str, float]]:
    """Coerce one raw step to {'trigger': float, 'stop': float}; None when unusable."""
    if not isinstance(raw, dict):
        return None
    trigger = _trailing_number(raw.get('trigger'))
    stop = _trailing_number(raw.get('stop'))
    if trigger is None or stop is None:
        return None
    return {'trigger': trigger, 'stop': stop}


def _trailing_step_in_bounds(step: Dict[str, float]) -> bool:
    """True when one coerced step respects the trigger/stop bounds and trigger > stop."""
    trigger, stop = step['trigger'], step['stop']
    return (
        trigger > float(TRAILING_STOP['min_trigger'])
        and trigger <= float(TRAILING_STOP['max_trigger'])
        and stop >= float(TRAILING_STOP['min_stop'])
        and stop <= float(TRAILING_STOP['max_stop'])
        and stop < trigger  # a stop can never sit at or above its own trigger
    )


def normalize_trailing_stop(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Canonical form of `config['trailing_stop']` -> {'enabled': bool, 'steps': [...]}.

    Idempotent: normalizing an already normalized block changes nothing, so the engine
    (#145), the API (#149) and the paper/live paths can call it on every run.

    - no key / None / {} / non-dict config -> {'enabled': False, 'steps': []} - the
      pre-#144 behaviour is preserved bit-for-bit (Issue #144 section 5);
    - `enabled` is a strict bool (truthy strings and numbers normalize to False so a
      malformed flag can never arm the ladder silently);
    - steps keep their float precision (1.9 / 2.4 / 2.9 stay exactly that - no rounding
      to 0.5R, no stringification), are sorted by trigger and de-duplicated;
    - structurally broken steps are dropped here. Validate the RAW list with
      `validate_trailing_steps` before trusting the result - normalization is the
      canonicalizer, not the gatekeeper.
    """
    raw = config.get('trailing_stop') if isinstance(config, dict) else None
    if not isinstance(raw, dict):
        return {'enabled': False, 'steps': []}

    enabled = _trailing_enabled(raw.get('enabled', False))

    steps = raw.get('steps') or []
    if not isinstance(steps, (list, tuple)):
        steps = []

    normalized: List[Dict[str, float]] = []
    seen = set()
    for item in steps:
        step = _coerce_trailing_step(item)
        if step is None:
            continue
        key = (step['trigger'], step['stop'])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(step)
    normalized.sort(key=lambda step: (step['trigger'], step['stop']))
    return {'enabled': enabled, 'steps': normalized}


def validate_trailing_steps(
    steps: Any,
    *,
    enabled: bool = True,
    max_steps: Optional[int] = None,
) -> List[str]:
    """Stable reason codes explaining why a RAW step list must be rejected.

    An empty list means "accepted". The function never raises and never mutates its
    input, so the engine (#145), the API (#149), paper (#148) and the sandbox executor
    (#151) all reach the same verdict from one call.

    `enabled` only decides whether an empty ladder is a rejection (`trailing_disabled`):
    a disabled block with no steps is the shipped default. Bounds are checked even when
    `enabled` is False - a stored-but-disabled ladder is what #145 will arm, so invalid
    numbers must never reach the database.
    """
    reasons: List[str] = []
    if max_steps is None:
        max_steps = int(TRAILING_STOP['max_steps'])

    if steps is None or (isinstance(steps, (dict, list, tuple, str)) and len(steps) == 0):
        return [TRAILING_REASON_DISABLED] if enabled else reasons
    if not isinstance(steps, (list, tuple)):
        return [TRAILING_REASON_DISABLED] if enabled else reasons

    parsed: List[Dict[str, float]] = []
    for item in steps:
        step = _coerce_trailing_step(item)
        if step is None or not _trailing_step_in_bounds(step):
            reasons.append(TRAILING_REASON_STEP_INVALID)
            continue
        parsed.append(step)

    parsed.sort(key=lambda step: (step['trigger'], step['stop']))

    deduped: List[Dict[str, float]] = []
    for step in parsed:
        if deduped:
            last = deduped[-1]
            if last['trigger'] == step['trigger'] and last['stop'] == step['stop']:
                continue  # exact duplicate - normalization removes it, not an error
            if last['trigger'] == step['trigger']:
                # Same trigger twice with two different stops: no defined order.
                reasons.append(TRAILING_REASON_NOT_MONOTONIC)
        deduped.append(step)

    if len(deduped) > int(max_steps):
        reasons.append(TRAILING_REASON_TOO_MANY_STEPS)

    highest_stop = -math.inf
    for step in deduped:
        if step['stop'] < highest_stop:
            reasons.append(TRAILING_REASON_NOT_MONOTONIC)
            break
        highest_stop = max(highest_stop, step['stop'])

    return sorted(set(reasons))


def resolve_trailing_stop(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """One call for every consumer: {'enabled', 'steps', 'reasons'} for a strategy config.

    `reasons` is empty both for a usable ladder and for the "no trailing" defaults, so a
    caller only has to look at one list. Issue #144 section 5: a config without the key
    resolves to {'enabled': False, 'steps': [], 'reasons': []} - behaviour unchanged.
    """
    raw = config.get('trailing_stop') if isinstance(config, dict) else None
    if not isinstance(raw, dict):
        return {'enabled': False, 'steps': [], 'reasons': []}
    normalized = normalize_trailing_stop(config)
    reasons = validate_trailing_steps(raw.get('steps'), enabled=normalized['enabled'])
    return {'enabled': normalized['enabled'], 'steps': normalized['steps'], 'reasons': reasons}


def require_valid_trailing_stop(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """resolve_trailing_stop() that refuses to guess: raises ValueError on any reason code.

    Intended gate for the write paths (Lab editor #146, strategy create/update API #149).
    Nothing calls it in production yet - Issue #144 ships the contract only - so saving a
    malformed ladder still succeeds today; callers that cannot wait for #146/#149 must read
    `resolve_trailing_stop(config)['reasons']` themselves.
    """
    resolved = resolve_trailing_stop(config)
    if resolved['reasons']:
        raise ValueError(
            "invalid config.trailing_stop: " + ", ".join(resolved['reasons']))
    return {'enabled': resolved['enabled'], 'steps': resolved['steps']}


# Fallback only (used if trading.trading_universe is empty/unavailable).
# The canonical universe lives in the DB table; this is a safety net.
DEFAULT_UNIVERSE = [
    'RUAL', 'GMKN', 'PIKK', 'GAZP', 'SIBN',
    'SBER', 'LKOH', 'VTBR', 'ROSN', 'NVTK',
    'TATN', 'CHMF', 'ALRS', 'PLZL', 'MOEX',
]

# Issue #135 PO sandbox list, extended 2026-09-02 with FEES, GAZP, PLZL.
# Issue #66 ranking (SBER/LKOH/RUAL/NVTK/GAZP) remains historical in
# analytics/issue-66-live-universe/. Do not shrink trading.trading_universe.
LIVE_UNIVERSE = [
    'ROSN', 'IRAO', 'AFKS', 'NVTK', 'SBER', 'MTSS', 'PHOR', 'MOEX', 'FLOT',
    'FEES', 'GAZP', 'PLZL',
]
EXPECTED_LOCKED_STRATEGY = 'test_20260830_new_level'


def _unique_extend(base: List[str], extra: List[str]) -> List[str]:
    """Preserve base order, then append unseen names from extra."""
    seen = set(base)
    out = list(base)
    for ticker in extra:
        if ticker not in seen:
            seen.add(ticker)
            out.append(ticker)
    return out


def get_trading_universe(db=None, limit: Optional[int] = None) -> List[str]:
    """Traded tickers from trading.trading_universe (rank order). Falls back to DEFAULT_UNIVERSE."""
    if db is not None:
        try:
            df = db.select(
                "SELECT ticker FROM trading.trading_universe ORDER BY rank ASC, ticker ASC"
            ).to_dataframe()
            if not df.empty:
                tickers = [str(t) for t in df['ticker'].tolist()]
                return tickers[:limit] if limit else tickers
        except Exception:
            pass
    return DEFAULT_UNIVERSE[:limit] if limit else list(DEFAULT_UNIVERSE)


def get_live_trading_universe(db=None) -> List[str]:
    """Tickers allowed for sandbox live execution (PO list).

    Returns LIVE_UNIVERSE as configured. Names may sit outside the paper
    top-15; do not clip them against trading.trading_universe.
    """
    return list(LIVE_UNIVERSE)


def get_streaming_universe(db=None) -> List[str]:
    """Paper top-15 plus sandbox live names, without shrinking either list."""
    return _unique_extend(get_trading_universe(db), get_live_trading_universe(db))


# --- Strategy registry -------------------------------------------------------
# Canonical, validated strategies. Backtest (strategy_backtest) and paper trading
# (online_signals/paper_trader) reference these BY NAME so they stay consistent.
# A/B arms (signal_source/window_mode/entry_mode/rr_mode) are experimental overlays
# applied on top of a base strategy; the registry defines the base signal logic.
STRATEGIES: Dict[str, Dict[str, Any]] = {
    'levels_reversal_4hbuy': {
        'description': ('4h зона поддержки + активный 4h BUY-сигнал + подтверждение '
                        'разворота (10min выше зоны) + фильтр RR 1:2. Валидирована на '
                        'бэктесте (entry_mode=levels_ts1, confirm 10min, RR 2.0).'),
        'patterns': ['levels_reversal', 'signal_4h_buy'],
        'confirm_windows': [10],
        'commission_pct': 0.06,
        'slippage_pct': 0.0,
        'risk_reward': {'risk': 1.0, 'reward': 2.0},
        'entry_window': [7, 19],
    },
    'levels_reversal_base': {
        'description': ('4h зона поддержки + подтверждение разворота (10min) + RR 1:2, '
                        'БЕЗ 4h BUY-фильтра. Плечо А/Б против levels_reversal_4hbuy.'),
        'patterns': ['levels_reversal'],
        'confirm_windows': [10],
        'commission_pct': 0.06,
        'slippage_pct': 0.0,
        'risk_reward': {'risk': 1.0, 'reward': 2.0},
        'entry_window': [7, 19],
    },
}
DEFAULT_STRATEGY = 'levels_reversal_4hbuy'


def get_strategy(name: Optional[str] = None) -> Dict[str, Any]:
    """Return a strategy definition by name (default: DEFAULT_STRATEGY)."""
    name = name or DEFAULT_STRATEGY
    if name not in STRATEGIES:
        raise KeyError(f"Unknown strategy '{name}'. Available: {sorted(STRATEGIES)}")
    return dict(STRATEGIES[name])


def list_strategies() -> List[Dict[str, Any]]:
    """All registered strategies with their names (for UI / reporting)."""
    return [{'name': k, **v} for k, v in STRATEGIES.items()]
