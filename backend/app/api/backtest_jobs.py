"""
Background backtest runner + status endpoint (BT-3b + signal_exit matrix).

- Uses the SHARED jobs_state lock (same module as /api/data/refresh and
  /api/signals/regenerate): only one heavy job runs at a time.
- POST /api/backtest/run  -> reserves the slot, starts a daemon thread that runs
  the hard-wired default matrix (and filter baselines), returns 202-ish accepted.
  ?quick=true runs a tiny subset on 3 tickers for a fast liveness check.
  ?signal_exit=on/off/all controls whether SELL-signal exit is tested (default all).
- GET  /api/backtest/run/status -> snapshot of the backtest job (progress +
  per-combination results so the matrix outcome is readable without hitting DB).
- NEVER call db.close_pool() in the worker (process-wide pool).
"""
from __future__ import annotations

import threading
import traceback
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query

from app.api import jobs_state
from app.analytics.backtest_models import BacktestParams, ExitRule

JOB = "backtest"

MATRIX_HOLDINGS = [3, 6, 12]
MATRIX_STOPS = [1.0, 2.0]
MATRIX_TAKES = [1.0, 2.0]
MATRIX_TFS = ["1h", "4h"]
FILTER_TOTAL_SIGNALS_MIN = 2
FILTER_CONFIDENCES = [0.5, 0.7, 0.9]


def _build_combos(quick: bool, signal_exit_mode: str = "all") -> List[BacktestParams]:
    combos: List[BacktestParams] = []
    signal_exits = [True, False] if signal_exit_mode == "all" else [signal_exit_mode == "on"]
    if quick:
        for se in signal_exits:
            suffix = "_sigOn" if se else "_sigOff"
            combos.append(BacktestParams(
                strategy_name=f"quick_h6_s2_t2{suffix}",
                exit_rule=ExitRule(holding_bars=6, stop_atr=2.0, take_atr=2.0),
                timeframes=MATRIX_TFS, signal_exit=se, extra={},
            ))
            combos.append(BacktestParams(
                strategy_name=f"quick_filter_ts2_c0.7{suffix}",
                exit_rule=ExitRule(holding_bars=6, stop_atr=2.0, take_atr=2.0),
                timeframes=MATRIX_TFS, signal_exit=se,
                extra={"filter_total_signals_min": FILTER_TOTAL_SIGNALS_MIN, "filter_confidence_min": 0.7},
            ))
        return combos
    for h in MATRIX_HOLDINGS:
        for s in MATRIX_STOPS:
            for t in MATRIX_TAKES:
                for se in signal_exits:
                    suffix = "_sigOn" if se else "_sigOff"
                    combos.append(BacktestParams(
                        strategy_name=f"matrix_h{h}_s{int(s)}_t{int(t)}{suffix}",
                        exit_rule=ExitRule(holding_bars=h, stop_atr=s, take_atr=t),
                        timeframes=MATRIX_TFS, signal_exit=se, extra={},
                    ))
    for c in FILTER_CONFIDENCES:
        for se in signal_exits:
            suffix = "_sigOn" if se else "_sigOff"
            combos.append(BacktestParams(
                strategy_name=f"filter_ts{FILTER_TOTAL_SIGNALS_MIN}_c{c}{suffix}",
                exit_rule=ExitRule(holding_bars=6, stop_atr=2.0, take_atr=2.0),
                timeframes=MATRIX_TFS, signal_exit=se,
                extra={"filter_total_signals_min": FILTER_TOTAL_SIGNALS_MIN, "filter_confidence_min": c},
            ))
    return combos


def _run_matrix(quick: bool, universe_limit, signal_exit_mode: str = "all", tickers: Optional[List[str]] = None) -> None:
    combos = _build_combos(quick, signal_exit_mode)
    jobs_state.update(JOB, combinations_total=len(combos), combinations_done=0,
                      current=None, combo_results=[], errors=[])
    combo_results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    try:
        from app.db.db_manager import DBManager
        from app.analytics.backtest_engine import run_backtest
        db = DBManager()
        try:
            for i, params in enumerate(combos, 1):
                jobs_state.update(JOB, current=params.strategy_name, combinations_done=i - 1)
                try:
                    res = run_backtest(db, params, tickers=tickers, universe_limit=universe_limit, write=True)
                    m = res.get("metrics") or {}
                    combo_results.append({
                        "strategy_name": params.strategy_name,
                        "run_id": res.get("run_id"),
                        "n_trades": res.get("n_trades"),
                        "profit_factor": m.get("profit_factor"),
                        "expectancy": m.get("expectancy"),
                        "win_rate": m.get("win_rate"),
                        "reliable": m.get("reliable"),
                        "benchmark_buyhold": m.get("benchmark_buyhold_return_pct"),
                        "benchmark_random": m.get("benchmark_random_return_pct"),
                    })
                except Exception as exc:
                    errors.append({"strategy_name": params.strategy_name, "error": str(exc)})
                    traceback.print_exc()
                jobs_state.update(JOB, combinations_done=i, combo_results=list(combo_results))
        finally:
            pass
        jobs_state.finish(JOB, status="done", combinations_done=len(combos),
                          combo_results=combo_results, errors=errors, current=None)
    except Exception as exc:
        jobs_state.finish(JOB, status="failed", error=str(exc),
                          combo_results=combo_results, errors=errors)
        traceback.print_exc()


def register_routes(app: FastAPI) -> None:
    @app.post("/api/backtest/run")
    def start_backtest(quick: bool = Query(False), universe_limit: int = Query(0, ge=0), signal_exit: str = Query("all"), tickers: str = Query(None)):
        if not jobs_state.try_start(JOB, stage="starting", combinations_total=0,
                                    combinations_done=0, current=None,
                                    combo_results=[], errors=[]):
            raise HTTPException(status_code=409, detail={
                "message": "A heavy job is already running",
                "jobs": jobs_state.all_snapshots(),
            })
        ul = universe_limit if universe_limit and universe_limit > 0 else (3 if quick else None)
        tickers_list = tickers.split(',') if tickers else None
        threading.Thread(target=_run_matrix, args=(quick, ul, signal_exit, tickers_list), name="backtest-run", daemon=True).start()
        return {"accepted": True, "quick": quick, "universe_limit": ul, "signal_exit": signal_exit, "job": jobs_state.snapshot(JOB)}

    @app.get("/api/backtest/run/status")
    def backtest_status():
        return jobs_state.snapshot(JOB)
