"""
Background "update from market" pipeline + status endpoint.

Per ticker (TOP-30 from top_stocks_by_volume, latest report_date):
  1) load 30min candles from T-Bank for the last `days` days;
  2) idempotent write into candles_30min_raw via DELETE-range + insert
     (range = min/max of the fetched set, so history outside is never touched);
  3) re-aggregate that ticker's window into candles_aggregated;
  4) recompute indicators for 30min/1h/4h/1d;
After all tickers: regenerate signals once over the same universe.

Uses the shared jobs_state lock (cannot run together with /regenerate).
"""
from __future__ import annotations
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from app.api import jobs_state

JOB = "refresh"
DEFAULT_DAYS = 24
SIGNAL_TIMEFRAMES = ["30min", "1h", "4h", "1d"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _get_universe(db) -> List[Dict[str, str]]:
    rows = db.select(
        """
        WITH latest AS (SELECT max(report_date) AS rd FROM trading.top_stocks_by_volume)
        SELECT t.ticker, t.figi
        FROM trading.top_stocks_by_volume t JOIN latest l ON t.report_date = l.rd
        ORDER BY t.rank ASC LIMIT 30
        """
    ).to_dataframe()
    if rows.empty:
        return []
    return [{"ticker": str(r["ticker"]), "figi": str(r["figi"])} for _, r in rows.iterrows()]


def _load_raw_for_ticker(loader, db, ticker: str, figi: str, days: int) -> int:
    df = loader.fetch_candles_by_figi(figi=figi, ticker=ticker, days=days, interval_str="30min")
    if df is None or df.empty:
        return 0
    df = df.rename(columns={"time": "timestamp"})
    df["ticker"] = ticker
    df["figi"] = figi
    df = df[["ticker", "figi", "timestamp", "open", "high", "low", "close", "volume"]].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp")
    df = df.drop_duplicates(subset=["ticker", "timestamp"])
    if df.empty:
        return 0
    mn, mx = df["timestamp"].min(), df["timestamp"].max()
    db.execute(
        "DELETE FROM trading.candles_30min_raw "
        "WHERE ticker = %(t)s AND timestamp >= %(mn)s AND timestamp <= %(mx)s",
        {"t": ticker, "mn": mn, "mx": mx},
    )
    db.insert_with_schema("candles_30min_raw", df)
    return len(df)


def _run_job(days: int) -> None:
    try:
        from app.db.db_manager import DBManager
        from app.broker.data_loader import DataLoader
        from app.analytics.candles_aggregator import CandlesAggregator
        from app.analytics.indicators_manager import IndicatorsManager
        from app.analytics.signal_generator import SignalGenerator

        db = DBManager()
        loader = DataLoader()
        agg = CandlesAggregator()
        im = IndicatorsManager()

        universe = _get_universe(db)
        jobs_state.update(JOB, tickers_total=len(universe), tickers_done=0,
                          raw_inserted=0, stage="loading")
        if not universe:
            raise RuntimeError("Empty universe (top_stocks_by_volume)")

        now = datetime.now(timezone.utc)
        from_dt = now - timedelta(days=days)
        to_dt = now + timedelta(days=1)

        raw_inserted = 0
        errors: List[Dict[str, Any]] = []
        tickers = [u["ticker"] for u in universe]

        for i, u in enumerate(universe, 1):
            ticker, figi = u["ticker"], u["figi"]
            jobs_state.update(JOB, current_ticker=ticker, stage="loading")
            try:
                n = _load_raw_for_ticker(loader, db, ticker, figi, days)
                raw_inserted += n
                jobs_state.update(JOB, stage="aggregating")
                agg.aggregate_all_timeframes(ticker, from_dt, to_dt)
                jobs_state.update(JOB, stage="indicators")
                for tf in SIGNAL_TIMEFRAMES:
                    im.update_indicators_for_ticker(ticker, tf)
            except Exception as exc:
                errors.append({"ticker": ticker, "stage": "per_ticker", "error": str(exc)})
                traceback.print_exc()
            jobs_state.update(JOB, tickers_done=i, raw_inserted=raw_inserted)

        jobs_state.update(JOB, stage="signals", current_ticker=None)
        gen = SignalGenerator()
        report = gen.scan_and_save_signals(
            tickers=tickers, timeframes=SIGNAL_TIMEFRAMES, lookback=2000,
        )
        # NOTE: no gen.close() (shared pool)

        jobs_state.finish(
            JOB,
            status="done",
            stage="done",
            days=days,
            raw_inserted=raw_inserted,
            total_signals_saved=int(report.get("total_signals_saved", 0) or 0),
            errors_count=len(errors) + len(report.get("errors", []) or []),
            per_ticker_errors=errors[:50],
        )
    except Exception as exc:
        jobs_state.finish(JOB, status="failed", error=str(exc))
        traceback.print_exc()


def register_routes(app: FastAPI) -> None:
    @app.post("/api/data/refresh")
    def start_refresh(days: int = Query(DEFAULT_DAYS, ge=1, le=365)):
        if not jobs_state.try_start(JOB, stage="starting", days=days,
                                    tickers_total=0, tickers_done=0, raw_inserted=0):
            raise HTTPException(status_code=409, detail={
                "message": "A heavy job is already running",
                "jobs": jobs_state.all_snapshots(),
            })
        import threading
        threading.Thread(target=_run_job, args=(days,), name="data-refresh", daemon=True).start()
        return {"accepted": True, "job": jobs_state.snapshot(JOB)}

    @app.get("/api/data/refresh/status")
    def refresh_status():
        return jobs_state.snapshot(JOB)

    @app.get("/api/jobs/status")
    def all_jobs_status():
        return jobs_state.all_snapshots()
