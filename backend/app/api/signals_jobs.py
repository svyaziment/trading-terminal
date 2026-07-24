"""
Async signal regeneration job + status endpoint.
Uses the shared jobs_state lock so it cannot run together with /api/data/refresh.
The actual work is synchronous and runs in a daemon thread.
NOTE: we deliberately do NOT call SignalGenerator.close() at the end, because
DBManager uses a single process-wide connection pool.
"""
from __future__ import annotations
import traceback
from fastapi import FastAPI, HTTPException
from app.api import jobs_state

JOB = "regenerate"


def _run_job() -> None:
    try:
        from app.analytics.signal_generator import SignalGenerator
        gen = SignalGenerator()
        try:
            tickers = gen.get_top_tickers(limit=30)
            jobs_state.update(JOB, tickers_count=len(tickers))
            if not tickers:
                raise RuntimeError("No tickers in trading.top_stocks_by_volume")
            report = gen.scan_and_save_signals(
                tickers=tickers,
                timeframes=["30min", "1h", "4h", "1d"],
                lookback=2000,
            )
            jobs_state.finish(
                JOB,
                status="done",
                total_signals_saved=int(report.get("total_signals_saved", 0) or 0),
                total_candles_analyzed=int(report.get("total_candles_analyzed", 0) or 0),
                errors_count=len(report.get("errors", []) or []),
            )
        finally:
            pass  # intentionally no gen.close()
    except Exception as exc:
        jobs_state.finish(JOB, status="failed", error=str(exc))
        traceback.print_exc()


def register_routes(app: FastAPI) -> None:
    @app.post("/api/signals/regenerate")
    def start_regenerate():
        if not jobs_state.try_start(JOB, stage="signals"):
            raise HTTPException(status_code=409, detail={
                "message": "A heavy job is already running",
                "jobs": jobs_state.all_snapshots(),
            })
        import threading
        threading.Thread(target=_run_job, name="signals-regenerate", daemon=True).start()
        return {"accepted": True, "job": jobs_state.snapshot(JOB)}

    @app.get("/api/signals/regenerate/status")
    def regenerate_status():
        return jobs_state.snapshot(JOB)
