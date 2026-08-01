"""
Strategy storage + backtest API.
- POST /api/strategies              : save strategy (name + config JSONB); rejects overwrite of locked (paper-trading) strategies.
- GET  /api/strategies              : list strategies (with in_paper_test / locked / description).
- GET  /api/strategies/run/status   : backtest job status.
- POST /api/strategies/{id}/run     : run backtest; writes params to reports/strategy-lab/last_run.json, then starts job.
- GET  /api/strategies/{id}/results : stored results.
- GET  /api/tickers/big             : tickers with >= min_candles 1min rows.
"""
from __future__ import annotations
import os
import re
import json
import traceback
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from app.api import jobs_state
from app.db.db_manager import DBManager

JOB = "strategy_backtest"
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

REPORT_DIR = os.environ.get("STRATEGY_REPORT_DIR", "/app/reports/strategy-lab")
REPORT_PATH = os.path.join(REPORT_DIR, "last_run.json")


class StrategyIn(BaseModel):
    name: str
    config: Dict[str, Any]


class RunIn(BaseModel):
    tickers: List[str]
    test_types: List[str] = ["full_sample", "walkforward"]
    depth: str = "express"
    date_from: Optional[str] = None
    date_to: Optional[str] = None


def _validate_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="name must be English letters/digits/_/- (1-64 chars)")


def _get_db():
    return DBManager()


def _json_dumps(obj) -> str:
    return json.dumps(obj, default=str)


def _to_dict(raw):
    """DBManager returns JSONB as a Python-repr str; normalize to a real dict."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    s = str(raw)
    try:
        return json.loads(s)
    except (ValueError, json.JSONDecodeError):
        pass
    try:
        import ast
        return ast.literal_eval(s)
    except Exception:
        return None


def _json_safe(obj):
    """Recursively replace NaN/inf/NaT with None; Timestamp/datetime -> ISO str (strict JSON)."""
    import math
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    s = str(obj)
    if s in ('NaT', 'nan'):
        return None
    try:
        return obj.isoformat()
    except AttributeError:
        return s


def _write_report(data: Dict[str, Any]) -> None:
    """Merge-update last_run.json (params + results + errors). Never raises."""
    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        existing: Dict[str, Any] = {}
        if os.path.exists(REPORT_PATH):
            try:
                with open(REPORT_PATH, encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = {}
        existing.update(data)
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass


def _run_job(strategy_id: int, tickers: List[str], test_types: List[str], depth: str, date_from=None, date_to=None) -> None:
    try:
        from app.analytics.strategy_backtest import run_strategy_backtest, run_walkforward, DEPTH_PRESETS
        import pandas as pd
        db = _get_db()
        srow = db.select("SELECT name, config FROM trading.strategies WHERE id=%s", (strategy_id,)).to_dataframe()
        if srow.empty:
            raise RuntimeError(f"strategy {strategy_id} not found")
        name = srow.iloc[0]['name']
        config = _to_dict(srow.iloc[0]['config'])
        _write_report({"strategy_name": name, "config": config})
        custom_period = bool(date_from and date_to)
        if custom_period:
            df_from = date_from
            # engine uses 'timestamp < date_to'; +1 day includes the whole end date
            df_to = (pd.Timestamp(date_to) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            preset = DEPTH_PRESETS.get(depth, DEPTH_PRESETS['express'])
            months = preset['months']
            df_from = (pd.Timestamp.now() - pd.DateOffset(months=months)).strftime('%Y-%m-%d')
            df_to = None
        jobs_state.update(JOB, stage="running", tickers_total=len(tickers), tickers_done=0)
        db.execute("DELETE FROM trading.backtest_results WHERE strategy_id=%s", (strategy_id,))
        done = 0
        for i, tk in enumerate(tickers, 1):
            jobs_state.update(JOB, current_ticker=tk)
            if "full_sample" in test_types:
                r = run_strategy_backtest(db, tk, config, date_from=df_from, date_to=df_to)
                if r.get('status') == 'success':
                    m = dict(r.get('metrics') or {})
                    m['trades'] = r.get('trades', [])
                else:
                    m = {'error': r.get('error')}
                db.execute(
                    "INSERT INTO trading.backtest_results (strategy_id, ticker, test_type, depth, metrics) VALUES (%s,%s,%s,%s,%s)",
                    (strategy_id, tk, 'full_sample', depth, _json_dumps(m)))
            if "walkforward" in test_types and not custom_period:
                wf = run_walkforward(db, tk, config)
                db.execute(
                    "INSERT INTO trading.backtest_results (strategy_id, ticker, test_type, depth, metrics) VALUES (%s,%s,%s,%s,%s)",
                    (strategy_id, tk, 'walkforward', depth, _json_dumps(wf)))
            done = i
            jobs_state.update(JOB, tickers_done=done)
        jobs_state.finish(JOB, status="done", stage="done", tickers_done=done)
        _write_report({"status": "done", "error": None, "finished_at": datetime.now().isoformat(),
                       "tickers_done": done, "tickers_total": len(tickers)})
    except Exception as exc:
        jobs_state.finish(JOB, status="failed", error=str(exc))
        _write_report({"status": "failed", "error": str(exc), "traceback": traceback.format_exc(),
                       "finished_at": datetime.now().isoformat()})
        traceback.print_exc()


def _ensure_schema(db) -> None:
    """Idempotent schema guard: soft-delete flag on strategies."""
    db.execute("ALTER TABLE trading.strategies ADD COLUMN IF NOT EXISTS is_deleted INTEGER NOT NULL DEFAULT 0")


def register_routes(app: FastAPI) -> None:
    _ensure_schema(_get_db())

    @app.post("/api/strategies")
    def save_strategy(payload: StrategyIn):
        _validate_name(payload.name)
        db = _get_db()
        # Reject overwrite of a paper-trading (locked) strategy
        existing = db.select("SELECT id, locked FROM trading.strategies WHERE name=%s", (payload.name,)).to_dataframe()
        if not existing.empty and bool(existing.iloc[0]['locked']):
            raise HTTPException(
                status_code=409,
                detail=f"Стратегия '{payload.name}' заблокирована (тестируется в paper trading). Выберите другое имя.")
        db.execute(
            "INSERT INTO trading.strategies (name, config) VALUES (%s,%s) "
            "ON CONFLICT (name) DO UPDATE SET config=EXCLUDED.config, is_deleted=0",
            (payload.name, _json_dumps(payload.config)))
        df = db.select("SELECT id FROM trading.strategies WHERE name=%s", (payload.name,)).to_dataframe()
        sid = int(df.iloc[0]['id'])
        return {"id": sid, "name": payload.name, "config": payload.config}

    @app.get("/api/strategies")
    def list_strategies():
        db = _get_db()
        df = db.select(
            "SELECT id, name, config, in_paper_test, locked, description, created_at::text AS created_at "
            "FROM trading.strategies WHERE coalesce(is_deleted,0) = 0 ORDER BY id").to_dataframe()
        rows = df.to_dict('records')
        for r in rows:
            r['config'] = _to_dict(r.get('config'))
            r['in_paper_test'] = bool(r.get('in_paper_test'))
            r['locked'] = bool(r.get('locked'))
        return {"strategies": [_json_safe(r) for r in rows]}

    # NOTE: registered before the {strategy_id} routes so 'run' is never parsed as an id.
    @app.get("/api/strategies/run/status")
    def run_status():
        return jobs_state.snapshot(JOB)

    @app.get("/api/strategies/data-range")
    def data_range():
        import pandas as pd
        db = _get_db()
        df = db.select("SELECT min(timestamp) mn, max(timestamp) mx FROM trading.candles_1min_raw").to_dataframe()
        if df.empty or df.iloc[0]['mn'] is None:
            return {"min_date": None, "max_date": None}
        return {"min_date": pd.Timestamp(df.iloc[0]['mn']).strftime('%Y-%m-%d'),
                "max_date": pd.Timestamp(df.iloc[0]['mx']).strftime('%Y-%m-%d')}

    @app.post("/api/strategies/{strategy_id}/run")
    def run_strategy(strategy_id: int, payload: RunIn):
        if not payload.tickers:
            raise HTTPException(status_code=400, detail="tickers required")
        for tt in payload.test_types:
            if tt not in ("full_sample", "walkforward"):
                raise HTTPException(status_code=400, detail=f"bad test_type {tt}")
        if not jobs_state.try_start(JOB, stage="starting", strategy_id=strategy_id,
                                    tickers_total=len(payload.tickers), tickers_done=0):
            raise HTTPException(status_code=409, detail={"message": "A heavy job is already running",
                                                          "jobs": jobs_state.all_snapshots()})
        _write_report({
            "strategy_id": strategy_id,
            "tickers": payload.tickers,
            "test_types": payload.test_types,
            "depth": payload.depth,
            "started_at": datetime.now().isoformat(),
            "status": "starting",
            "error": None,
        })
        # Persist run params so the UI can restore filters when the strategy is selected
        run_params = {"tickers": payload.tickers, "test_types": payload.test_types,
                      "depth": payload.depth, "date_from": payload.date_from, "date_to": payload.date_to}
        db = _get_db()
        db.execute(
            "UPDATE trading.strategies SET config = coalesce(config, '{}'::jsonb) || CAST(%s AS jsonb) WHERE id=%s",
            (_json_dumps({"run_params": run_params}), strategy_id))
        threading.Thread(target=_run_job, args=(strategy_id, payload.tickers, payload.test_types, payload.depth, payload.date_from, payload.date_to),
                         name="strategy-backtest", daemon=True).start()
        return {"accepted": True, "job": jobs_state.snapshot(JOB)}

    @app.get("/api/strategies/{strategy_id}/results")
    def strategy_results(strategy_id: int):
        db = _get_db()
        df = db.select(
            "SELECT id, ticker, test_type, depth, metrics, created_at::text AS created_at FROM trading.backtest_results "
            "WHERE strategy_id=%s ORDER BY test_type, ticker", (strategy_id,)).to_dataframe()
        rows = df.to_dict('records')
        for r in rows:
            r['metrics'] = _to_dict(r.get('metrics'))
        return {"strategy_id": strategy_id, "results": [_json_safe(r) for r in rows]}

    @app.delete("/api/strategies/{strategy_id}")
    def delete_strategy(strategy_id: int):
        db = _get_db()
        existing = db.select(
            "SELECT id, locked, name FROM trading.strategies WHERE id=%s AND coalesce(is_deleted,0) = 0",
            (strategy_id,)).to_dataframe()
        if existing.empty:
            raise HTTPException(status_code=404, detail="Стратегия не найдена или уже удалена")
        if bool(existing.iloc[0]['locked']):
            raise HTTPException(status_code=409, detail="Стратегия заблокирована (тестируется в paper trading) — удаление запрещено")
        db.execute("UPDATE trading.strategies SET is_deleted = 1 WHERE id=%s", (strategy_id,))
        return {"deleted": True, "id": strategy_id}

    @app.get("/api/tickers/big")
    def big_tickers(min_candles: int = Query(250000, ge=1)):
        from app.analytics.strategy_backtest import get_big_tickers
        db = _get_db()
        return {"min_candles": min_candles, "tickers": get_big_tickers(db, min_candles=min_candles)}
