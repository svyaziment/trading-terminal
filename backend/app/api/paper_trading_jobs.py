"""
Paper trading monitoring API (A/B test dashboard).
- GET /api/paper-trading/overview  : strategy name, factor options, summary stats (factor filters)
- GET /api/paper-trading/positions : positions list (filters + pagination + sort)
- GET /api/paper-trading/dynamics  : cumulative realized PnL series by 1h/1d/1w buckets (filters)
Factors: signal_source (base/imbalance/base_4hbuy), window_mode (window/always),
         rr_mode (all/rr15/rr2), entry_mode (market/limit).
"""
from __future__ import annotations
import math
from typing import Any, Optional
from fastapi import FastAPI, HTTPException, Query
from app.db.db_manager import DBManager

TF_MAP = {'1h': 'hour', '1d': 'day', '1w': 'week'}
FACTOR_COLS = ['signal_source', 'window_mode', 'rr_mode', 'entry_mode']


def _get_db():
    return DBManager()


def _json_safe(obj: Any) -> Any:
    """Decimal->float, Timestamp->isoformat str, nan/NaT->None (strict JSON)."""
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    s = str(obj)
    if s in ('NaT', 'nan', 'None', '<NA>'):
        return None
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    try:
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return s


def _build_where(signal_source=None, window_mode=None, rr_mode=None, entry_mode=None, status=None):
    clauses, params = [], {}
    if signal_source:
        clauses.append("signal_source = %(signal_source)s"); params['signal_source'] = signal_source
    if window_mode:
        clauses.append("window_mode = %(window_mode)s"); params['window_mode'] = window_mode
    if rr_mode:
        clauses.append("rr_mode = %(rr_mode)s"); params['rr_mode'] = rr_mode
    if entry_mode:
        clauses.append("entry_mode = %(entry_mode)s"); params['entry_mode'] = entry_mode
    if status:
        clauses.append("status = %(status)s"); params['status'] = status
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def register_routes(app: FastAPI) -> None:
    @app.get("/api/paper-trading/overview")
    def overview(signal_source: Optional[str] = None, window_mode: Optional[str] = None,
                 rr_mode: Optional[str] = None, entry_mode: Optional[str] = None):
        db = _get_db()
        where, params = _build_where(signal_source, window_mode, rr_mode, entry_mode)
        # strategy under paper test (locked)
        strat = db.select(
            "SELECT name, description FROM trading.strategies "
            "WHERE in_paper_test=TRUE ORDER BY id LIMIT 1").to_dataframe()
        strategy_name = str(strat.iloc[0]['name']) if not strat.empty else 'levels_reversal_4hbuy'
        strategy_desc = strat.iloc[0]['description'] if not strat.empty else None
        # factor options (unfiltered distinct values)
        factors = {}
        for col in FACTOR_COLS:
            fdf = db.select(
                f"SELECT DISTINCT {col} FROM trading.paper_positions "
                f"WHERE {col} IS NOT NULL ORDER BY {col}").to_dataframe()
            factors[col] = [str(v) for v in fdf[col].tolist()] if not fdf.empty else []
        # summary (filtered)
        sdf = db.select(f"""
            SELECT
              COUNT(*) AS total,
              COUNT(*) FILTER (WHERE status='open') AS open_count,
              COUNT(*) FILTER (WHERE status='pending') AS pending_count,
              COUNT(*) FILTER (WHERE status IN ('closed_stop','closed_take')) AS closed_count,
              COALESCE(SUM(pnl_rub) FILTER (WHERE status IN ('closed_stop','closed_take')), 0) AS realized_pnl_rub,
              COUNT(*) FILTER (WHERE status='closed_take') AS wins,
              COUNT(*) FILTER (WHERE status='closed_stop') AS losses
            FROM trading.paper_positions {where}
        """, params).to_dataframe()
        r = sdf.iloc[0]
        wins, losses = int(r['wins'] or 0), int(r['losses'] or 0)
        closed = int(r['closed_count'] or 0)
        summary = {
            'total': int(r['total'] or 0),
            'open': int(r['open_count'] or 0),
            'pending': int(r['pending_count'] or 0),
            'closed': closed,
            'realized_pnl_rub': round(float(r['realized_pnl_rub'] or 0), 2),
            'win_rate': round(wins / closed * 100, 1) if closed else None,
            'wins': wins,
            'losses': losses,
        }
        return _json_safe({'strategy_name': strategy_name, 'strategy_description': strategy_desc,
                           'factors': factors, 'summary': summary})

    @app.get("/api/paper-trading/positions")
    def positions(signal_source: Optional[str] = None, window_mode: Optional[str] = None,
                  rr_mode: Optional[str] = None, entry_mode: Optional[str] = None,
                  status: Optional[str] = None, ticker: Optional[str] = None,
                  limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                  sort_by: str = Query("entry_ts"), sort_dir: str = Query("desc")):
        db = _get_db()
        where, params = _build_where(signal_source, window_mode, rr_mode, entry_mode, status)
        if ticker:
            params['ticker'] = ticker
            where = where + (" AND ticker = %(ticker)s" if where else " WHERE ticker = %(ticker)s")
        allowed = {'entry_ts', 'exit_ts', 'pnl_rub', 'pnl_pct', 'ticker', 'status',
                   'created_at', 'entry_price', 'exit_price', 'id'}
        if sort_by not in allowed:
            sort_by = 'entry_ts'
        if sort_dir not in ('asc', 'desc'):
            sort_dir = 'desc'
        cdf = db.select(f"SELECT COUNT(*) c FROM trading.paper_positions {where}", params).to_dataframe()
        total = int(cdf.iloc[0]['c']) if not cdf.empty else 0
        df = db.select(f"""
            SELECT id, ticker, entry_ts, entry_price, exit_ts, exit_price, stop_price, take_price,
                   status, exit_reason, signal_source, window_mode, rr_mode, entry_mode,
                   pnl_rub, pnl_pct, size_lots, lot_size, created_at, updated_at
            FROM trading.paper_positions {where}
            ORDER BY {sort_by} {sort_dir}, id {sort_dir}
            LIMIT %(limit)s OFFSET %(offset)s
        """, {**params, 'limit': limit, 'offset': offset}).to_dataframe()
        items = [_json_safe(rec) for rec in df.to_dict('records')] if not df.empty else []
        return {'items': items, 'total': total, 'limit': limit, 'offset': offset}

    @app.get("/api/paper-trading/dynamics")
    def dynamics(timeframe: str = Query("1d"), signal_source: Optional[str] = None,
                 window_mode: Optional[str] = None, rr_mode: Optional[str] = None,
                 entry_mode: Optional[str] = None):
        if timeframe not in TF_MAP:
            raise HTTPException(status_code=400, detail=f"bad timeframe {timeframe}; use {list(TF_MAP)}")
        trunc = TF_MAP[timeframe]
        db = _get_db()
        where, params = _build_where(signal_source, window_mode, rr_mode, entry_mode)
        base_where = where + (" AND " if where else " WHERE ") + \
            "status IN ('closed_stop','closed_take') AND exit_ts IS NOT NULL"
        df = db.select(f"""
            SELECT date_trunc('{trunc}', exit_ts) AS bucket,
                   SUM(pnl_rub) AS pnl_rub,
                   COUNT(*) AS closed,
                   COUNT(*) FILTER (WHERE status='closed_take') AS wins
            FROM trading.paper_positions {base_where}
            GROUP BY bucket ORDER BY bucket
        """, params).to_dataframe()
        points, cum = [], 0.0
        for _, r in df.iterrows():
            pnl = float(r['pnl_rub'] or 0)
            cum += pnl
            points.append({'ts': str(r['bucket']), 'pnl_rub': round(pnl, 2),
                           'cum_pnl_rub': round(cum, 2), 'closed': int(r['closed'] or 0),
                           'wins': int(r['wins'] or 0)})
        return {'timeframe': timeframe, 'points': points, 'cum_pnl_rub': round(cum, 2)}
