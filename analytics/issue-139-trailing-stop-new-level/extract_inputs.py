"""Extract Issue #139 portfolio inputs: baseline vs stepped trailing stop.

Strategy: `test_20260830_new_level` (id=126, locked) — patterns `signal_4h_buy`
+ `levels_sr_support`, RR 1:3, commission 0.06%, slippage 0, universe = the 28
`run_params.tickers`, full period 2024-08-01 .. timestamp < 2026-08-21.

The read-only snapshot re-uses the unified `StrategyEvaluator` (single brain,
identical entry logic to the production backtest) to enumerate every trade and
capture its bar-by-bar managed path, then evaluates BOTH exits on the SAME path:

  * A (baseline): fixed stop = support level, take = resistance level (1:3 gate).
  * B (trailing): identical entry / stop / take, plus the configurable stepped
    trailing table from `trailing.py` (defaults +2R->+1.5R, +2.5R->+2R).

This module READS the DB (candles, 4h levels, signals) but never WRITES trades to
the DB, never locks/paper-flags a strategy, and does not touch the production
portfolio_simulator / backtest / paper / sandbox path. The stepped trailing is an
ANALYTIC exit mode only (see `trailing.py`); the portfolio slot replay + A/B
comparison happen in `analysis.py` (which runs with no DB).

Run from the repository root:

    python analytics/issue-139-trailing-stop-new-level/extract_inputs.py
    python analytics/issue-139-trailing-stop-new-level/extract_inputs.py --tickers SBER
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve()
ANALYSIS_DIR = HERE.parent
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))
REPO_ROOT = ANALYSIS_DIR.parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
REPORTS_DIR = REPO_ROOT / "reports/Vulpec/139_trailing-stop-new-level"
INPUTS_PATH = ANALYSIS_DIR / "inputs.json"
RESULTS_PATH = ANALYSIS_DIR / "results.json"

ISSUE = 139
DATE_FROM = "2024-08-01"
DATE_TO = "2026-08-21"          # exclusive query bound (timestamp < DATE_TO)
PERIOD_LAST_DAY = "2026-08-20"
N_RUNS = 1
INITIAL_CAPITAL = 50_000.0
SLOT_SIZE = 10_000.0
MAX_POSITIONS = 5

STRATEGY_ID = 126
STRATEGY_NAME = "test_20260830_new_level"

# Static volume ranking reused from the #103/#44/#129/#130 family so slot
# competition is directly comparable to the published books. Same 28 names.
VOLUME_ORDER = [
    "FEES", "IRAO", "AFKS", "VTBR", "GAZP", "SNGS", "SBER", "RUAL", "ALRS",
    "GMKN", "MTLR", "CBOM", "NLMK", "ROSN", "RTKM", "MOEX", "FLOT", "MTSS",
    "NVTK", "PIKK", "TATN", "CHMF", "SIBN", "PLZL", "LKOH", "TRNFP", "MGNT",
    "PHOR",
]
EXPECTED_UNIVERSE = sorted(
    "AFKS ALRS CBOM CHMF FEES FLOT GAZP GMKN IRAO LKOH MGNT MOEX MTLR MTSS "
    "NLMK NVTK PHOR PIKK PLZL ROSN RTKM RUAL SBER SIBN SNGS TATN TRNFP VTBR".split()
)

PROTECTED = {
    126: "test_20260830_new_level",
    36: "test_20260731",
    102: "test_20260820",
    118: "test_20260821",
}

EXPECTED_PATTERNS = {"signal_4h_buy", "levels_sr_support"}


def _load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))
    if os.environ.get("POSTGRES_HOST") == "host.docker.internal":
        os.environ["POSTGRES_HOST"] = "localhost"
    os.environ.setdefault("PSTGRS_PWD", os.environ.get("POSTGRES_PASSWORD", ""))


def _prepare_runtime() -> None:
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    _load_env()
    # DBManager logs to stdout; route logging to stderr so analytics JSON stays clean.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)


def _to_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    text = str(raw)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, json.JSONDecodeError):
        pass
    try:
        parsed = ast.literal_eval(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def config_sha(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fetch_strategy(db, strategy_id: int) -> dict[str, Any]:
    frame = db.select(
        "SELECT id, name, in_paper_test, locked, config FROM trading.strategies WHERE id=%s",
        (strategy_id,),
    ).to_dataframe()
    if frame.empty:
        raise RuntimeError(f"strategy id={strategy_id} not found")
    row = frame.iloc[0]
    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "in_paper_test": bool(row["in_paper_test"]),
        "locked": bool(row["locked"]),
        "config": _to_dict(row["config"]),
    }


def _fetch_flags(db) -> list[dict[str, Any]]:
    ids = list(PROTECTED)
    names = list(PROTECTED.values())
    frame = db.select(
        "SELECT id, name, in_paper_test, locked FROM trading.strategies "
        "WHERE id = ANY(%s) OR name = ANY(%s) ORDER BY id",
        (ids, names),
    ).to_dataframe()
    out = []
    for _, row in frame.iterrows():
        out.append({
            "id": int(row["id"]),
            "name": str(row["name"]),
            "in_paper_test": bool(row["in_paper_test"]),
            "locked": bool(row["locked"]),
        })
    return out


def assert_protected_untouched(before, after) -> None:
    if sorted(map(str, before)) != sorted(map(str, after)):
        raise RuntimeError(f"protected strategies changed:\nbefore={before}\nafter={after}")


def assert_config_contract(cfg: dict[str, Any]) -> None:
    patterns = cfg.get("patterns") or {}
    ids = set(patterns) if isinstance(patterns, dict) else set(patterns)
    if ids != EXPECTED_PATTERNS:
        raise RuntimeError(f"config patterns {sorted(ids)} != expected {sorted(EXPECTED_PATTERNS)}")
    rr = cfg.get("risk_reward") or {}
    if int(rr.get("risk")) != 1 or int(rr.get("reward")) != 3:
        raise RuntimeError(f"config risk_reward {rr} != 1:3")
    if float(cfg.get("commission_pct", -1)) != 0.06:
        raise RuntimeError(f"config commission_pct != 0.06: {cfg.get('commission_pct')}")
    if float(cfg.get("slippage_pct", -1)) != 0.0:
        raise RuntimeError(f"config slippage_pct != 0: {cfg.get('slippage_pct')}")
    if list(cfg.get("confirm_windows") or []) != [10]:
        raise RuntimeError(f"config confirm_windows != [10]: {cfg.get('confirm_windows')}")
    support = patterns.get("levels_sr_support") if isinstance(patterns, dict) else {}
    forbidden = {"retest_window_bars", "retest_zone_atr", "entry_trigger_bullish", "stop_atr", "risk_reward"}
    leaked = [k for k in forbidden if k in (support or {})]
    if leaked:
        raise RuntimeError(f"retest keys leaked into levels_sr_support: {leaked}")


def _resolve_steps(raw: list[str] | None) -> list[dict[str, float]]:
    if not raw:
        from trailing import DEFAULT_STEPS
        return [dict(s) for s in DEFAULT_STEPS]
    steps = []
    for item in raw:
        trig, stop = item.split(":")
        steps.append({"trigger": float(trig), "stop": float(stop)})
    return steps



def _extract_ticker(payload: dict[str, Any]) -> dict[str, Any]:
    """Replay one ticker's trades and evaluate both exits on the same path."""
    from trailing import apply_trailing

    _prepare_runtime()
    from app.analytics.pattern_registry import normalize_patterns
    from app.analytics.strategy_context import build_strategy_context
    from app.analytics.strategy_engine import StrategyEvaluator
    from app.db.db_manager import DBManager

    ticker = payload["ticker"]
    config = normalize_patterns(payload["config"])
    commission_pct = float(config.get("commission_pct", 0.06))
    steps = payload["steps"]

    db = DBManager()
    try:
        df = db.select(
            "SELECT timestamp, open, high, low, close FROM trading.candles_1min_raw "
            "WHERE ticker=%s AND timestamp >= %s AND timestamp < %s ORDER BY timestamp",
            (ticker, payload["date_from"], payload["date_to"]),
        ).to_dataframe()
        if df.empty:
            return {"ticker": ticker, "status": "failed", "error": "no 1min candles", "trades": []}
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        ctx = build_strategy_context(db, ticker, config, df_1m=df)
        if ctx.get("status") != "ok":
            return {"ticker": ticker, "status": "failed", "error": ctx.get("error"), "trades": []}
    finally:
        db.close_pool()

    ev = StrategyEvaluator(ctx["config"])
    ev.load_context(levels=ctx["levels"], ts_4h=ctx["ts_htf"], atr_by_ts=ctx["atr_by_ts"],
                    buy_ts=ctx["buy_ts"], confirm_series=ctx["confirm_series"],
                    signal_filter_series=ctx.get("signal_filter_series"),
                    htf_bars=ctx.get("htf_bars"))

    ts_list = df["timestamp"].tolist()
    hi = df["high"].tolist()
    lo = df["low"].tolist()
    op = df["open"].tolist()
    cl = df["close"].tolist()
    n_bars = len(df)
    del df  # release the frame; the lists are enough for the brain loop
    print(f"[worker {os.getpid()}] {ticker} start bars={n_bars}", flush=True)

    trades: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    mismatch = 0
    for i in range(n_bars):
        row = {"timestamp": ts_list[i], "open": op[i], "high": hi[i], "low": lo[i], "close": cl[i]}
        if cur is not None:
            cur["path"].append((hi[i], lo[i]))
            cur["path_ts"].append(ts_list[i])
            if hi[i] >= cur["trigger2r"]:
                cur["reached_2r"] = True
        decision = ev.on_bar(row, idx=i)
        action = decision["action"]
        if action == "enter":
            entry_price = float(decision["entry_price"])
            stop = float(decision["stop"])
            take = float(decision["take"])
            risk = entry_price - stop
            cur = {
                "entry_price": entry_price, "stop": stop, "take": take,
                "entry_ts": str(ts_list[i]),
                "path": [], "path_ts": [], "reached_2r": False,
                "trigger2r": entry_price + 2.0 * risk,
            }
        elif action == "exit" and cur is not None:
            engine = decision["trade"]
            res = apply_trailing(
                cur["entry_price"], cur["stop"], cur["take"], cur["path"],
                commission_pct=commission_pct, steps=steps,
            )
            # The baseline replay must reproduce the engine's own exit on the same path.
            if abs(res["baseline"]["net_return_pct"] - float(engine["net_return_pct"])) > 1e-2:
                mismatch += 1
            b_index = res["trailing"]["exit_index"]
            b_exit_ts = (str(cur["path_ts"][b_index]) if b_index < len(cur["path_ts"])
                         else str(engine["exit_ts"]))
            trades.append({
                "ticker": ticker,
                "entry_ts": cur["entry_ts"],
                "entry_price": round(cur["entry_price"], 4),
                "stop": round(cur["stop"], 4),
                "take": round(cur["take"], 4),
                "risk_rub": res["risk_rub"],
                "bars_held": int(len(cur["path"])),
                "source": engine.get("source"),
                "reached_2r": bool(cur["reached_2r"]),
                "A": {
                    "exit_ts": str(engine["exit_ts"]),
                    "exit_price": float(engine["exit_price"]),
                    "exit_reason": str(engine["exit_reason"]),
                    "net_return_pct": float(engine["net_return_pct"]),
                },
                "B": {
                    "exit_ts": b_exit_ts,
                    "exit_price": res["trailing"]["exit_price"],
                    "exit_reason": res["trailing"]["exit_reason"],
                    "net_return_pct": res["trailing"]["net_return_pct"],
                    "step_reached": res["trailing"]["step_reached"],
                },
            })
            cur = None

    return {
        "ticker": ticker, "status": "success",
        "bars_1min": n_bars, "n_trades": len(trades),
        "baseline_replay_mismatches": mismatch, "trades": trades,
    }



def snapshot(db, steps: list[dict[str, float]]) -> dict[str, Any]:
    strategy = _fetch_strategy(db, STRATEGY_ID)
    if strategy["name"] != STRATEGY_NAME:
        raise RuntimeError(f"strategy id {STRATEGY_ID} is {strategy['name']}, expected {STRATEGY_NAME}")
    raw_cfg = strategy["config"]
    assert_config_contract(raw_cfg)
    run_params = _to_dict(raw_cfg.get("run_params", {}))
    universe = sorted(run_params.get("tickers") or [])
    if universe != EXPECTED_UNIVERSE:
        raise RuntimeError(f"run_params.tickers {universe} != expected 28 universe")
    volume_order = [t for t in VOLUME_ORDER if t in universe]
    for t in universe:
        if t not in volume_order:
            volume_order.append(t)
    return {
        "issue": ISSUE,
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
        "period_last_day": PERIOD_LAST_DAY,
        "n_runs": N_RUNS,
        "initial_capital_rub": INITIAL_CAPITAL,
        "slot_size_rub": SLOT_SIZE,
        "max_positions": MAX_POSITIONS,
        "strategy_id": STRATEGY_ID,
        "strategy_name": STRATEGY_NAME,
        "strategy_locked": strategy["locked"],
        "strategy_in_paper_test": strategy["in_paper_test"],
        "config": _json_safe(raw_cfg),
        "config_sha256": config_sha(raw_cfg),
        "universe": universe,
        "volume_order": volume_order,
        "trailing_steps": steps,
        "flags_at_start": _fetch_flags(db),
        "protected": PROTECTED,
    }


def run_extract(workers, tickers, steps, inputs) -> dict[str, Any]:
    universe = tickers or inputs["volume_order"]
    cache_dir = REPORTS_DIR / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    jobs = [{"ticker": t, "config": inputs["config"], "date_from": DATE_FROM,
             "date_to": DATE_TO, "steps": steps} for t in universe]
    per_ticker: dict[str, Any] = {}
    pending = []
    for job in jobs:
        cache_file = cache_dir / f"{job['ticker']}.json"
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if cached.get("status") == "success":
                    per_ticker[job["ticker"]] = cached
                    continue
            except (ValueError, OSError):
                pass
        pending.append(job)
    print(f"extract universe={len(universe)} cached={len(per_ticker)} pending={len(pending)} "
          f"workers={max(1, workers)} steps={steps}", flush=True)
    if pending:
        with ProcessPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = {ex.submit(_extract_ticker, job): job["ticker"] for job in pending}
            done = 0
            for fut in as_completed(futs):
                ticker = futs[fut]
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001
                    res = {"ticker": ticker, "status": "failed", "error": str(exc), "trades": []}
                per_ticker[ticker] = res
                if res.get("status") == "success":
                    (cache_dir / f"{ticker}.json").write_text(
                        json.dumps(_json_safe(res), ensure_ascii=False, default=str),
                        encoding="utf-8")
                done += 1
                print(f"[{done}/{len(pending)}] {ticker} status={res.get('status')} "
                      f"n={res.get('n_trades')} mm={res.get('baseline_replay_mismatches')} "
                      f"err={res.get('error')}", flush=True)


    all_trades: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    mismatches = 0
    for t in inputs["volume_order"]:
        row = per_ticker.get(t) or {}
        if row.get("status") != "success":
            failed.append({"ticker": t, "error": str(row.get("error") or "missing")})
            continue
        mismatches += int(row.get("baseline_replay_mismatches") or 0)
        all_trades.extend(row["trades"])
    reached = sum(1 for tr in all_trades if tr["reached_2r"])
    return {
        **{k: v for k, v in inputs.items() if k != "flags_at_start"},
        "status": "success",
        "engine": "StrategyEvaluator.on_bar (identical entry brain to run_strategy_backtest)",
        "strategy_config_name": STRATEGY_NAME,
        "failed_tickers": failed,
        "baseline_replay_mismatches": mismatches,
        "candidate_trades": len(all_trades),
        "reached_2r_trades": reached,
        "trades": all_trades,
    }



def main() -> int:
    parser = argparse.ArgumentParser(description="Issue #139 baseline-vs-trailing extract")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--tickers", nargs="*", default=None,
                        help="subset for a smoke run (default: full 28-name universe)")
    parser.add_argument("--step", action="append", dest="steps", metavar="TRIG:STOP",
                        help="override a trailing step in R, e.g. --step 2:1.5 (repeatable)")
    args = parser.parse_args()

    steps = _resolve_steps(args.steps)
    _prepare_runtime()
    from app.db.db_manager import DBManager

    db = DBManager()
    try:
        inputs = snapshot(db, steps)
        flags_before = inputs["flags_at_start"]
    finally:
        db.close_pool()

    INPUTS_PATH.write_text(
        json.dumps(_json_safe(inputs), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "inputs.json").write_text(INPUTS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"universe={len(inputs['universe'])} sha126={inputs['config_sha256'][:12]} "
          f"locked={inputs['strategy_locked']}", flush=True)

    result = run_extract(args.workers, args.tickers, steps, inputs)

    if not args.tickers:
        db = DBManager()
        try:
            flags_after = _fetch_flags(db)
        finally:
            db.close_pool()
        assert_protected_untouched(flags_before, flags_after)
        result["flags_at_end"] = flags_after
        result["protected_untouched"] = True
        RESULTS_PATH.write_text(
            json.dumps(_json_safe(result), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        (REPORTS_DIR / "results.json").write_text(
            RESULTS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"WROTE {RESULTS_PATH}", flush=True)
    print(f"candidate_trades={result['candidate_trades']} "
          f"reached_2r={result['reached_2r_trades']} "
          f"mismatches={result['baseline_replay_mismatches']} "
          f"failed={len(result['failed_tickers'])}", flush=True)
    if result["failed_tickers"]:
        return 1
    if result["baseline_replay_mismatches"] > 0:
        print(f"WARNING: baseline replay mismatched engine on "
              f"{result['baseline_replay_mismatches']} trades", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

