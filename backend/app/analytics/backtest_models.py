"""
Backtest contract: shared structures and constants for the deterministic
backtest engine (BT-2..BT-5). Pure definitions; no I/O, no side effects.

Conventions locked with the user (see discussion before task-049/050):
- Entry price = open of the bar AFTER the signal bar (open_next). No look-ahead.
- Long-only: BUY opens a long (if flat); SELL closes a long (else no-op).
- Commission 0.03% per side (round-trip 0.06%); slippage modelled separately.
- Stop/take checked against bar high/low; holding/signal exit against close.
- If stop and take both hit in one bar -> stop wins (conservative).
- A group with fewer than MIN_TRADES_PER_GROUP trades is flagged unreliable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# --- locked parameters / thresholds ---
DEFAULT_COMMISSION_PER_SIDE: float = 0.0003      # 0.03% per side
DEFAULT_EXCHANGE_FEE_PER_SIDE: float = 0.0       # add MOEX exchange fee later if needed
DEFAULT_SLIPPAGE_TICKS: int = 0                  # optimistic; run a 1-tick sensitivity pass
MIN_TRADES_PER_GROUP: int = 30                   # below this -> reliable=False
ENTRY_RULE: str = "open_next"                    # the only entry mode in v1
SIDE_LONG: str = "LONG"
SIGNAL_BUY: str = "BUY"
SIGNAL_SELL: str = "SELL"

# exit reasons (closed set; engine must emit only these)
EXIT_STOP: str = "stop"
EXIT_TAKE: str = "take"
EXIT_HOLDING: str = "holding"
EXIT_SIGNAL: str = "signal"
EXIT_SESSION: str = "session"
EXIT_UNTRADEABLE: str = "untradeable"   # signal on last bar (no T+1) -> skipped, not an error

VALID_EXIT_REASONS = {EXIT_STOP, EXIT_TAKE, EXIT_HOLDING, EXIT_SIGNAL, EXIT_SESSION}

# default exit-rule matrix (BT-3 sweeps over these)
DEFAULT_HOLDINGS: List[int] = [3, 6, 12]
DEFAULT_STOP_ATR: List[float] = [1.0, 2.0]
DEFAULT_TAKE_ATR: List[float] = [1.0, 2.0]
DEFAULT_TIMEFRAMES: List[str] = ["1h", "4h"]     # 30min expected to die on costs; run for honesty


@dataclass
class ExitRule:
    holding_bars: int = 6
    stop_atr: float = 2.0
    take_atr: float = 2.0
    session_only: bool = True          # do not hold intraday positions overnight/weekend
    stop_wins_on_tie: bool = True      # both stop+take in one bar -> stop


@dataclass
class BacktestParams:
    strategy_name: str
    entry_rule: str = ENTRY_RULE
    exit_rule: ExitRule = field(default_factory=ExitRule)
    commission_per_side: float = DEFAULT_COMMISSION_PER_SIDE
    exchange_fee_per_side: float = DEFAULT_EXCHANGE_FEE_PER_SIDE
    slippage_ticks: int = DEFAULT_SLIPPAGE_TICKS
    timeframes: List[str] = field(default_factory=lambda: list(DEFAULT_TIMEFRAMES))
    universe_report_date: Optional[str] = None    # top_stocks_by_volume.report_date (fixed universe)
    selection_bias: bool = True                   # fixed top-30 -> bias flag on every report
    min_trades_per_group: int = MIN_TRADES_PER_GROUP
    initial_capital: float = 1_000_000.0
    signal_exit: bool = True
    signal_exit_min_total: int = 1  # min total_signals for exit signal (1=any)  # use SELL signals as exit trigger (default True for backward compat)   # base for equity curve & drawdown (v1 fixed 1 lot)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "entry_rule": self.entry_rule,
            "exit_rule": {
                "holding_bars": self.exit_rule.holding_bars,
                "stop_atr": self.exit_rule.stop_atr,
                "take_atr": self.exit_rule.take_atr,
                "session_only": self.exit_rule.session_only,
                "stop_wins_on_tie": self.exit_rule.stop_wins_on_tie,
            },
            "commission_per_side": self.commission_per_side,
            "exchange_fee_per_side": self.exchange_fee_per_side,
            "slippage_ticks": self.slippage_ticks,
            "timeframes": list(self.timeframes),
            "universe_report_date": self.universe_report_date,
            "selection_bias": self.selection_bias,
            "min_trades_per_group": self.min_trades_per_group,
            "initial_capital": self.initial_capital,
            "signal_exit": self.signal_exit,
            "signal_exit_min_total": self.signal_exit_min_total,
            "extra": dict(self.extra),
        }


@dataclass
class Trade:
    run_id: int
    ticker: str
    figi: Optional[str]
    timeframe: str
    signal_id: Optional[int]
    pattern_name: Optional[str]
    side: str
    entry_ts: str
    entry_price: float
    exit_ts: str
    exit_price: float
    exit_reason: str
    bars_held: int
    gross_return_pct: float
    commission_pct: float
    slippage_pct: float
    net_return_pct: float
    pnl_rub: float
    lot_size: int
    min_price_increment: float


@dataclass
class EquityPoint:
    run_id: int
    ts: str
    equity_rub: float
    drawdown_pct: float


@dataclass
class GroupMetrics:
    run_id: int
    group_key: str
    n_trades: int
    win_rate: float
    profit_factor: float
    expectancy: float
    sharpe: float
    sortino: float
    max_drawdown: float
    avg_bars_held: float
    reliable: bool
    benchmark_buyhold_return_pct: float
    benchmark_random_return_pct: float
