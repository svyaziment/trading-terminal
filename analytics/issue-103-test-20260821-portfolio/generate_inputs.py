"""Generate the portfolio inputs required by Issue #103.

Parallel per-ticker StrategyPlugin backtests for DB strategy id=118
(`test_20260821`), then one portfolio replay with Issue #44 slot rules.

date_from comes from MODE_PRESETS['full']. date_to is shifted to exclusive
2026-08-21 so the 2026-08-20 session (paper #711 bar) is inside the sample.
Locked test_20260731 (id=36) and swing-only test_20260820 (id=102) are not
read or written.
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

import pandas as pd


ANALYSIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANALYSIS_DIR.parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.analytics.pattern_registry import normalize_patterns
from app.analytics.portfolio_backtest import run_portfolio_backtest
from app.analytics.portfolio_simulator import (
    MODE_PRESETS,
    _portfolio_metrics,
    _replay_portfolio_trades,
    get_tickers_by_volume,
    resolve_strategy_name,
)
from app.analytics.strategies.registry import register_default_strategies
from app.db.db_manager import DBManager


STRATEGY_ID = 118
EXPECTED_NAME = "test_20260821"
LOCKED_REFERENCE_ID = 36
SWING_ONLY_REFERENCE_ID = 102
INITIAL_CAPITAL = 50_000.0
SLOT_SIZE = 10_000.0
MAX_POSITIONS = 5
PERIOD_LAST_DAY = "2026-08-20"
DATE_TO_EXCLUSIVE = "2026-08-21"
ALRS_VETO_TS = pd.Timestamp("2026-08-20 11:50:24")
ALRS_VETO_PRICE = 19.80
EXPECTED_LEVEL_METHOD = ["swing", "impulse"]


def _period(mode: str) -> tuple[str, str, int | None]:
    preset = MODE_PRESETS[mode]
    date_from = str(preset["date_from"])
    max_tickers = preset["max_tickers"]
    if mode == "full":
        return date_from, DATE_TO_EXCLUSIVE, max_tickers
    return date_from, str(preset["date_to"]), max_tickers


def _levels_params(config: dict[str, Any]) -> dict[str, Any]:
    patterns = config.get("patterns") or {}
    if isinstance(patterns, dict):
        raw = patterns.get("levels_reversal") or {}
        return raw if isinstance(raw, dict) else {}
    return {}


def _load_strategy(db: DBManager) -> dict[str, Any]:
    row = db.select(
        "SELECT id, name, config, in_paper_test, locked "
        "FROM trading.strategies WHERE id=%s",
        (STRATEGY_ID,),
    ).to_dataframe()
    if row.empty:
        raise RuntimeError(f"strategy id={STRATEGY_ID} not found")
    name = str(row.iloc[0]["name"])
    if name != EXPECTED_NAME:
        raise RuntimeError(
            f"strategy id={STRATEGY_ID} name={name!r}, expected {EXPECTED_NAME!r}"
        )
    raw = row.iloc[0]["config"]
    config = ast.literal_eval(raw) if isinstance(raw, str) else dict(raw or {})
    config = normalize_patterns(config)
    levels = _levels_params(config)
    level_method = list(levels.get("level_method") or [])
    if level_method != EXPECTED_LEVEL_METHOD:
        raise RuntimeError(
            f"strategy id={STRATEGY_ID} level_method={level_method!r}, "
            f"expected {EXPECTED_LEVEL_METHOD!r}"
        )
    return {
        "id": int(row.iloc[0]["id"]),
        "name": name,
        "in_paper_test": bool(row.iloc[0]["in_paper_test"]),
        "locked": bool(row.iloc[0]["locked"]),
        "config": config,
    }


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


def _output_path(mode: str) -> Path:
    return REPO_ROOT / "reports/Arctic/103_test-20260821-portfolio" / f"{mode}_run.json"


def _alrs_hits(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    minute = ALRS_VETO_TS.floor("min")
    hits: list[dict[str, Any]] = []
    for trade in trades:
        if trade.get("ticker") != "ALRS":
            continue
        ts = pd.Timestamp(trade["entry_ts"])
        price = float(trade.get("entry_price") or 0.0)
        same_bar = ts == ALRS_VETO_TS or ts.floor("min") == minute
        same_price = abs(price - ALRS_VETO_PRICE) <= 0.015
        if same_bar and same_price:
            hits.append(
                {
                    "ticker": "ALRS",
                    "entry_ts": str(trade.get("entry_ts")),
                    "entry_price": trade.get("entry_price"),
                }
            )
    return hits


def generate(mode: str, workers: int) -> Path:
    date_from, date_to, max_tickers = _period(mode)
    db = DBManager()
    try:
        snapshot = _load_strategy(db)
        tickers = get_tickers_by_volume(
            db,
            date_from=date_from,
            date_to=date_to,
            max_tickers=max_tickers,
        )
    finally:
        db.close_pool()

    config = snapshot["config"]
    plugin_name = resolve_strategy_name(config)
    by_ticker: dict[str, dict[str, Any]] = {}
    payloads = [
        (plugin_name, config, ticker, date_from, date_to) for ticker in tickers
    ]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_run_ticker, payload): payload[2] for payload in payloads}
        for completed, future in enumerate(as_completed(futures), 1):
            ticker = futures[future]
            try:
                by_ticker[ticker] = future.result()
                print(f"[{completed}/{len(tickers)}] {plugin_name}: {ticker}", flush=True)
            except Exception as exc:
                by_ticker[ticker] = {
                    "ticker": ticker,
                    "status": "failed",
                    "error": str(exc),
                }

    candidates: list[dict[str, Any]] = []
    loaded: list[str] = []
    failed: list[dict[str, str]] = []
    for ticker in tickers:
        ticker_result = by_ticker[ticker]
        if ticker_result.get("status") != "success":
            failed.append(
                {"ticker": ticker, "error": str(ticker_result.get("error", "failed"))}
            )
            continue
        loaded.append(ticker)
        candidates.extend(
            {"ticker": ticker, **trade}
            for trade in ticker_result.get("trades", [])
        )

    if not loaded:
        raise RuntimeError(f"{plugin_name}: no ticker backtests succeeded")

    volume_rank = {ticker: rank for rank, ticker in enumerate(tickers)}
    trades, equity_curve, game_over, game_over_ts, skipped = _replay_portfolio_trades(
        candidates,
        volume_rank,
        initial_capital=INITIAL_CAPITAL,
        slot_size=SLOT_SIZE,
        max_positions=MAX_POSITIONS,
    )
    alrs_candidates = _alrs_hits(candidates)
    alrs_portfolio = _alrs_hits(trades)
    result = {
        "status": "success",
        "issue": 103,
        "mode": mode,
        "strategy": plugin_name,
        "strategy_id": snapshot["id"],
        "strategy_config_name": snapshot["name"],
        "in_paper_test": snapshot["in_paper_test"],
        "locked": snapshot["locked"],
        "locked_reference_id_untouched": LOCKED_REFERENCE_ID,
        "swing_only_reference_id_untouched": SWING_ONLY_REFERENCE_ID,
        "strategy_config": config,
        "initial_capital_rub": INITIAL_CAPITAL,
        "slot_size_rub": SLOT_SIZE,
        "max_positions": MAX_POSITIONS,
        "date_from": date_from,
        "date_to": date_to,
        "period_last_day": PERIOD_LAST_DAY if mode == "full" else None,
        "tickers_volume_order": tickers,
        "tickers": loaded,
        "tickers_loaded": len(loaded),
        "candidate_trades": len(candidates),
        "failed_tickers": failed,
        "game_over": game_over,
        "game_over_ts": game_over_ts,
        "skipped_entries_no_slot": skipped,
        "metrics": _portfolio_metrics(trades, equity_curve, INITIAL_CAPITAL),
        "alrs_veto_check": {
            "timestamp": str(ALRS_VETO_TS),
            "price": ALRS_VETO_PRICE,
            "found_in_candidates": bool(alrs_candidates),
            "found_in_portfolio_trades": bool(alrs_portfolio),
            "candidate_hits": alrs_candidates,
            "portfolio_hits": alrs_portfolio,
        },
        "trades": trades,
        "equity_curve": equity_curve,
    }
    output = _output_path(mode)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"Wrote {output}", flush=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Issue #103 inputs")
    parser.add_argument("--mode", choices=sorted(MODE_PRESETS), default="full")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    generate(args.mode, max(1, args.workers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
