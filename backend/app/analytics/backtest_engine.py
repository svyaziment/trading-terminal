"""
Deterministic backtest engine core (BT-2 + BT-3 metrics upgrade).

Locked semantics (unchanged from BT-2):
- Long-only. BUY opens a long (if flat); SELL is an EXIT trigger for an open long.
- Entry price = open of the bar AFTER the signal bar (open_next). Signal on the last
  bar (no T+1) is skipped as 'untradeable', NOT an error.
- Stop/take checked against bar high/low; holding-expiry and signal-exit use close.
- Both stop+take on one bar -> stop wins. Gap-open beyond a level -> fill at open.
- Costs: commission_per_side + exchange_fee_per_side + slippage in ticks.
- Position size fixed = 1 lot in v1. session_only FORCED False (tz unverified).

BT-3 additions:
- Equity curve is capital-based: equity = initial_capital + cum_pnl, so drawdown and
  the equity series are meaningful (the BT-2 cum_pnl-from-zero DD was an artefact).
- Metrics are emitted per group (pattern_name x timeframe) AND for ALL, each with a
  reliable flag (n_trades >= min_trades_per_group).
- Benchmarks (buy&hold and per-trade random long) are computed for the ALL group only
  in v1; per-group benchmarks are null (deferred). Random uses a FIXED module seed so
  it is deterministic across runs.
- Filter baseline (total_signals>=k & confidence>=tau) is supported via params.extra
  (filter_total_signals_min / filter_confidence_min) - no engine change needed.

DB contract (backend/app/db/db_manager.py):
- read  : db.select(sql, params).to_dataframe()
- write : db.insert_with_schema(table, df)  (INSERT by df column NAMES; commits)
- execute commits; select does NOT commit.
- run id via nextval(...) BEFORE insert (sequence non-transactional), then explicit-id
  insert via execute (commits).
- NEVER call db.close_pool() here (process-wide pool).
"""
from __future__ import annotations

import json
import random
import statistics as _st
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.analytics.backtest_models import (
    DEFAULT_COMMISSION_PER_SIDE,
    DEFAULT_EXCHANGE_FEE_PER_SIDE,
    DEFAULT_SLIPPAGE_TICKS,
    ENTRY_RULE,
    EXIT_HOLDING,
    EXIT_SIGNAL,
    EXIT_STOP,
    EXIT_TAKE,
    EXIT_UNTRADEABLE,
    MIN_TRADES_PER_GROUP,
    SIDE_LONG,
    SIGNAL_BUY,
    SIGNAL_SELL,
    BacktestParams,
    ExitRule,
)

SESSION_FORCED_OFF_NOTE = "session_only forced False in v1 (timestamp tz unverified)"
RANDOM_BENCH_SEED = 20260725  # fixed -> deterministic random benchmark


@dataclass
class _OpenPos:
    signal_id: Optional[int]
    ticker: str
    figi: Optional[str]
    timeframe: str
    pattern_name: Optional[str]
    entry_ts: pd.Timestamp
    entry_price: float
    stop: float
    take: float
    entry_idx: int
    atr_at_entry: float
    lot_size: int
    min_price_increment: float


def _round_to_tick(price: float, tick: float) -> float:
    if not tick or tick <= 0:
        return float(price)
    return round(round(float(price) / tick) * tick, 10)


def _load_df(db, sql: str, params: dict) -> pd.DataFrame:
    return db.select(sql, params).to_dataframe()


def _next_run_id(db) -> int:
    df = db.select("SELECT nextval('trading.backtest_runs_id_seq') AS id").to_dataframe()
    return int(df.iloc[0]["id"])


def _get_universe(db, params: BacktestParams) -> pd.DataFrame:
    sql = (
        "WITH latest AS (SELECT max(report_date) AS rd FROM trading.top_stocks_by_volume) "
        "SELECT t.rank, t.ticker, t.figi "
        "FROM trading.top_stocks_by_volume t JOIN latest l ON t.report_date = l.rd "
        "ORDER BY t.rank ASC LIMIT 30"
    )
    df = _load_df(db, sql, {})
    if params.universe_report_date is None and not df.empty:
        rd = _load_df(db, "SELECT max(report_date) AS rd FROM trading.top_stocks_by_volume", {})
        params.universe_report_date = None if rd.empty else str(rd.iloc[0]["rd"])
    return df


def _get_instruments(db, tickers: List[str]) -> Dict[str, Dict[str, Any]]:
    if not tickers:
        return {}
    sql = (
        "SELECT ticker, figi, lot_size, min_price_increment "
        "FROM trading.instruments WHERE ticker = ANY(%(tk)s)"
    )
    df = _load_df(db, sql, {"tk": tickers})
    out: Dict[str, Dict[str, Any]] = {}
    for _, r in df.iterrows():
        out[str(r["ticker"])] = {
            "figi": None if pd.isna(r.get("figi")) else str(r["figi"]),
            "lot_size": int(r["lot_size"]) if pd.notna(r.get("lot_size")) else 1,
            "min_price_increment": float(r["min_price_increment"]) if pd.notna(r.get("min_price_increment")) else 0.0,
        }
    return out


def _simulate_exit(candles: pd.DataFrame, pos: _OpenPos, sell_ts: set, rule: ExitRule):
    n = len(candles)
    last_idx = min(pos.entry_idx + rule.holding_bars, n - 1)
    tick = pos.min_price_increment
    for j in range(pos.entry_idx + 1, last_idx + 1):
        row = candles.iloc[j]
        ts = row["timestamp"]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        hit_stop = l <= pos.stop
        hit_take = h >= pos.take
        if o <= pos.stop:
            return j, EXIT_STOP, _round_to_tick(o, tick)
        if o >= pos.take:
            return j, EXIT_TAKE, _round_to_tick(o, tick)
        if hit_stop and hit_take:
            return j, EXIT_STOP, _round_to_tick(pos.stop, tick)
        if hit_stop:
            return j, EXIT_STOP, _round_to_tick(pos.stop, tick)
        if hit_take:
            return j, EXIT_TAKE, _round_to_tick(pos.take, tick)
        if ts in sell_ts:
            return j, EXIT_SIGNAL, c
        if j == last_idx:
            return j, EXIT_HOLDING, c
    return None, EXIT_UNTRADEABLE, float("nan")


def _build_trade(run_id, pos, exit_idx, reason, exit_price,
                 commission_per_side, exchange_fee_per_side, slippage_ticks):
    entry_price = pos.entry_price
    lot = pos.lot_size
    tick = pos.min_price_increment
    gross_rub = (exit_price - entry_price) * lot
    comm_rub = (entry_price * lot) * (commission_per_side + exchange_fee_per_side) + \
               (exit_price * lot) * (commission_per_side + exchange_fee_per_side)
    slip_rub = 2.0 * slippage_ticks * tick * lot if tick > 0 else 0.0
    net_rub = gross_rub - comm_rub - slip_rub
    base_rub = entry_price * lot if entry_price * lot else 1.0
    return {
        "run_id": run_id, "ticker": pos.ticker, "figi": pos.figi, "timeframe": pos.timeframe,
        "signal_id": pos.signal_id, "pattern_name": pos.pattern_name, "side": SIDE_LONG,
        "entry_ts": str(pos.entry_ts), "entry_price": float(entry_price),
        "exit_ts": str(pos.exit_ts), "exit_price": float(exit_price), "exit_reason": reason,
        "bars_held": int(exit_idx - pos.entry_idx),
        "gross_return_pct": float(gross_rub / base_rub * 100.0),
        "commission_pct": float(comm_rub / base_rub * 100.0),
        "slippage_pct": float(slip_rub / base_rub * 100.0),
        "net_return_pct": float(net_rub / base_rub * 100.0),
        "pnl_rub": float(net_rub),
        "lot_size": int(lot), "min_price_increment": float(tick),
        "entry_close": float(pos.entry_close), "exit_close": float(pos.exit_close),
    }


def _group_metrics(run_id, group_key, trades, min_trades, initial_capital,
                   buyhold=None, randomret=None):
    n = len(trades)
    if n == 0:
        return {
            "run_id": run_id, "group_key": group_key, "n_trades": 0, "win_rate": 0.0,
            "profit_factor": 0.0, "expectancy": 0.0, "sharpe": 0.0, "sortino": 0.0,
            "max_drawdown": 0.0, "avg_bars_held": 0.0, "reliable": False,
            "benchmark_buyhold_return_pct": buyhold, "benchmark_random_return_pct": randomret,
        }
    nets = [t["net_return_pct"] for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    sum_w = sum(wins) if wins else 0.0
    sum_l = abs(sum(losses)) if losses else 0.0
    pf = (sum_w / sum_l) if sum_l > 0 else (float("inf") if sum_w > 0 else 0.0)
    mean = _st.mean(nets)
    sd = _st.pstdev(nets) if n > 1 else 0.0
    down = [x for x in nets if x < 0]
    sd_down = _st.pstdev(down) if len(down) > 1 else 0.0
    # capital-based drawdown over this group's trades (ordered by exit_ts)
    ordered = sorted(trades, key=lambda t: t["exit_ts"])
    cum = 0.0
    peak = float(initial_capital)
    max_dd = 0.0
    for t in ordered:
        cum += t["pnl_rub"]
        eq = float(initial_capital) + cum
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return {
        "run_id": run_id, "group_key": group_key, "n_trades": n,
        "win_rate": float(len(wins) / n),
        "profit_factor": float(pf) if pf != float("inf") else None,
        "expectancy": float(mean),
        "sharpe": float(mean / sd) if sd > 0 else 0.0,
        "sortino": float(mean / sd_down) if sd_down > 0 else 0.0,
        "max_drawdown": float(max_dd),
        "avg_bars_held": float(sum(t["bars_held"] for t in trades) / n),
        "reliable": bool(n >= min_trades),
        "benchmark_buyhold_return_pct": buyhold,
        "benchmark_random_return_pct": randomret,
    }


def _benchmarks(trades, candles_by_tk_tf, seed):
    """buy&hold = mean(close_exit/close_entry - 1) over the strategy's trades (no costs).
    random = mean return of len(trades) random long-only entries on random bars/tickers
    of the run, holding the same bars_held as the matched real trade (no costs)."""
    if not trades:
        return None, None
    buyhold = _st.mean([(t["exit_close"] / t["entry_close"] - 1.0) * 100.0 for t in trades
                        if t["entry_close"] and t["entry_close"] > 0])
    rng = random.Random(seed)
    pool = [(tk, tf, c) for (tk, tf), c in candles_by_tk_tf.items() if len(c) > 5]
    if not pool:
        return float(buyhold), None
    rnd_rets = []
    for t in trades:
        tk, tf, c = rng.choice(pool)
        max_start = len(c) - 1 - t["bars_held"]
        if max_start <= 0:
            continue
        s = rng.randint(0, max_start - 1)
        e = s + t["bars_held"]
        ce = float(c.iloc[s]["close"])
        cx = float(c.iloc[e]["close"])
        if ce > 0:
            rnd_rets.append((cx / ce - 1.0) * 100.0)
    randomret = float(_st.mean(rnd_rets)) if rnd_rets else None
    return float(buyhold), randomret


def run_backtest(db, params: BacktestParams, *, tickers: Optional[List[str]] = None, universe_limit: Optional[int] = None, write: bool = True):
    rule = params.exit_rule
    rule.session_only = False  # FORCED in v1
    initial_capital = float(getattr(params, "initial_capital", 1_000_000.0) or 1_000_000.0)

    universe = _get_universe(db, params)
    if tickers:
        universe = universe[universe['ticker'].isin(tickers)]
    if universe_limit:
        universe = universe.head(universe_limit)
    tickers = [str(t) for t in universe["ticker"].tolist()] if not universe.empty else []
    instruments = _get_instruments(db, tickers)

    run_id: Optional[int] = None
    if write:
        run_id = _next_run_id(db)
        db.execute(
            "INSERT INTO trading.backtest_runs "
            "(id, strategy_name, params, universe_snapshot, selection_bias, git_hash, "
            " started_at, status, total_trades, note) "
            "VALUES (%(id)s, %(sn)s, %(p)s::jsonb, %(u)s::jsonb, %(sb)s, %(gh)s, "
            " CURRENT_TIMESTAMP, %(st)s, %(tt)s, %(note)s)",
            {
                "id": run_id, "sn": params.strategy_name,
                "p": json.dumps(params.to_dict(), ensure_ascii=False),
                "u": json.dumps({"tickers": tickers, "report_date": params.universe_report_date}, ensure_ascii=False),
                "sb": bool(params.selection_bias), "gh": None, "st": "running", "tt": None,
                "note": SESSION_FORCED_OFF_NOTE,
            },
        )

    trades: List[Dict[str, Any]] = []
    candles_by_tk_tf: Dict[Tuple[str, str], pd.DataFrame] = {}

    for tf in params.timeframes:
        for _, urow in universe.iterrows():
            ticker = str(urow["ticker"])
            figi = str(urow["figi"]) if pd.notna(urow.get("figi")) else instruments.get(ticker, {}).get("figi")
            inst = instruments.get(ticker, {"lot_size": 1, "min_price_increment": 0.0, "figi": figi})
            lot = int(inst.get("lot_size") or 1)
            tick = float(inst.get("min_price_increment") or 0.0)

            candles = _load_df(
                db,
                "SELECT timestamp, open, high, low, close FROM trading.candles_aggregated "
                "WHERE ticker=%(t)s AND timeframe=%(tf)s ORDER BY timestamp",
                {"t": ticker, "tf": tf},
            )
            if candles.empty:
                continue
            candles = candles.reset_index(drop=True)
            candles["timestamp"] = pd.to_datetime(candles["timestamp"])
            candles_by_tk_tf[(ticker, tf)] = candles
            ts_to_idx = {ts: i for i, ts in enumerate(candles["timestamp"])}

            ind = _load_df(
                db,
                "SELECT timestamp, atr_14 FROM trading.indicators "
                "WHERE ticker=%(t)s AND timeframe=%(tf)s ORDER BY timestamp",
                {"t": ticker, "tf": tf},
            )
            atr_by_ts = {}
            if not ind.empty:
                ind = ind.copy()
                ind["timestamp"] = pd.to_datetime(ind["timestamp"])
                atr_by_ts = {ts: (None if pd.isna(v) else float(v)) for ts, v in zip(ind["timestamp"], ind["atr_14"])}

            sigs = _load_df(
                db,
                "SELECT id, timestamp, signal, confidence, total_signals, pattern_name "
                "FROM trading.signals WHERE ticker=%(t)s AND timeframe=%(tf)s ORDER BY timestamp",
                {"t": ticker, "tf": tf},
            )
            if sigs.empty:
                continue
            sigs = sigs.copy()
            sigs["timestamp"] = pd.to_datetime(sigs["timestamp"])
            buy = sigs[sigs["signal"] == SIGNAL_BUY].copy()
            if params.extra.get("filter_total_signals_min") is not None:
                buy = buy[buy["total_signals"].fillna(0) >= int(params.extra["filter_total_signals_min"])]
            if params.extra.get("filter_confidence_min") is not None:
                buy = buy[buy["confidence"].fillna(0) >= float(params.extra["filter_confidence_min"])]
            if params.signal_exit:

                min_total = getattr(params, 'signal_exit_min_total', 1) or 1

                sell_ts = set(sigs[(sigs["signal"] == SIGNAL_SELL) & (sigs["total_signals"].fillna(0) >= min_total)]["timestamp"].tolist())

            else:

                sell_ts = set()

            for _, srow in buy.iterrows():
                t_sig = srow["timestamp"]
                if t_sig not in ts_to_idx:
                    continue
                idx = ts_to_idx[t_sig]
                if idx + 1 >= len(candles):
                    continue
                entry_row = candles.iloc[idx + 1]
                entry_price = _round_to_tick(float(entry_row["open"]), tick)
                entry_ts = entry_row["timestamp"]
                atr = atr_by_ts.get(entry_ts)
                if atr is None or atr <= 0:
                    continue
                stop = entry_price - rule.stop_atr * atr
                take = entry_price + rule.take_atr * atr
                pos = _OpenPos(
                    signal_id=int(srow["id"]) if pd.notna(srow.get("id")) else None,
                    ticker=ticker, figi=figi, timeframe=tf,
                    pattern_name=None if pd.isna(srow.get("pattern_name")) else str(srow["pattern_name"]),
                    entry_ts=entry_ts, entry_price=entry_price, stop=stop, take=take,
                    entry_idx=idx + 1, atr_at_entry=atr, lot_size=lot, min_price_increment=tick,
                )
                pos.entry_close = float(entry_row["close"])  # type: ignore[attr-defined]
                exit_idx, reason, exit_price = _simulate_exit(candles, pos, sell_ts, rule)
                if exit_idx is None:
                    continue
                pos.exit_ts = candles.iloc[exit_idx]["timestamp"]  # type: ignore[attr-defined]
                pos.exit_close = float(candles.iloc[exit_idx]["close"])  # type: ignore[attr-defined]
                tr = _build_trade(run_id if write else -1, pos, exit_idx, reason, exit_price,
                                  params.commission_per_side, params.exchange_fee_per_side, params.slippage_ticks)
                trades.append(tr)

    # ---- metrics: ALL (with benchmarks) + per pattern x timeframe groups ----
    buyhold, randomret = _benchmarks(trades, candles_by_tk_tf, RANDOM_BENCH_SEED)
    metrics_rows = [_group_metrics(run_id if write else -1, "ALL", trades,
                                   params.min_trades_per_group, initial_capital, buyhold, randomret)]
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for t in trades:
        groups.setdefault((t.get("pattern_name") or "NONE", t["timeframe"]), []).append(t)
    for (pn, tf), gtr in sorted(groups.items()):
        metrics_rows.append(_group_metrics(run_id if write else -1, f"pattern={pn}|tf={tf}", gtr,
                                           params.min_trades_per_group, initial_capital, None, None))

    # ---- equity curve (capital-based) over ALL trades ----
    ordered = sorted(trades, key=lambda t: t["exit_ts"])
    cum = 0.0
    peak = float(initial_capital)
    eq_rows = []
    for t in ordered:
        cum += t["pnl_rub"]
        eq = float(initial_capital) + cum
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
        eq_rows.append({"run_id": run_id if write else -1, "ts": t["exit_ts"],
                        "equity_rub": float(eq), "drawdown_pct": float(dd)})

    # ---- write ----
    if write and run_id is not None:
        if trades:
            trade_cols = ["run_id", "ticker", "figi", "timeframe", "signal_id", "pattern_name",
                          "side", "entry_ts", "entry_price", "exit_ts", "exit_price", "exit_reason",
                          "bars_held", "gross_return_pct", "commission_pct", "slippage_pct",
                          "net_return_pct", "pnl_rub", "lot_size", "min_price_increment"]
            db.insert_with_schema("trading.backtest_trades", pd.DataFrame(trades)[trade_cols])
        if eq_rows:
            db.insert_with_schema("trading.backtest_equity",
                                  pd.DataFrame(eq_rows)[["run_id", "ts", "equity_rub", "drawdown_pct"]])
        db.insert_with_schema("trading.backtest_metrics", pd.DataFrame(metrics_rows))
        db.execute(
            "UPDATE trading.backtest_runs SET status=%(st)s, total_trades=%(tt)s, "
            "finished_at=CURRENT_TIMESTAMP WHERE id=%(id)s",
            {"st": "done", "tt": len(trades), "id": run_id},
        )

    return {
        "run_id": run_id, "n_trades": len(trades),
        "metrics": metrics_rows[0], "metrics_rows": metrics_rows,
        "trades": trades, "note": SESSION_FORCED_OFF_NOTE,
    }
