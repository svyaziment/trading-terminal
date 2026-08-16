"""Generate the portfolio inputs required by Issue #44.

The production simulator processes tickers sequentially. This report-only runner
parallelizes independent per-ticker plugin backtests, then applies the unchanged
portfolio replay rules once in ticker-volume order.
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ANALYSIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANALYSIS_DIR.parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.analytics.pattern_registry import normalize_patterns
from app.analytics.portfolio_backtest import run_portfolio_backtest
from app.analytics.portfolio_simulator import (
    MODE_PRESETS,
    _portfolio_metrics,
    _replay_portfolio_trades,
    get_tickers_by_volume,
)
from app.analytics.strategies.registry import register_default_strategies
from app.db.db_manager import DBManager


LEVELS_STRATEGY_ID = 36
ATR_CONFIG = {
    "atr_period": 14,
    "atr_completion_min": 0.80,
    "atr_completion_max": 0.90,
    "volume_spike_mult": 2.0,
    "stop_atr_mult": 1.0,
    "take_atr_mult": 0.85,
    "level_proximity_atr": 0.5,
    "commission_pct": 0.06,
    "slippage_pct": 0.0,
}


def _load_levels_config(db: DBManager) -> tuple[str, dict[str, Any]]:
    row = db.select(
        "SELECT name, config FROM trading.strategies WHERE id=%s",
        (LEVELS_STRATEGY_ID,),
    ).to_dataframe()
    if row.empty:
        raise RuntimeError(f"levels strategy id={LEVELS_STRATEGY_ID} not found")
    raw = row.iloc[0]["config"]
    config = ast.literal_eval(raw) if isinstance(raw, str) else dict(raw or {})
    return str(row.iloc[0]["name"]), normalize_patterns(config)


def _run_ticker(payload: tuple[str, dict[str, Any], str, str, str]) -> dict[str, Any]:
    strategy, config, ticker, date_from, date_to = payload
    register_default_strategies()
    db = DBManager()
    try:
        result = run_portfolio_backtest(
            db,
            strategy,
            config,
            [ticker],
            date_from=date_from,
            date_to=date_to,
        )
        return (result.get("results") or [{"ticker": ticker, "status": "failed"}])[0]
    finally:
        db.close_pool()


def _output_path(strategy: str, mode: str) -> Path:
    if strategy == "levels_reversal":
        return (
            REPO_ROOT
            / "reports/Arctic/37_portfolio-backtest"
            / f"{mode}_run.json"
        )
    return REPO_ROOT / "reports/Arctic/42_atr-reversal" / f"{mode}_run.json"


def generate(strategy: str, mode: str, workers: int) -> Path:
    preset = MODE_PRESETS[mode]
    db = DBManager()
    try:
        tickers = get_tickers_by_volume(
            db,
            date_from=preset["date_from"],
            date_to=preset["date_to"],
            max_tickers=preset["max_tickers"],
        )
        if strategy == "levels_reversal":
            config_name, config = _load_levels_config(db)
        else:
            config_name, config = "atr_reversal_default_v1", ATR_CONFIG.copy()
    finally:
        db.close_pool()

    by_ticker: dict[str, dict[str, Any]] = {}
    payloads = [
        (strategy, config, ticker, preset["date_from"], preset["date_to"])
        for ticker in tickers
    ]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_run_ticker, payload): payload[2] for payload in payloads}
        for completed, future in enumerate(as_completed(futures), 1):
            ticker = futures[future]
            try:
                by_ticker[ticker] = future.result()
                print(f"[{completed}/{len(tickers)}] {strategy}: {ticker}", flush=True)
            except Exception as exc:
                by_ticker[ticker] = {
                    "ticker": ticker,
                    "status": "failed",
                    "error": str(exc),
                }

    candidates = []
    loaded = []
    failed = []
    for ticker in tickers:
        ticker_result = by_ticker[ticker]
        if ticker_result.get("status") != "success":
            failed.append(
                {"ticker": ticker, "error": ticker_result.get("error", "failed")}
            )
            continue
        loaded.append(ticker)
        candidates.extend(
            {"ticker": ticker, **trade}
            for trade in ticker_result.get("trades", [])
        )

    if not loaded:
        raise RuntimeError(f"{strategy}: no ticker backtests succeeded")
    volume_rank = {ticker: rank for rank, ticker in enumerate(tickers)}
    trades, equity_curve, game_over, game_over_ts, skipped = (
        _replay_portfolio_trades(
            candidates,
            volume_rank,
            initial_capital=50_000.0,
            slot_size=10_000.0,
            max_positions=5,
        )
    )
    result = {
        "status": "success",
        "mode": mode,
        "strategy": strategy,
        "strategy_config_name": config_name,
        "strategy_config": config,
        "initial_capital_rub": 50_000.0,
        "slot_size_rub": 10_000.0,
        "max_positions": 5,
        "date_from": preset["date_from"],
        "date_to": preset["date_to"],
        "tickers": loaded,
        "tickers_loaded": len(loaded),
        "candidate_trades": len(candidates),
        "failed_tickers": failed,
        "game_over": game_over,
        "game_over_ts": game_over_ts,
        "skipped_entries_no_slot": skipped,
        "metrics": _portfolio_metrics(trades, equity_curve, 50_000.0),
        "trades": trades,
        "equity_curve": equity_curve,
    }
    output = _output_path(strategy, mode)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"Wrote {output}", flush=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Issue #44 inputs")
    parser.add_argument(
        "--strategy",
        choices=["levels_reversal", "atr_reversal", "both"],
        default="both",
    )
    parser.add_argument("--mode", choices=sorted(MODE_PRESETS), default="full")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    strategies = (
        ("levels_reversal", "atr_reversal")
        if args.strategy == "both"
        else (args.strategy,)
    )
    for strategy in strategies:
        generate(strategy, args.mode, max(1, args.workers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
