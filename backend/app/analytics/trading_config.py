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
     defaults, normalization and validation; StrategyEvaluator applies it since #145);
  11) live equity risk gates (Issue #176 / Epic #172 task D - daily drawdown limit,
     absolute position-notional cap and open-position cap, all env-overridable);
  12) live alerting and heartbeat monitoring (Issue #177 / Epic #172 block E -
     Telegram heartbeat cadence, staleness window, alert debounce, slippage
     threshold and the metrics-flush contract, all env-overridable).


Every module (data_refresher, online_data, online_signals, paper_trader, strategy_backtest)
must import get_trading_universe() / get_strategy() from here instead of hardcoding
ticker lists or strategy parameters. LiveExecutor uses get_live_trading_universe().
"""
from __future__ import annotations
import math
import os
from typing import Any, Dict, List, Optional, Tuple


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
    # Issue #151: live trailing-stop runtime switches. Read once per
    # executor loop iteration from the in-memory copy (config_manager
    # refreshes from trading.app_settings when available).
    # trailing_kill_switch=true stops all new trailing arming and cancels
    # existing trailing stops on the next iteration.
    'trailing_kill_switch': False,
    # live_trailing_enabled=false disables trailing arming for new positions
    # but does not cancel already armed ladders.
    'live_trailing_enabled': True,
    # Issue #178: GLOBAL live kill switch - the emergency stop of the whole
    # live contour. true rejects every new entry (skip reason 'kill_switch')
    # while existing positions keep their broker-side protection: no flatten,
    # no stop cancellation.
    #
    # The in-memory default is deliberately the FAIL-SAFE value: a process that
    # has not read trading.app_settings.live_kill_switch yet (migration
    # 20260928_001 seeds it to false) must not enter. initialize() reads the
    # stored value before the first loop iteration, so a migrated database
    # starts with entries allowed and no "transition" alert.
    'live_kill_switch': True,
    # Number of protective ticks (min_price_increment) added to the stop
    # price when submitting a stop-triggered marketable sell limit.
    'trailing_protective_ticks': 5,
    # Optional narrowing of the live universe for trailing tests.
    # Empty list means all tickers from LIVE_UNIVERSE are eligible.
    'trailing_ticker_allowlist': [],
    # Issue #175: broker-side protection (real STOP_LOSS via PostStopOrder).
    # broker_stop_enabled=false falls back to the pre-#175 synthetic stop
    # (cancel take + marketable sell limit); it is a debug switch only.
    'broker_stop_enabled': True,
    # Base delay of the exponential backoff applied when PostStopOrder fails.
    # The position stays flagged protection_failed until a stop is armed.
    'protection_retry_seconds': 30,
    # How often armed stops are re-verified against GetStopOrders (seconds).
    'broker_stop_verify_interval_seconds': 60,
    # OCO: grace period after a close before orphaned stop/take orders are
    # cancelled manually (Product Owner decision of 2026-09-22).
    'oco_check_delay_seconds': 60,
    # OCO: verification attempts before the executor stops retrying.
    'oco_check_attempts': 3,
    # Fill reconciliation: GetOperations lookback window in hours.
    'operations_lookback_hours': 24,
    # Token-bucket tokens kept in reserve for protection calls so that new
    # entries can never starve stop arming / amend-trailing
    # (priority: protection > trailing > entry).
    'entry_token_reserve': 1.0,
    # Issue #199: account-wide sweep of orphaned broker stop orders. The OCO
    # pass of #175 only knows the legs of the closes *this* process performed,
    # so a stop orphaned by a restart, a failed DB close or a manual
    # intervention stays at the broker as a naked sell order. The sweep is the
    # safety net; false disables it and leaves the #175 behaviour untouched.
    'orphan_stop_sweep_enabled': True,
    # Minimum seconds between two sweeps (independent of check_interval_seconds:
    # the sweep is a reconciliation net, not a per-cycle pass).
    'orphan_stop_sweep_interval_seconds': 300,
    # Consecutive sweeps that must report the same orphan before it is
    # cancelled, so one inconsistent GetStopOrders/GetPositions pair cannot
    # remove protection.
    'orphan_stop_confirmations': 2,
    # A stop armed by this process less than this many seconds ago is never
    # swept: it is younger than any DB write that could reference it.
    'orphan_stop_grace_seconds': 900,
    # Hard cap of cancellations per sweep. More orphans than the cap means our
    # model of the account is wrong, so the sweep refuses to act (fail closed)
    # and raises a critical alert instead of emptying the stop book.
    'orphan_stop_max_cancels': 3,
    # Minimum seconds between two "fail closed" alerts while the condition lasts,
    # so a stuck sweep warns the operator without flooding Telegram.
    'orphan_stop_alert_interval_seconds': 3600,
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


# --- Issue #176: live equity risk gates --------------------------------------
# Absolute risk limits for the live contour (sandbox today, real account in
# #178). They deliberately do NOT live in config_manager.RiskConfig: that object
# is the *paper* risk policy read from config/settings.yaml and consumed by
# paper_trader.write_equity(), and Epic #172 task D forbids touching the paper
# contour. Trading policy belongs here (see the module docstring), so paper and
# live each keep one clearly named source of truth.
#
# Every limit is overridable per environment without a code change:
#   MAX_DAILY_LOSS_PCT, MAX_POSITION_SIZE, MAX_OPEN_POSITIONS,
#   LIVE_EQUITY_SNAPSHOT.
# An unparsable or out-of-range override raises ValueError at read time, so a
# typo in .env fails fast instead of silently disabling a risk gate in
# production (LiveExecutor.__init__ -> _validate_config surfaces it).
LIVE_RISK: Dict[str, Any] = {
    # Max intraday drawdown in percent of the *daily* peak equity. Reaching it
    # blocks new entries (skip reason 'risk_breach') until the next MSK trading
    # day or a manual reset; existing stops are preserved and re-armed. The
    # default mirrors the paper limit already running in production.
    'max_daily_loss_pct': 2.0,
    # Max notional of a single position in RUB (size_lots * lot_size * price).
    # An absolute cap on top of the relative POSITION_SIZING['max_position_pct']
    # budget, so a growing account cannot grow one position without bound.
    'max_position_size': 100_000,
    # Write one trading.live_equity row per executor cycle. Turning this off
    # also disables the drawdown gate (there is nothing to gate on), which
    # makes it the dry-run switch for the whole risk contour.
    'equity_snapshot_enabled': True,
    # trading.app_settings key the operator sets to true to clear an active
    # breach. The executor consumes it once and writes false back, following
    # the trailing_kill_switch pattern from migration 20260916_001.
    'risk_breach_reset_key': 'live_risk_breach_reset',
}

# Acceptable ranges. Float limits are (exclusive_low, inclusive_high]; the
# integer limit is inclusive on both ends. Kept next to LIVE_RISK so a default
# and its bound cannot drift apart unnoticed.
MAX_DAILY_LOSS_PCT_RANGE: Tuple[float, float] = (0.0, 100.0)
MAX_POSITION_SIZE_RANGE: Tuple[float, float] = (0.0, 1.0e12)
MAX_OPEN_POSITIONS_RANGE: Tuple[int, int] = (1, 100)

LIVE_RISK_BOUNDS: Dict[str, Tuple[float, float]] = {
    'max_daily_loss_pct': MAX_DAILY_LOSS_PCT_RANGE,
    'max_position_size': MAX_POSITION_SIZE_RANGE,
    'max_open_positions': MAX_OPEN_POSITIONS_RANGE,
}

# env variable -> config key. MAX_OPEN_POSITIONS resolves into LIVE_TRADING
# (its single source of truth); the other two resolve into LIVE_RISK.
LIVE_RISK_ENV: Dict[str, str] = {
    'MAX_DAILY_LOSS_PCT': 'max_daily_loss_pct',
    'MAX_POSITION_SIZE': 'max_position_size',
    'MAX_OPEN_POSITIONS': 'max_open_positions',
}

_TRUTH_WORDS = ('1', 'true', 'yes', 'on')
_FALSE_WORDS = ('0', 'false', 'no', 'off')


def _env_raw(env_name: str) -> Optional[str]:
    """Return a non-empty stripped env value, or None when unset/blank."""
    raw = os.getenv(env_name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _env_bounded_float(
    env_name: str,
    key: str,
    default: float,
    bounds: Dict[str, Tuple[float, float]],
) -> float:
    """Read a float from env and validate it against ``bounds[key]``.

    Shared by the live risk gates (#176) and the live alerting policy (#177) so
    both keep one message shape and both fail fast on a typo in ``.env``.
    """
    raw = _env_raw(env_name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{env_name} must be a number, got {raw!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"{env_name} must be a finite number, got {raw!r}")
    low, high = bounds[key]
    if not low < value <= high:
        raise ValueError(
            f"{env_name} must be within ({low:g}, {high:g}], got {value:g}"
        )
    return value


def _env_risk_float(env_name: str, key: str, default: float) -> float:
    """Read a float risk limit from env, validating it against its range."""
    return _env_bounded_float(env_name, key, default, LIVE_RISK_BOUNDS)


def _env_bounded_int(
    env_name: str,
    key: str,
    default: int,
    bounds: Dict[str, Tuple[float, float]],
) -> int:
    """Read an integer from env and validate it against ``bounds[key]``."""
    raw = _env_raw(env_name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{env_name} must be an integer, got {raw!r}") from None
    low, high = bounds[key]
    if not low <= value <= high:
        raise ValueError(f"{env_name} must be within [{low}, {high}], got {value}")
    return value


def _env_risk_int(env_name: str, key: str, default: int) -> int:
    """Read an integer risk limit from env, validating it against its range."""
    return _env_bounded_int(env_name, key, default, LIVE_RISK_BOUNDS)


def _env_bool(env_name: str, default: bool) -> bool:
    """Read a boolean switch from env using the project's truth words."""
    raw = _env_raw(env_name)
    if raw is None:
        return default
    return raw.lower() in _TRUTH_WORDS


def _env_strict_bool(env_name: str, default: bool) -> bool:
    """Read a safety-critical boolean from env, rejecting ambiguous values.

    Issue #178. :func:`_env_bool` maps an unknown word to the default, which is
    the right behaviour for a tunable. For the switch that decides whether real
    money may be traded an unparsable value must fail fast instead of silently
    picking a contour: ``ALLOW_REAL_TRADING=ture`` has to stop the process, not
    quietly mean "sandbox" (or worse, "real").

    Raises:
        ValueError: when the variable is set to something that is neither a
            truth word nor a false word.
    """
    raw = _env_raw(env_name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _TRUTH_WORDS:
        return True
    if lowered in _FALSE_WORDS:
        return False
    raise ValueError(
        f"{env_name} must be one of "
        f"{', '.join(_TRUTH_WORDS + _FALSE_WORDS)}, got {raw!r}"
    )


def get_live_risk_config() -> Dict[str, Any]:
    """Return an isolated copy of the live equity risk-gate policy.

    Defaults come from :data:`LIVE_RISK`; ``MAX_DAILY_LOSS_PCT``,
    ``MAX_POSITION_SIZE`` and ``LIVE_EQUITY_SNAPSHOT`` override them per
    environment. Raises ``ValueError`` on an unparsable or out-of-range
    override so a broken limit can never reach the executor silently.
    """
    config = dict(LIVE_RISK)
    config['max_daily_loss_pct'] = _env_risk_float(
        'MAX_DAILY_LOSS_PCT',
        'max_daily_loss_pct',
        float(LIVE_RISK['max_daily_loss_pct']),
    )
    config['max_position_size'] = _env_risk_float(
        'MAX_POSITION_SIZE',
        'max_position_size',
        float(LIVE_RISK['max_position_size']),
    )
    config['equity_snapshot_enabled'] = _env_bool(
        'LIVE_EQUITY_SNAPSHOT',
        bool(LIVE_RISK['equity_snapshot_enabled']),
    )
    return config


def get_live_risk_bounds() -> Dict[str, Tuple[float, float]]:
    """Expose the validated ranges (diagnostics and API schema, Issue #176)."""
    return dict(LIVE_RISK_BOUNDS)


# --- Issue #177: live alerting, heartbeat and operator monitoring -------------
# Notification policy for the live contour. Like LIVE_RISK it lives here (not in
# config_manager) because it is trading policy, and like LIVE_RISK every value
# is env-overridable and range-validated so a typo in .env fails fast instead of
# silently muting an operator alert.
#
# Secrets are NOT part of this section: the Telegram credentials come from
# config_manager.load_settings().telegram (TGM_TOKEN / TGM_CHAT_ID or
# backend/config/settings.yaml), which TelegramNotifier already reads.
LIVE_ALERTING: Dict[str, Any] = {
    # Send one Telegram heartbeat every N seconds while the loop is alive. The
    # heartbeat is the "process is alive" signal an external monitor watches.
    'heartbeat_interval_seconds': 3600,
    # A heartbeat older than this makes the monitoring API report
    # heartbeat_stale=true. Deliberately shorter than the interval above would
    # be a permanent alert, so the default pairs 3600s sends with a 300s
    # staleness window only meaningful right after a crash or a missed send.
    'heartbeat_stale_seconds': 300,
    # Minimum gap between two alerts sharing one dedup key. Repeating criticals
    # (protection_failed, invariant violation, equity snapshot errors) are
    # debounced; rare one-shot events bypass it (decision D2).
    'alert_debounce_seconds': 300,
    # Exit slippage worth waking the operator for, in basis points.
    'slippage_alert_bp': 50.0,
    # Consecutive loop errors that both stop the executor and alert. Decision
    # D3: this key replaces the hardcoded MAX_CONSECUTIVE_ERRORS module
    # constant as the single source of truth (default keeps the old behaviour).
    'max_consecutive_errors': 5,
    # Persist the metrics snapshot into trading.app_settings at most every N
    # seconds, plus on heartbeat, kill-switch transition and graceful shutdown
    # (decision D5 - writing every 30s cycle would be ~2880 UPDATE/day).
    'metrics_flush_seconds': 300,
    # trading.app_settings key holding the JSONB metrics snapshot. The executor
    # writes it, GET /api/live-trading/metrics reads it (decision D1).
    'metrics_key': 'live_executor_metrics',
    # Master switch. false keeps every event in the logs only, which is the
    # safe default for a local run without a Telegram chat.
    'telegram_alerts_enabled': True,
}

# Acceptable ranges, same convention as LIVE_RISK_BOUNDS: float keys are
# (exclusive_low, inclusive_high], integer keys are inclusive on both ends.
LIVE_ALERTING_BOUNDS: Dict[str, Tuple[float, float]] = {
    'heartbeat_interval_seconds': (1.0, 86400.0),
    'heartbeat_stale_seconds': (1.0, 86400.0),
    'alert_debounce_seconds': (0.0, 86400.0),
    'slippage_alert_bp': (0.0, 10000.0),
    'max_consecutive_errors': (1.0, 100.0),
    'metrics_flush_seconds': (1.0, 86400.0),
}

# env variable -> LIVE_ALERTING key. ``telegram_alerts_enabled`` and
# ``metrics_key`` are intentionally absent: the first is a plain boolean switch
# handled by _env_bool, the second is a contract name, not a tunable.
LIVE_ALERTING_ENV: Dict[str, str] = {
    'LIVE_HEARTBEAT_INTERVAL_SECONDS': 'heartbeat_interval_seconds',
    'LIVE_HEARTBEAT_STALE_SECONDS': 'heartbeat_stale_seconds',
    'LIVE_ALERT_DEBOUNCE_SECONDS': 'alert_debounce_seconds',
    'LIVE_SLIPPAGE_ALERT_BP': 'slippage_alert_bp',
    'LIVE_MAX_CONSECUTIVE_ERRORS': 'max_consecutive_errors',
    'LIVE_METRICS_FLUSH_SECONDS': 'metrics_flush_seconds',
}


def get_live_alerting_config() -> Dict[str, Any]:
    """Return an isolated copy of the live alerting policy (Issue #177).

    Defaults come from :data:`LIVE_ALERTING`; every key in
    :data:`LIVE_ALERTING_ENV` is overridable per environment and
    ``LIVE_TELEGRAM_ALERTS`` toggles the master switch. Raises ``ValueError`` on
    an unparsable or out-of-range override, exactly like
    :func:`get_live_risk_config`, so a broken threshold can never reach the
    executor silently.
    """
    config = dict(LIVE_ALERTING)
    for env_name, key in LIVE_ALERTING_ENV.items():
        default = config[key]
        # bool before int: bool is a subclass of int in Python.
        if isinstance(default, bool):
            config[key] = _env_bool(env_name, default)
        elif isinstance(default, int):
            config[key] = _env_bounded_int(
                env_name, key, default, LIVE_ALERTING_BOUNDS
            )
        else:
            config[key] = _env_bounded_float(
                env_name, key, float(default), LIVE_ALERTING_BOUNDS
            )
    config['telegram_alerts_enabled'] = _env_bool(
        'LIVE_TELEGRAM_ALERTS',
        bool(LIVE_ALERTING['telegram_alerts_enabled']),
    )
    return config


def get_live_alerting_bounds() -> Dict[str, Tuple[float, float]]:
    """Expose the validated alerting ranges (diagnostics and API schema)."""
    return dict(LIVE_ALERTING_BOUNDS)


def validate_live_alerting_values(values: Dict[str, Any]) -> None:
    """Validate an in-memory alerting override (Issue #177).

    ``LiveExecutor`` accepts an ``alerting=`` dict so the tests (and a future
    settings screen) can retune the policy per process. This guard makes such a
    dict obey exactly the ranges :data:`LIVE_ALERTING_BOUNDS` enforces on
    ``.env``, so a typo can never silently mute an operator alert or set a
    nonsense debounce window. The expected type comes from the
    :data:`LIVE_ALERTING` default rather than from the value passed in: an
    ``int`` ``0`` for the float key ``slippage_alert_bp`` is still out of range.
    Missing keys and ``None`` are allowed - they fall back to the defaults.

    Raises:
        ValueError: on a wrong type, a non-finite number or an out-of-range
            value. The message always names the offending key.
    """
    for key, default in LIVE_ALERTING.items():
        if key not in values or values[key] is None:
            continue
        value = values[key]
        # bool before int: bool is a subclass of int in Python.
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be a boolean, got {value!r}")
            continue
        if key not in LIVE_ALERTING_BOUNDS:
            if key == 'metrics_key':
                # Contract name, not a tunable; bounded by the VARCHAR(64)
                # trading.app_settings.key column it is stored under.
                text = str(value)
                if not text.strip() or len(text) > 64:
                    raise ValueError(
                        f"{key} must be 1..64 characters, got {len(text)}"
                    )
            continue
        if isinstance(value, bool):
            raise ValueError(f"{key} must be a number, got {value!r}")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number, got {value!r}") from None
        if not math.isfinite(number):
            raise ValueError(f"{key} must be a finite number, got {value!r}")
        low, high = LIVE_ALERTING_BOUNDS[key]
        if isinstance(default, int):
            if not low <= number <= high:
                raise ValueError(
                    f"{key} must be within [{low:g}, {high:g}], got {number:g}"
                )
        elif not low < number <= high:
            raise ValueError(
                f"{key} must be within ({low:g}, {high:g}], got {number:g}"
            )




def get_live_trading_config() -> Dict[str, Any]:
    """Return the complete sandbox live-executor policy.

    Issue #176: ``max_open_positions`` keeps ``LIVE_TRADING`` as its single
    source of truth (the executor gate in ``process_signal`` already reads it)
    and only gains the ``MAX_OPEN_POSITIONS`` env override, resolved here.
    """
    config: Dict[str, Any] = {
        'risk_per_trade_pct': POSITION_SIZING['risk_per_trade_pct'],
        'max_position_pct': POSITION_SIZING['max_position_pct'],
        **LIVE_TRADING,
    }
    config['max_open_positions'] = _env_risk_int(
        'MAX_OPEN_POSITIONS',
        'max_open_positions',
        int(LIVE_TRADING['max_open_positions']),
    )
    return config


# Secrets and the sandbox account id are intentionally loaded by config_manager from
# TINVEST_SANDBOX / TINVEST_SANDBOX_ACC. TINVEST_TOKEN / TINVEST_ACC remain
# market-data-only. The REAL contour (Issue #178) reads TINVEST_LIVE_TOKEN /
# TINVEST_LIVE_ACC through the same config_manager, so no secret lives here.
# Only non-secret execution policy lives here.
SANDBOX_TRADING: Dict[str, Any] = {
    'enabled': True,
    # Red line: this stays False in code. It is the single source of truth for
    # which contour the executor trades on and can only be turned on per
    # deployment through ALLOW_REAL_TRADING (see get_sandbox_trading_config).
    'allow_real_trading': False,
    'initial_capital_rub': 50_000,
    'default_currency': 'rub',
    'retry_attempts': 3,
    'retry_base_delay_seconds': 0.5,
    'discover_account_when_missing': True,
    # Issue #192: red line - stays False in code. Only ALLOW_LIVE_TOKEN_REUSE on
    # the deployment may let TINVEST_LIVE_TOKEN hold the same secret as the
    # market-data TINVEST_TOKEN (one physical token for both contours). It never
    # introduces a fallback between contours: each client still reads its own
    # variable only.
    'allow_live_token_reuse': False,
}


def get_sandbox_trading_config() -> Dict[str, Any]:
    """Return an isolated copy of the T-Bank execution policy.

    Issue #178: ``allow_real_trading`` keeps :data:`SANDBOX_TRADING` as its
    single source of truth (``False`` in code) and gains the
    ``ALLOW_REAL_TRADING`` env override, resolved here. The override is parsed
    strictly: an unparsable value raises ``ValueError`` at read time so a typo in
    ``.env`` can never silently select a contour.

    The retry policy, the default currency and the account-discovery switch in
    this dict are shared by both clients - :class:`TinkoffLiveClient` is the
    mirror of :class:`TinkoffSandboxClient`, not a second policy.

    Issue #192: ``allow_live_token_reuse`` follows exactly the same pattern -
    ``False`` in code, ``ALLOW_LIVE_TOKEN_REUSE`` env override, strict parsing.
    """
    config = dict(SANDBOX_TRADING)
    config['allow_real_trading'] = _env_strict_bool(
        'ALLOW_REAL_TRADING',
        bool(SANDBOX_TRADING['allow_real_trading']),
    )
    config['allow_live_token_reuse'] = _env_strict_bool(
        'ALLOW_LIVE_TOKEN_REUSE',
        bool(SANDBOX_TRADING['allow_live_token_reuse']),
    )
    return config


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
# Since #145 the production engine reads this block: StrategyEvaluator, the levels_reversal
# plugin and the portfolio simulator arm the ladder through app.analytics.trailing_stop,
# which calls resolve_trailing_stop() below. The API surfaces the reason codes in #149,
# paper in #148 and the sandbox executor in #151. This repo has no validate_config() to hook
# into, so a malformed ladder is still accepted at save time until the #146 editor and the
# #149 API gate on require_valid_trailing_stop(); the engine refuses to arm such a ladder
# (fail-safe) and manages the position exactly as it did before #145. A config without the
# key (or with
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

# The stable vocabulary the Lab editor (Issue #146) surfaces to the operator. Ordered
# from "nothing to arm" to "too long" so a multi-code rejection reads as a narrative.
TRAILING_REASON_CODES: List[str] = [
    TRAILING_REASON_DISABLED,
    TRAILING_REASON_STEP_INVALID,
    TRAILING_REASON_NOT_MONOTONIC,
    TRAILING_REASON_TOO_MANY_STEPS,
]

# Name of the approved ladder shipped in TRAILING_STOP['steps'] - the `ultra_late_tight`
# grid of the 143-trailing-v3 lattice (Product Owner decision of 2026-09-08, Epic #142).
# It is a LABEL, not a bound: the numbers themselves stay only in TRAILING_STOP. The Lab
# prints it on the "apply default grid" button so the operator can cross-check the button
# against #144/#142 without the frontend ever restating a step. backend/tests/
# test_trailing_contract.py pins this name to the grid of the same id in
# analytics/issue-143-trailing-robustness/grids.json, so the label cannot drift from it.
TRAILING_STOP_DEFAULT_GRID: str = 'ultra_late_tight'

# Keystroke resolution the editor must offer, in R. The approved default lives on a 0.1R
# gap (1.9 / 2.4 / 2.9), so snapping an input to 0.5R or 1.0R would silently rewrite the
# shipped ladder into something validate_trailing_steps() rejects. Served to the Lab
# (Issue #146) for exactly that reason; a label, not a bound.
TRAILING_INPUT_STEP: float = 0.1


def get_trailing_stop_config() -> Dict[str, Any]:
    """Return an isolated copy of the trailing-stop contract (defaults + bounds)."""
    cfg = dict(TRAILING_STOP)
    cfg['steps'] = [dict(step) for step in TRAILING_STOP['steps']]
    return cfg


def get_trailing_stop_schema() -> Dict[str, Any]:
    """The whole trailing-stop contract for the schema-driven Lab editor (Issue #146).

    `get_trailing_stop_config()` plus the two labels the editor needs to render honestly:
    the name of the approved default grid (button caption) and the R keystroke resolution
    an input must accept without snapping. Bounds and steps are the contract's own values
    - this function is the only place the Lab is allowed to read them from, which is what
    keeps `frontend/.../TrailingStopSection.tsx` free of trailing numbers.

    Additive: TRAILING_STOP itself is untouched, so every existing consumer of
    get_trailing_stop_config() sees exactly the keys it saw before.
    """
    schema = get_trailing_stop_config()
    schema['default_grid'] = TRAILING_STOP_DEFAULT_GRID
    schema['input_step'] = TRAILING_INPUT_STEP
    schema['reason_codes'] = list(TRAILING_REASON_CODES)
    return schema


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
    (trailing_stop.py since #145), the API (#149) and the paper/live paths can call it on
    every run.

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
    input, so the engine (trailing_stop.py since #145), the API (#149), paper (#148) and
    the sandbox executor (#151) all reach the same verdict from one call.

    `enabled` only decides whether an empty ladder is a rejection (`trailing_disabled`):
    a disabled block with no steps is the shipped default. Bounds are checked even when
    `enabled` is False - a stored-but-disabled ladder is what the engine arms the moment
    the flag flips, so invalid numbers must never reach the database.
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
    Nothing calls it in production yet - saving a malformed ladder still succeeds today -
    but the #145 engine reads `resolve_trailing_stop(config)['reasons']` and refuses to arm
    a ladder the validator rejects, so a malformed block changes no exits.
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
