"""
Background levels-backtest matrix runner + status endpoint.
Uses the SHARED jobs_state lock (same module as backtest/data_refresh/signals jobs):
only one heavy job runs at a time (try_start fails otherwise -> 409).
POST /api/levels-backtest/run -> reserves slot, starts daemon thread running the matrix
  (tickers x swing_window x zone_atr x confirm_tf x risk_reward x slippage x entry_mode),
  persists each combo to backtest_* via run_and_persist, returns accepted.
GET /api/levels-backtest/run/status -> snapshot (progress + per-combo results).
NEVER call db.close_pool() in the worker (process-wide pool).
"""
from __future__ import annotations

import threading
import traceback
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query

from app.api import jobs_state

JOB = "levels_backtest"

DEFAULT_TICKERS = ["SBER", "GAZP", "VTBR"]


def _build_combos(tickers, swing_windows, zone_atrs, confirm_tfs,
                  risk_rewards, slippages, entry_modes) -> List[Dict[str, Any]]:
    combos = []
    for tk in tickers:
        for sw in swing_windows:
            for za in zone_atrs:
                for ctf in confirm_tfs:
                    for rr in risk_rewards:
                        for slip in slippages:
                            for em in entry_modes:
                                combos.append({
                                    'ticker': tk, 'swing_window': sw, 'zone_atr': za,
                                    'confirm_tf': ctf, 'risk_reward': rr,
                                    'slippage_per_side': slip, 'entry_mode': em,
                                    'entry_window_start': 7, 'entry_window_end': 19,
                                })
    return combos


def _run_matrix(combos: List[Dict[str, Any]]) -> None:
    jobs_state.update(JOB, combinations_total=len(combos), combinations_done=0,
                      current=None, combo_results=[], errors=[])
    combo_results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    try:
        from app.db.db_manager import DBManager
        from app.analytics.levels_backtest_db import run_and_persist
        db = DBManager()
        for i, params in enumerate(combos, 1):
            tk = params['ticker']
            label = f"{tk} ({i} из {len(combos)})"
            jobs_state.update(JOB, current=label, combinations_done=i - 1)
            try:
                res = run_and_persist(db, tk, params)
                m = res.get('metrics', {})
                combo_results.append({
                    'ticker': tk, 'run_id': res.get('run_id'),
                    'swing_window': params['swing_window'], 'zone_atr': params['zone_atr'],
                    'confirm_tf': params['confirm_tf'], 'risk_reward': params['risk_reward'],
                    'slippage_pct': params['slippage_per_side'] * 100,
                    'entry_mode': params['entry_mode'],
                    'n_trades': m.get('n_trades'), 'profit_factor': m.get('profit_factor'),
                    'expectancy': m.get('expectancy'), 'win_rate': m.get('win_rate'),
                    'total_net_pct': m.get('total_net_pct'),
                    'max_drawdown_pct': m.get('max_drawdown_pct'),
                    'persisted': res.get('persisted'),
                })
            except Exception as exc:
                errors.append({'ticker': tk, 'params': params, 'error': str(exc)})
                traceback.print_exc()
            jobs_state.update(JOB, combinations_done=i, combo_results=list(combo_results))
        jobs_state.finish(JOB, status="done", combinations_done=len(combos),
                          combo_results=combo_results, errors=errors, current=None)
    except Exception as exc:
        jobs_state.finish(JOB, status="failed", error=str(exc),
                          combo_results=combo_results, errors=errors)
        traceback.print_exc()


def register_routes(app: FastAPI) -> None:
    @app.post("/api/levels-backtest/run")
    def start_levels_backtest(
        tickers: str = Query(None),
        swing_windows: str = Query("10"),
        zone_atrs: str = Query("0.5"),
        confirm_tfs: str = Query("10min"),
        risk_rewards: str = Query("2.0"),
        slippages: str = Query("0.0005"),
        entry_modes: str = Query("levels_ts1"),
    ):
        if not jobs_state.try_start(JOB, stage="starting", combinations_total=0,
                                    combinations_done=0, current=None,
                                    combo_results=[], errors=[]):
            raise HTTPException(status_code=409, detail={
                "message": "A heavy job is already running",
                "jobs": jobs_state.all_snapshots(),
            })
        tk_list = [t.strip() for t in tickers.split(',')] if tickers else DEFAULT_TICKERS
        sw_list = [int(x) for x in swing_windows.split(',')]
        za_list = [float(x) for x in zone_atrs.split(',')]
        ctf_list = [x.strip() for x in confirm_tfs.split(',')]
        rr_list = [float(x) for x in risk_rewards.split(',')]
        slip_list = [float(x) for x in slippages.split(',')]
        em_list = [x.strip() for x in entry_modes.split(',')]
        combos = _build_combos(tk_list, sw_list, za_list, ctf_list, rr_list, slip_list, em_list)
        threading.Thread(target=_run_matrix, args=(combos,),
                         name="levels-backtest-run", daemon=True).start()
        return {"accepted": True, "combinations": len(combos), "job": jobs_state.snapshot(JOB)}

    @app.get("/api/levels-backtest/run/status")
    def levels_backtest_status():
        return jobs_state.snapshot(JOB)
