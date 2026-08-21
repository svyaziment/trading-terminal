"""Generate Lab full-sample / walk-forward inputs for Issue #108.

Baseline A is the published Issue #100 Lab config ``test_20260820`` (id=102):
``levels_reversal`` (swing-only) + ``signal_4h_buy``. Config B is A plus
``level_breakout_retest`` with Lab defaults. Locked ``test_20260731`` is not
read or written.

The simplified JSON in the GitHub issue is a sketch; matching Issue #100
metrics requires the real Lab row, including ``signal_4h_buy`` and
``confirm_windows=[10]``.

Run from the repository root:

    python analytics/issue-108-breakout-retest-validation/generate_inputs.py
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import logging
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ANALYSIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANALYSIS_DIR.parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.analytics.levels_engine import LevelsTracker  # noqa: E402
from app.analytics.pattern_registry import (  # noqa: E402
    get_pattern_defaults,
    normalize_patterns,
)
from app.analytics.patterns.level_breakout_retest import (  # noqa: E402
    PATTERN_ID,
    classify_retest_rejection,
    disable_rejection_log,
    enable_rejection_log,
)
from app.analytics.signal_pattern_filters import SIGNAL_TIMEFRAME_DELTAS  # noqa: E402
from app.analytics.strategy_backtest import (  # noqa: E402
    WALKFORWARD_PERIODS,
    get_big_tickers,
    run_strategy_backtest,
)
from app.analytics.strategy_context import build_strategy_context  # noqa: E402
from app.analytics.strategy_engine import StrategyEvaluator  # noqa: E402
from app.db.db_manager import DBManager  # noqa: E402

STRATEGY_ID = 102
EXPECTED_NAME = "test_20260820"
LOCKED_REFERENCE_ID = 36
DATE_FROM = "2024-08-21"
DATE_TO = "2026-08-21"
ALRS_VETO_TS = pd.Timestamp("2026-08-20 11:50:24")
ALRS_VETO_PRICE = 19.80
IMPULSE_RESISTANCE = 19.67
IMPULSE_ZONE = (19.40, 19.94)
RETEST_DEFAULTS = {
    "level_timeframe": "4h",
    "retest_window_bars": 20,
    "retest_zone_atr": 0.5,
    "entry_trigger_bullish": True,
    "stop_atr": 1.0,
    "risk_reward": 2.0,
}


def _parse_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return ast.literal_eval(raw)
    return {}


def _slim_trade(trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_ts": str(trade.get("entry_ts")),
        "exit_ts": str(trade.get("exit_ts")),
        "entry_price": trade.get("entry_price"),
        "exit_price": trade.get("exit_price"),
        "exit_reason": trade.get("exit_reason"),
        "net_return_pct": trade.get("net_return_pct"),
    }


def _slim_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    if not metrics:
        return {"n": 0, "pf": None, "exp_pct": None, "wr": None, "maxdd_pct": None}
    return {
        "n": metrics.get("n", 0),
        "pf": metrics.get("pf"),
        "exp_pct": metrics.get("exp_pct"),
        "wr": metrics.get("wr"),
        "maxdd_pct": metrics.get("maxdd_pct"),
    }


def with_breakout_retest(config: dict[str, Any]) -> dict[str, Any]:
    cfg = normalize_patterns(copy.deepcopy(config))
    patterns = cfg.setdefault("patterns", {})
    params = get_pattern_defaults(PATTERN_ID)
    params.update(RETEST_DEFAULTS)
    patterns[PATTERN_ID] = params
    cfg["n_runs"] = 1
    return normalize_patterns(cfg)


def with_impulse_methods(config: dict[str, Any]) -> dict[str, Any]:
    cfg = normalize_patterns(copy.deepcopy(config))
    levels = cfg.setdefault("patterns", {}).setdefault("levels_reversal", {})
    levels["level_method"] = ["swing", "impulse"]
    return normalize_patterns(cfg)


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
    config = normalize_patterns(_parse_config(row.iloc[0]["config"]))
    config["n_runs"] = 1
    flags = db.select(
        "SELECT id, name, in_paper_test, locked FROM trading.strategies "
        "WHERE id IN (%s, %s) ORDER BY id",
        (LOCKED_REFERENCE_ID, STRATEGY_ID),
    ).to_dataframe()
    flag_rows = []
    if not flags.empty:
        for _, item in flags.iterrows():
            flag_rows.append(
                {
                    "id": int(item["id"]),
                    "name": str(item["name"]),
                    "in_paper_test": bool(item["in_paper_test"]),
                    "locked": bool(item["locked"]),
                }
            )
    return {
        "id": int(row.iloc[0]["id"]),
        "name": name,
        "in_paper_test": bool(row.iloc[0]["in_paper_test"]),
        "locked": bool(row.iloc[0]["locked"]),
        "config": config,
        "flags": flag_rows,
    }


def _worker(payload: tuple[Any, ...]) -> dict[str, Any]:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    label, ticker, config, date_from, date_to, collect_rejections = payload
    db = DBManager()
    bucket: list[str] = []
    try:
        if collect_rejections:
            enable_rejection_log(bucket)
        result = run_strategy_backtest(
            db, ticker, config, date_from=date_from, date_to=date_to
        )
    except Exception as exc:
        result = {"status": "failed", "ticker": ticker, "error": str(exc)}
    finally:
        if collect_rejections:
            disable_rejection_log()
        db.close_pool()
    out = {
        "label": label,
        "ticker": ticker,
        "status": result.get("status"),
        "error": result.get("error"),
        "metrics": _slim_metrics(result.get("metrics")),
        "bars_1min": result.get("bars_1min"),
        "trades": [_slim_trade(t) for t in result.get("trades") or []],
    }
    if collect_rejections:
        out["rejection_counts"] = dict(Counter(bucket))
    return out


def _run_jobs(jobs: list[tuple[Any, ...]], workers: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    checkpoint = (
        REPO_ROOT / "reports/Arctic/108_breakout-retest-validation/jobs.jsonl"
    )
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    done: set[tuple[str, str]] = set()
    if checkpoint.exists():
        with checkpoint.open(encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                key = (str(row.get("label")), str(row.get("ticker")))
                if key in done:
                    continue
                done.add(key)
                results.append(row)
        print(f"Resuming: {len(results)} jobs already in {checkpoint}", flush=True)
    pending = [job for job in jobs if (str(job[0]), str(job[1])) not in done]
    if not pending:
        return results

    def _persist(item: dict[str, Any]) -> None:
        with checkpoint.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
        results.append(item)

    if workers <= 1 or len(pending) <= 1:
        total = len(pending)
        for idx, job in enumerate(pending, 1):
            item = _worker(job)
            print(
                f"[{idx}/{total}] {item.get('label')} {item.get('ticker')} "
                f"{item.get('status')} n={item.get('metrics', {}).get('n')}",
                flush=True,
            )
            _persist(item)
        return results

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, job): job for job in pending}
        total = len(futures)
        for done_n, future in enumerate(as_completed(futures), 1):
            job = futures[future]
            try:
                item = future.result()
            except Exception as exc:
                item = {
                    "label": job[0],
                    "ticker": job[1],
                    "status": "failed",
                    "error": str(exc),
                    "metrics": _slim_metrics(None),
                    "trades": [],
                }
            print(
                f"[{done_n}/{total}] {item.get('label')} {item.get('ticker')} "
                f"{item.get('status')} n={item.get('metrics', {}).get('n')}",
                flush=True,
            )
            _persist(item)
    return results


def _load_published_a(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, dict[str, Any]]]]:
    published = json.loads(path.read_text(encoding="utf-8"))
    fs: dict[str, dict[str, Any]] = {}
    wf: dict[str, dict[str, dict[str, Any]]] = {name: {} for name, _, _ in WALKFORWARD_PERIODS}
    for ticker, row in (published.get("by_ticker") or {}).items():
        full = row.get("full_sample") or {}
        fs[str(ticker)] = {
            "label": "A",
            "ticker": str(ticker),
            "status": full.get("status") or row.get("status"),
            "error": full.get("error") or row.get("error"),
            "metrics": _slim_metrics(full.get("metrics")),
            "bars_1min": full.get("bars_1min"),
            "trades": [_slim_trade(t) for t in full.get("trades") or []],
            "source": "issue_100_published",
        }
        periods = ((row.get("walkforward") or {}).get("periods")) or {}
        for name, metrics in periods.items():
            if name not in wf:
                continue
            wf[name][str(ticker)] = {
                "label": f"A:{name}",
                "ticker": str(ticker),
                "status": "success",
                "metrics": _slim_metrics(metrics),
                "trades": [],
                "source": "issue_100_published",
            }
    return fs, wf


def _index_by_ticker(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["ticker"]): row for row in rows}


def _ohlc_records(df: pd.DataFrame, ts_from: str, ts_to: str) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    frame = df.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    lo = pd.Timestamp(ts_from)
    hi = pd.Timestamp(ts_to)
    frame = frame[(frame["timestamp"] >= lo) & (frame["timestamp"] < hi)]
    out = []
    for _, row in frame.iterrows():
        rec = {"timestamp": str(row["timestamp"])}
        for col in ("open", "high", "low", "close", "atr"):
            if col in row and pd.notna(row[col]):
                rec[col] = float(row[col])
        out.append(rec)
    return out


def _alrs_case(
    db: DBManager, config_a: dict[str, Any], config_b: dict[str, Any]
) -> dict[str, Any]:
    """Replay ALRS 2026-08-20 against swing-only B and swing+impulse + retest."""
    config_impulse = with_breakout_retest(with_impulse_methods(config_a))
    df_1m = db.select(
        "SELECT timestamp, open, high, low, close FROM trading.candles_1min_raw "
        "WHERE ticker=%s AND timestamp >= %s AND timestamp < %s ORDER BY timestamp",
        ("ALRS", "2026-08-18", DATE_TO),
    ).to_dataframe()
    for col in ("open", "high", "low", "close"):
        df_1m[col] = pd.to_numeric(df_1m[col], errors="coerce")

    ctx = build_strategy_context(db, "ALRS", config_impulse, df_1m=df_1m)
    if ctx.get("status") == "failed":
        return {"status": "failed", "error": ctx.get("error")}

    htf = ctx["htf_bars"].copy()
    htf["timestamp"] = pd.to_datetime(htf["timestamp"])
    delta = SIGNAL_TIMEFRAME_DELTAS.get("4h", pd.Timedelta(hours=4))
    zone_lo, zone_hi = IMPULSE_ZONE

    closes_above = []
    level_defined = pd.Timestamp("2026-08-14 12:00:00")
    for _, bar in htf.iterrows():
        bar_ts = pd.Timestamp(bar["timestamp"])
        close_ts = bar_ts + delta
        if bar_ts < level_defined:
            continue
        if close_ts > ALRS_VETO_TS:
            break
        if float(bar["close"]) > zone_hi:
            closes_above.append(
                {
                    "timestamp": str(bar["timestamp"]),
                    "close_ts": str(close_ts),
                    "close": float(bar["close"]),
                    "high": float(bar["high"]),
                }
            )

    tracker = LevelsTracker(ctx["levels"])
    fed = 0
    while fed < len(htf):
        bar = htf.iloc[fed]
        close_ts = pd.Timestamp(bar["timestamp"]) + delta
        if close_ts > ALRS_VETO_TS:
            break
        tracker.update(bar)
        fed += 1

    snapshot = tracker.get_levels_with_state()
    target = None
    if snapshot is not None and not snapshot.empty:
        resist = snapshot[snapshot["type"] == "resistance"].copy()
        if not resist.empty:
            resist["dist"] = (resist["level_price"] - IMPULSE_RESISTANCE).abs()
            row = resist.sort_values("dist").iloc[0]
            lid = row["level_id"]
            since = None
            try:
                since = tracker.bars_since_breakout(lid)
            except KeyError:
                since = None
            target = {
                "level_id": str(lid),
                "level_price": float(row["level_price"]),
                "zone_lower": float(row["zone_lower"]),
                "zone_upper": float(row["zone_upper"]),
                "method": str(row.get("method")),
                "defined_ts": str(row.get("defined_ts")),
                "state": str(row["state"]),
                "is_broken": bool(tracker.is_broken(lid)),
                "bars_since_breakout": since,
            }

    session = df_1m.copy()
    session["timestamp"] = pd.to_datetime(session["timestamp"])
    day = session[
        (session["timestamp"] >= pd.Timestamp("2026-08-20 10:00:00"))
        & (session["timestamp"] <= pd.Timestamp("2026-08-20 13:00:00"))
    ]
    session_high = None
    if not day.empty:
        idx = day["high"].idxmax()
        session_high = {
            "timestamp": str(day.loc[idx, "timestamp"]),
            "high": float(day.loc[idx, "high"]),
            "close": float(day.loc[idx, "close"]),
        }

    veto_ts = pd.to_datetime(df_1m["timestamp"])
    minute = ALRS_VETO_TS.floor("min")
    veto_rows = df_1m[(veto_ts == ALRS_VETO_TS) | (veto_ts.dt.floor("min") == minute)]
    veto_bar = None if veto_rows.empty else veto_rows.iloc[0]
    decisions = {}
    classify_at_veto = None
    for name, cfg in (("A", config_a), ("B", config_b), ("B_impulse", config_impulse)):
        ev = StrategyEvaluator(cfg)
        ctx_cfg = build_strategy_context(db, "ALRS", cfg, df_1m=df_1m)
        if ctx_cfg.get("status") == "failed":
            decisions[name] = {"action": None, "error": ctx_cfg.get("error")}
            continue
        ev.load_context(
            levels=ctx_cfg["levels"],
            ts_4h=ctx_cfg["ts_htf"],
            atr_by_ts=ctx_cfg["atr_by_ts"],
            buy_ts=ctx_cfg["buy_ts"],
            confirm_series=ctx_cfg["confirm_series"],
            signal_filter_series=ctx_cfg.get("signal_filter_series"),
            htf_bars=ctx_cfg.get("htf_bars"),
        )
        if veto_bar is None:
            decisions[name] = {"action": None, "error": "veto bar missing"}
            continue
        prior = df_1m[pd.to_datetime(df_1m["timestamp"]) < ALRS_VETO_TS]
        prev_high = float(prior.iloc[-1]["high"]) if not prior.empty else None
        ev._prev_high = prev_high
        decision = ev.check_entry(veto_bar)
        if decision is None:
            decisions[name] = {"action": None}
        else:
            decisions[name] = {
                "action": decision.get("action"),
                "entry_price": decision.get("entry_price"),
                "stop": decision.get("stop"),
                "take": decision.get("take"),
            }
        if name == "B_impulse" and ev._tracker is not None:
            a4 = ev._active_4h_ts(ALRS_VETO_TS)
            atr_val = float(ev.atr_by_ts.get(a4, 0.0) or 0.0) if a4 is not None else 0.0
            classify_at_veto = classify_retest_rejection(
                veto_bar,
                ev._tracker,
                atr=atr_val,
                params=ev._breakout_params,
                prev_high=prev_high,
            )

    consecutive = 0
    htf_closed = htf.copy()
    htf_closed["close_ts"] = htf_closed["timestamp"] + delta
    prior = htf_closed[
        (htf_closed["close_ts"] <= ALRS_VETO_TS)
        & (htf_closed["timestamp"] >= level_defined)
    ]
    for _, bar in prior.iloc[::-1].iterrows():
        if float(bar["close"]) > zone_hi:
            consecutive += 1
        else:
            break

    return {
        "status": "ok",
        "veto_ts": str(ALRS_VETO_TS),
        "veto_price": ALRS_VETO_PRICE,
        "impulse_resistance": IMPULSE_RESISTANCE,
        "impulse_zone": list(IMPULSE_ZONE),
        "close_above_zone_upper_before_veto": closes_above,
        "consecutive_4h_closes_above_zone": consecutive,
        "breakout_confirmed": bool(target and target["is_broken"]),
        "target_level": target,
        "session_high": session_high,
        "htf_bars": _ohlc_records(htf, "2026-08-13", "2026-08-21"),
        "intraday_1min": _ohlc_records(df_1m, "2026-08-20 10:00:00", "2026-08-20 13:00:01"),
        "decisions": decisions,
        "classify_at_veto_impulse": classify_at_veto,
        "htf_bars_fed": fed,
    }


def generate(
    tickers: list[str] | None,
    workers: int,
    skip_wf: bool,
    reuse_a: Path | None,
    run_a: bool,
    alrs_only: bool = False,
) -> Path:
    output = REPO_ROOT / "reports/Arctic/108_breakout-retest-validation/results.json"
    db = DBManager()
    try:
        snapshot = _load_strategy(db)
        universe = get_big_tickers(db)
        config_a = snapshot["config"]
        config_b = with_breakout_retest(config_a)
        print("Building ALRS case study...", flush=True)
        alrs = _alrs_case(db, config_a, config_b)
        print("ALRS case done.", flush=True)
    finally:
        db.close_pool()

    if alrs_only:
        if not output.exists():
            raise RuntimeError(f"--alrs-only requires existing {output}")
        payload = json.loads(output.read_text(encoding="utf-8"))
        payload["alrs_case"] = alrs
        payload["extracted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Updated ALRS case in {output}", flush=True)
        return output

    if tickers:
        missing = [t for t in tickers if t not in universe]
        if missing:
            raise RuntimeError(f"tickers not in get_big_tickers: {missing}")
        selected = tickers
    else:
        selected = universe
    if not selected:
        raise RuntimeError("empty ticker universe")

    fs_a: dict[str, dict[str, Any]] = {}
    wf: dict[str, dict[str, dict[str, Any]]] = {"A": {}, "B": {}}
    if reuse_a is not None:
        fs_a, wf_a = _load_published_a(reuse_a)
        wf["A"] = wf_a
        print(f"Reused published A from {reuse_a} ({len(fs_a)} tickers)", flush=True)

    jobs: list[tuple[Any, ...]] = []
    for ticker in selected:
        if run_a:
            jobs.append(("A", ticker, config_a, DATE_FROM, DATE_TO, False))
        jobs.append(("B", ticker, config_b, DATE_FROM, DATE_TO, True))
        if not skip_wf:
            for name, wf_from, wf_to in WALKFORWARD_PERIODS:
                if run_a:
                    jobs.append((f"A:{name}", ticker, config_a, wf_from, wf_to, False))
                jobs.append((f"B:{name}", ticker, config_b, wf_from, wf_to, False))

    print(f"Running {len(jobs)} backtests on {len(selected)} tickers", flush=True)
    raw = _run_jobs(jobs, workers)

    ran_a = _index_by_ticker([r for r in raw if r.get("label") == "A"])
    fs_a.update(ran_a)
    fs_b = _index_by_ticker([r for r in raw if r.get("label") == "B"])
    if not skip_wf:
        for name, _, _ in WALKFORWARD_PERIODS:
            if run_a:
                wf["A"][name] = _index_by_ticker(
                    [r for r in raw if r.get("label") == f"A:{name}"]
                )
            wf["B"][name] = _index_by_ticker(
                [r for r in raw if r.get("label") == f"B:{name}"]
            )

    rejection_total: Counter[str] = Counter()
    rejection_by_ticker = {}
    for ticker, row in fs_b.items():
        counts = row.get("rejection_counts") or {}
        rejection_by_ticker[ticker] = counts
        rejection_total.update(counts)

    payload = {
        "status": "success",
        "issue": 108,
        "extracted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strategy_id": snapshot["id"],
        "strategy_name": snapshot["name"],
        "in_paper_test": snapshot["in_paper_test"],
        "locked": snapshot["locked"],
        "locked_reference_id_untouched": LOCKED_REFERENCE_ID,
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
        "tickers": selected if run_a or not fs_a else sorted(set(selected) | set(fs_a)),
        "flags_at_start": snapshot["flags"],
        "config_a": config_a,
        "config_b": config_b,
        "reused_a": str(reuse_a) if reuse_a else None,
        "full_sample": {"A": fs_a, "B": fs_b},
        "walkforward_periods": [
            {"name": name, "date_from": a, "date_to": b}
            for name, a, b in WALKFORWARD_PERIODS
        ],
        "walkforward": wf,
        "rejections": {
            "by_ticker": rejection_by_ticker,
            "total": dict(rejection_total),
        },
        "alrs_case": alrs,
    }
    output = REPO_ROOT / "reports/Arctic/108_breakout-retest-validation/results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"Wrote {output}", flush=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Issue #108 validation inputs")
    parser.add_argument("--tickers", nargs="*", default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--skip-wf", action="store_true")
    parser.add_argument(
        "--reuse-a",
        default=str(
            REPO_ROOT / "analytics/issue-100-test-20260820-resistance-veto/results.json"
        ),
        help="Published Issue #100 results.json used as config A (skip A backtests)",
    )
    parser.add_argument(
        "--run-a",
        action="store_true",
        help="Also run config A (bit-for-bit check against Issue #100)",
    )
    parser.add_argument("--no-reuse-a", action="store_true")
    parser.add_argument(
        "--alrs-only",
        action="store_true",
        help="Refresh the ALRS case in an existing results.json without backtests",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    reuse = None if args.no_reuse_a else Path(args.reuse_a)
    generate(
        args.tickers,
        max(1, args.workers),
        args.skip_wf,
        reuse,
        args.run_a,
        args.alrs_only,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
