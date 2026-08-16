"""Portfolio backtest simulator — shared capital, slot limits, StrategyPlugin.

Simulates a single portfolio (default 50k RUB) across multiple tickers. Per-ticker
signals come from ``portfolio_backtest.run_portfolio_backtest`` (StrategyPlugin);
portfolio constraints (5 slots × 10k, volume priority, GAME OVER) are applied via
trade replay.

Usage:
    python -m app.analytics.portfolio_simulator --mode dev
    python -m app.analytics.portfolio_simulator --mode full --output reports/full_run.json

Part of Issue #37 (Epic #39).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.db.db_manager import DBManager
from app.analytics.paper_strategy import get_active_paper_strategy
from app.analytics.pattern_registry import normalize_patterns
from app.analytics.portfolio_backtest import run_portfolio_backtest
from app.analytics.strategies.registry import register_default_strategies

logger = logging.getLogger(__name__)

DEFAULT_CAPITAL = 50_000.0
DEFAULT_SLOT_SIZE = 10_000.0
DEFAULT_MAX_POSITIONS = 5

MODE_PRESETS = {
    "dev": {
        "date_from": "2026-07-01",
        "date_to": "2026-08-01",
        "max_tickers": 5,
    },
    "full": {
        "date_from": "2024-08-01",
        "date_to": "2026-08-15",
        "max_tickers": None,
    },
}


def resolve_strategy_name(config: Dict[str, Any]) -> str:
    """Pick registry plugin name from config."""
    explicit = config.get("strategy_name")
    if explicit:
        return str(explicit)
    patterns = config.get("patterns", [])
    if isinstance(patterns, dict):
        if "atr_reversal" in patterns:
            return "atr_reversal"
    elif isinstance(patterns, list):
        if "atr_reversal" in patterns:
            return "atr_reversal"
    return "levels_reversal"


def get_tickers_by_volume(
    db: DBManager,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    max_tickers: Optional[int] = None,
) -> List[str]:
    """All tickers from candles_1min_raw sorted by total volume (desc)."""
    q = (
        "SELECT ticker, SUM(COALESCE(volume, 0)) AS total_volume "
        "FROM trading.candles_1min_raw WHERE 1=1"
    )
    params: List[Any] = []
    if date_from:
        q += " AND timestamp >= %s"
        params.append(date_from)
    if date_to:
        q += " AND timestamp < %s"
        params.append(date_to)
    q += " GROUP BY ticker ORDER BY total_volume DESC NULLS LAST, ticker"
    if max_tickers:
        q += f" LIMIT {int(max_tickers)}"
    df = db.select(q, tuple(params) if params else None).to_dataframe()
    return df["ticker"].tolist() if not df.empty else []


def _portfolio_metrics(trades: List[Dict], equity_curve: List[Dict], initial_capital: float) -> Dict:
    if not trades:
        final = equity_curve[-1]["equity_rub"] if equity_curve else initial_capital
        return {
            "n_trades": 0,
            "win_rate": None,
            "profit_factor": None,
            "max_drawdown_pct": 0.0,
            "final_equity_rub": round(final, 2),
            "pnl_rub": round(final - initial_capital, 2),
            "pnl_pct": round((final / initial_capital - 1) * 100, 2),
        }

    pnls = [t["pnl_rub"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gw = sum(wins) if wins else 0.0
    gl = abs(sum(losses)) if losses else 0.0
    pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else None)

    eq_vals = [e["equity_rub"] for e in equity_curve] if equity_curve else [initial_capital]
    peak = eq_vals[0]
    max_dd = 0.0
    for eq in eq_vals:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100.0)

    final = eq_vals[-1]
    return {
        "n_trades": len(trades),
        "win_rate": round(len(wins) / len(pnls) * 100, 1),
        "profit_factor": round(pf, 2) if pf is not None and pf != float("inf") else pf,
        "max_drawdown_pct": round(max_dd, 2),
        "final_equity_rub": round(final, 2),
        "pnl_rub": round(final - initial_capital, 2),
        "pnl_pct": round((final / initial_capital - 1) * 100, 2),
    }


def _replay_portfolio_trades(
    candidates: List[Dict[str, Any]],
    volume_rank: Dict[str, int],
    initial_capital: float,
    slot_size: float,
    max_positions: int,
) -> Tuple[List[Dict], List[Dict], bool, Optional[str], int]:
    """Apply portfolio capital/slot rules to per-ticker trade candidates."""
    by_entry: Dict[pd.Timestamp, List[Dict[str, Any]]] = defaultdict(list)
    for cand in candidates:
        by_entry[pd.Timestamp(cand["entry_ts"])].append(cand)
    entry_times = sorted(by_entry.keys())

    cash = float(initial_capital)
    active: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []
    skipped = 0
    game_over = False
    game_over_ts: Optional[str] = None

    def _equity() -> float:
        return cash + sum(a["allocated_rub"] for a in active)

    def _record(ts: pd.Timestamp) -> None:
        equity_curve.append({
            "ts": str(ts),
            "equity_rub": round(_equity(), 2),
            "cash_rub": round(cash, 2),
            "open_positions": len(active),
        })

    def _settle(pos: Dict[str, Any]) -> None:
        nonlocal cash, game_over, game_over_ts
        net_pct = float(pos["net_return_pct"])
        pnl_rub = pos["allocated_rub"] * net_pct / 100.0
        cash += pos["allocated_rub"] + pnl_rub
        trades.append({
            "ticker": pos["ticker"],
            "entry_ts": pos["entry_ts"],
            "exit_ts": pos["exit_ts"],
            "entry_price": pos["entry_price"],
            "exit_price": pos["exit_price"],
            "exit_reason": pos["exit_reason"],
            "allocated_rub": round(pos["allocated_rub"], 2),
            "net_return_pct": pos["net_return_pct"],
            "pnl_rub": round(pnl_rub, 2),
            "bars_held": pos.get("bars_held"),
        })

    if entry_times:
        _record(entry_times[0])
    else:
        _record(pd.Timestamp.utcnow())

    for ts in entry_times:
        remaining: List[Dict[str, Any]] = []
        for pos in active:
            if pd.Timestamp(pos["exit_ts"]) <= ts:
                _settle(pos)
                _record(pd.Timestamp(pos["exit_ts"]))
                if cash <= 0:
                    game_over = True
                    game_over_ts = str(pos["exit_ts"])
                    break
            else:
                remaining.append(pos)
        active = remaining
        if game_over:
            break

        batch = sorted(
            by_entry[ts],
            key=lambda t: volume_rank.get(t["ticker"], 9999),
        )
        for cand in batch:
            if len(active) >= max_positions:
                skipped += 1
                continue
            if any(a["ticker"] == cand["ticker"] for a in active):
                skipped += 1
                continue
            if cash < 1.0:
                skipped += 1
                continue
            allocated = min(slot_size, cash)
            if allocated <= 0:
                skipped += 1
                continue
            cash -= allocated
            active.append({**cand, "allocated_rub": allocated})

    if not game_over:
        while active and not game_over:
            pos = active.pop(0)
            _settle(pos)
            _record(pd.Timestamp(pos["exit_ts"]))
            if cash <= 0:
                game_over = True
                game_over_ts = str(pos["exit_ts"])

    return trades, equity_curve, game_over, game_over_ts, skipped


def run_portfolio_simulation(
    db: DBManager,
    strategy_name: str,
    config: Dict[str, Any],
    tickers: List[str],
    *,
    initial_capital: float = DEFAULT_CAPITAL,
    slot_size: float = DEFAULT_SLOT_SIZE,
    max_positions: int = DEFAULT_MAX_POSITIONS,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    mode: str = "dev",
) -> Dict[str, Any]:
    """Run shared-capital portfolio simulation across tickers."""
    register_default_strategies()
    config = normalize_patterns(config)
    volume_rank = {t: i for i, t in enumerate(tickers)}

    candidates: List[Dict[str, Any]] = []
    loaded: List[str] = []
    failed: List[Dict[str, str]] = []

    for i, ticker in enumerate(tickers, 1):
        logger.info("Backtest ticker %s (%d/%d)", ticker, i, len(tickers))
        pr = run_portfolio_backtest(
            db, strategy_name, config, [ticker],
            date_from=date_from, date_to=date_to,
        )
        tr = (pr.get("results") or [{}])[0]
        if tr.get("status") != "success":
            failed.append({"ticker": ticker, "error": tr.get("error", "failed")})
            continue
        loaded.append(ticker)
        for trade in tr.get("trades", []):
            candidates.append({"ticker": ticker, **trade})

    if not loaded:
        return {
            "status": "failed",
            "error": "no ticker backtests succeeded",
            "failed_tickers": failed,
            "mode": mode,
        }

    trades, equity_curve, game_over, game_over_ts, skipped = _replay_portfolio_trades(
        candidates, volume_rank, initial_capital, slot_size, max_positions,
    )
    metrics = _portfolio_metrics(trades, equity_curve, initial_capital)

    return {
        "status": "success",
        "mode": mode,
        "strategy": strategy_name,
        "strategy_config_name": config.get("_strategy_name"),
        "initial_capital_rub": initial_capital,
        "slot_size_rub": slot_size,
        "max_positions": max_positions,
        "date_from": date_from,
        "date_to": date_to,
        "tickers": loaded,
        "tickers_loaded": len(loaded),
        "candidate_trades": len(candidates),
        "failed_tickers": failed,
        "game_over": game_over,
        "game_over_ts": game_over_ts,
        "skipped_entries_no_slot": skipped,
        "metrics": metrics,
        "trades": trades,
        "equity_curve": equity_curve,
    }


def run_from_db(
    db: DBManager,
    mode: str = "dev",
    *,
    strategy_id: Optional[int] = None,
    initial_capital: float = DEFAULT_CAPITAL,
    slot_size: float = DEFAULT_SLOT_SIZE,
    max_positions: int = DEFAULT_MAX_POSITIONS,
) -> Dict[str, Any]:
    """Load active (or specified) strategy from DB and run simulation."""
    if strategy_id is not None:
        row = db.select(
            "SELECT name, config FROM trading.strategies WHERE id=%s", (strategy_id,)
        ).to_dataframe()
        if row.empty:
            return {"status": "failed", "error": f"strategy {strategy_id} not found"}
        strat_name = str(row.iloc[0]["name"])
        raw_cfg = row.iloc[0]["config"]
        if isinstance(raw_cfg, str):
            import ast
            config = ast.literal_eval(raw_cfg)
        else:
            config = dict(raw_cfg or {})
    else:
        active = get_active_paper_strategy(db)
        strat_name = active["name"]
        config = active["config"]

    config = normalize_patterns(config)
    config["_strategy_name"] = strat_name
    plugin_name = resolve_strategy_name(config)

    preset = MODE_PRESETS.get(mode, MODE_PRESETS["dev"])
    tickers = get_tickers_by_volume(
        db,
        date_from=preset["date_from"],
        date_to=preset["date_to"],
        max_tickers=preset["max_tickers"],
    )
    if not tickers:
        return {"status": "failed", "error": "no tickers in universe"}

    result = run_portfolio_simulation(
        db,
        plugin_name,
        config,
        tickers,
        initial_capital=initial_capital,
        slot_size=slot_size,
        max_positions=max_positions,
        date_from=preset["date_from"],
        date_to=preset["date_to"],
        mode=mode,
    )
    result["strategy_config_name"] = strat_name
    return result


def _default_output_path(mode: str) -> Path:
    return Path("reports") / "portfolio-simulator" / f"{mode}_run.json"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Portfolio backtest simulator (Issue #37)")
    parser.add_argument("--mode", choices=["dev", "full"], default="dev")
    parser.add_argument("--output", type=str, default=None, help="JSON report path")
    parser.add_argument("--strategy-id", type=int, default=None)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--slot-size", type=float, default=DEFAULT_SLOT_SIZE)
    parser.add_argument("--max-positions", type=int, default=DEFAULT_MAX_POSITIONS)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    db = DBManager()
    try:
        result = run_from_db(
            db,
            mode=args.mode,
            strategy_id=args.strategy_id,
            initial_capital=args.capital,
            slot_size=args.slot_size,
            max_positions=args.max_positions,
        )
    finally:
        db.close_pool()

    out_path = Path(args.output) if args.output else _default_output_path(args.mode)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    print(json.dumps({
        "status": result.get("status"),
        "mode": args.mode,
        "output": str(out_path),
        "metrics": result.get("metrics"),
        "game_over": result.get("game_over"),
        "n_trades": result.get("metrics", {}).get("n_trades"),
    }, ensure_ascii=False, indent=2))

    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
