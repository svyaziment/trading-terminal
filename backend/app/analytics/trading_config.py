"""
Central trading configuration - SINGLE SOURCE OF TRUTH for:
  1) the traded universe (tickers) - read from trading.trading_universe (rank order);
  2) strategy definitions (name -> params) - so backtest and paper trading never diverge;
  3) T-Bank sandbox execution and retry policy;
  4) live order-book imbalance defaults.

Every module (data_refresher, online_data, online_signals, paper_trader, strategy_backtest)
must import get_trading_universe() / get_strategy() from here instead of hardcoding
ticker lists or strategy parameters.
"""
from __future__ import annotations
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


# Fallback only (used if trading.trading_universe is empty/unavailable).
# The canonical universe lives in the DB table; this is a safety net.
DEFAULT_UNIVERSE = [
    'RUAL', 'GMKN', 'PIKK', 'GAZP', 'SIBN',
    'SBER', 'LKOH', 'VTBR', 'ROSN', 'NVTK',
    'TATN', 'CHMF', 'ALRS', 'PLZL', 'MOEX',
]


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
