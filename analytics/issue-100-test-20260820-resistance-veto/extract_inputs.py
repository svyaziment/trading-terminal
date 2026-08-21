"""Extract Issue #100 inputs and run test_20260820 on the full Lab universe.

Run from the repository root:

    python analytics/issue-100-test-20260820-resistance-veto/extract_inputs.py

Reads strategy id=102 (`test_20260820`) from PostgreSQL, ignores
`run_params.tickers`, and backtests every `get_big_tickers` name through
`run_strategy_backtest` / `run_walkforward` (StrategyEvaluator after #97).

Writes:
  - inputs.json   strategy snapshot + baseline Lab row, no secrets
  - results.json  full-sample + walk-forward per ticker (resumable)

Does not INSERT/UPDATE `trading.strategies` or `trading.backtest_results`.
"""
from __future__ import annotations

import argparse
import ast
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


ANALYSIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANALYSIS_DIR.parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
INPUTS_PATH = ANALYSIS_DIR / "inputs.json"
RESULTS_PATH = ANALYSIS_DIR / "results.json"

STRATEGY_ID = 102
STRATEGY_NAME = "test_20260820"
LOCKED_NAME = "test_20260731"
MIN_CANDLES = 250_000
N_RUNS = 1
FULL_SAMPLE_MONTHS = 24
EXPRESS_BASELINE_ID = 271
ALRS_VETO_TS = "2026-08-20 11:50:24"

WALKFORWARD_PERIODS = [
    ("2024-H2", "2024-07-01", "2025-01-01"),
    ("2025-H1", "2025-01-01", "2025-07-01"),
    ("2025-H2", "2025-07-01", "2026-01-01"),
    ("2026-H1", "2026-01-01", "2026-07-01"),
    ("2026-H2", "2026-07-01", "2027-01-01"),
]


def _load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        raw = env_path.read_bytes()
        text = None
        for encoding in ("utf-8", "utf-8-sig", "cp1251"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text:
            for line in text.splitlines():
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
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)


def _engine():
    _prepare_runtime()
    from app.analytics.pattern_registry import normalize_patterns
    from app.analytics.strategy_backtest import get_big_tickers, run_strategy_backtest, run_walkforward
    from app.db.db_manager import DBManager

    return {
        "normalize_patterns": normalize_patterns,
        "get_big_tickers": get_big_tickers,
        "run_strategy_backtest": run_strategy_backtest,
        "run_walkforward": run_walkforward,
        "DBManager": DBManager,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


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


def resolve_tickers(big_tickers: list[str], config: dict[str, Any] | None = None) -> list[str]:
    """Lab universe is get_big_tickers. run_params.tickers is a Lab draft, not a bound."""
    del config
    return list(big_tickers)


def walkforward_periods(max_ts: Any | None) -> list[tuple[str, str, str]]:
    periods = list(WALKFORWARD_PERIODS)
    if max_ts is None:
        return periods[:-1]
    stamp = pd.Timestamp(max_ts)
    if stamp < pd.Timestamp("2026-07-01"):
        return periods[:-1]
    return periods


def _compact_trade(trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_ts": str(trade.get("entry_ts")),
        "exit_ts": str(trade.get("exit_ts")),
        "entry_price": trade.get("entry_price"),
        "exit_price": trade.get("exit_price"),
        "exit_reason": trade.get("exit_reason"),
        "net_return_pct": trade.get("net_return_pct"),
        "bars_held": trade.get("bars_held"),
    }


def _sanitize_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(metrics or {})
    pf = out.get("pf")
    if isinstance(pf, float) and not math.isfinite(pf):
        out["pf"] = None
        out["pf_infinite"] = True
    elif pf == float("inf"):
        out["pf"] = None
        out["pf_infinite"] = True
    return _json_safe(out)


def _strip_trades(metrics: Any) -> Any:
    payload = _to_dict(metrics) if not isinstance(metrics, dict) else dict(metrics)
    payload.pop("trades", None)
    return _sanitize_metrics(payload)


def _fetch_strategy(db, strategy_id: int) -> dict[str, Any]:
    frame = db.select(
        "SELECT id, name, in_paper_test, locked, description, config "
        "FROM trading.strategies WHERE id=%s",
        (strategy_id,),
    ).to_dataframe()
    if frame.empty:
        raise RuntimeError(f"strategy id={strategy_id} not found")
    row = frame.iloc[0]
    config = _to_dict(row["config"])
    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "in_paper_test": bool(row["in_paper_test"]),
        "locked": bool(row["locked"]),
        "description": None if pd.isna(row.get("description")) else str(row["description"]),
        "config": config,
    }


def _fetch_flags(db) -> list[dict[str, Any]]:
    frame = db.select(
        "SELECT id, name, in_paper_test, locked "
        "FROM trading.strategies WHERE id=%s OR name=%s OR name=%s "
        "ORDER BY id",
        (STRATEGY_ID, STRATEGY_NAME, LOCKED_NAME),
    ).to_dataframe()
    rows = []
    for _, row in frame.iterrows():
        rows.append(
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "in_paper_test": bool(row["in_paper_test"]),
                "locked": bool(row["locked"]),
            }
        )
    return rows


def _fetch_baseline(db) -> list[dict[str, Any]]:
    frame = db.select(
        "SELECT id, ticker, test_type, depth, metrics, created_at::text AS created_at "
        "FROM trading.backtest_results WHERE strategy_id=%s ORDER BY id",
        (STRATEGY_ID,),
    ).to_dataframe()
    rows = []
    for _, row in frame.iterrows():
        rows.append(
            {
                "id": int(row["id"]),
                "ticker": str(row["ticker"]),
                "test_type": str(row["test_type"]),
                "depth": None if pd.isna(row.get("depth")) else str(row["depth"]),
                "created_at": None if pd.isna(row.get("created_at")) else str(row["created_at"]),
                "metrics": _strip_trades(row.get("metrics")),
            }
        )
    return rows


def _max_candle_ts(db) -> str | None:
    frame = db.select("SELECT max(timestamp)::text AS max_ts FROM trading.candles_1min_raw").to_dataframe()
    if frame.empty or pd.isna(frame.iloc[0]["max_ts"]):
        return None
    return str(frame.iloc[0]["max_ts"])


def snapshot(db, engine: dict[str, Any] | None = None) -> dict[str, Any]:
    engine = engine or _engine()
    strategy = _fetch_strategy(db, STRATEGY_ID)
    if strategy["name"] != STRATEGY_NAME:
        raise RuntimeError(
            f"strategy id={STRATEGY_ID} name={strategy['name']!r}, expected {STRATEGY_NAME!r}"
        )
    if strategy["locked"] or strategy["in_paper_test"]:
        raise RuntimeError(
            f"{STRATEGY_NAME} is locked/paper-flagged; Issue #100 must not run against the active paper strategy"
        )
    config = engine["normalize_patterns"](strategy["config"])
    big_tickers = engine["get_big_tickers"](db, min_candles=MIN_CANDLES)
    tickers = resolve_tickers(big_tickers, config)
    if not tickers:
        raise RuntimeError("get_big_tickers returned an empty Lab universe")
    max_ts = _max_candle_ts(db)
    date_from = (pd.Timestamp.now() - pd.DateOffset(months=FULL_SAMPLE_MONTHS)).strftime("%Y-%m-%d")
    levels = (config.get("patterns") or {}).get("levels_reversal") or {}
    return {
        "extracted_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "issue": 100,
        "strategy": {
            "id": strategy["id"],
            "name": strategy["name"],
            "in_paper_test": strategy["in_paper_test"],
            "locked": strategy["locked"],
            "description": strategy["description"],
            "patterns": config.get("patterns"),
            "confirm_windows": config.get("confirm_windows"),
            "risk_reward": config.get("risk_reward"),
            "commission_pct": config.get("commission_pct"),
            "slippage_pct": config.get("slippage_pct"),
            "run_params": config.get("run_params"),
            "level_method": levels.get("level_method"),
            "swing_window": levels.get("swing_window"),
            "zone_atr_mult": levels.get("zone_atr_mult"),
            "level_timeframe": levels.get("level_timeframe"),
        },
        "config": config,
        "flags_at_start": _fetch_flags(db),
        "lab_universe": tickers,
        "run_params_tickers_ignored": (config.get("run_params") or {}).get("tickers"),
        "min_candles": MIN_CANDLES,
        "n_runs": N_RUNS,
        "full_sample_months": FULL_SAMPLE_MONTHS,
        "date_from": date_from,
        "date_to": None,
        "max_1min_ts": max_ts,
        "walkforward_periods": [
            {"name": name, "date_from": start, "date_to": end}
            for name, start, end in walkforward_periods(max_ts)
        ],
        "baseline_backtest_results": _fetch_baseline(db),
        "express_baseline_id": EXPRESS_BASELINE_ID,
        "alrs_veto_ts": ALRS_VETO_TS,
        "engine": "run_strategy_backtest",
        "notes": [
            "Universe is get_big_tickers(min_candles=250000), not run_params.tickers and not LIVE_UNIVERSE.",
            "n_runs forced to 1 for a deterministic pass; strategy row is not updated.",
            "Results are written only to analytics/; trading.backtest_results is not rewritten.",
        ],
    }


def _run_one_ticker(payload: tuple[str, dict[str, Any], str, list[tuple[str, str, str]]]) -> dict[str, Any]:
    ticker, config, date_from, periods = payload
    engine = _engine()
    db = engine["DBManager"]()
    try:
        full = engine["run_strategy_backtest"](db, ticker, config, date_from=date_from, date_to=None)
        if full.get("status") == "success":
            full_out = {
                "status": "success",
                "ticker": ticker,
                "bars_1min": full.get("bars_1min"),
                "metrics": _sanitize_metrics(full.get("metrics")),
                "trades": [_compact_trade(trade) for trade in full.get("trades") or []],
            }
        else:
            full_out = {
                "status": "failed",
                "ticker": ticker,
                "error": full.get("error") or "failed",
                "metrics": {},
                "trades": [],
            }
        wf = engine["run_walkforward"](db, ticker, config, periods=periods)
        periods_out = {}
        for name, row in (wf.get("periods") or {}).items():
            item = dict(row)
            pf = item.get("pf")
            if isinstance(pf, float) and not math.isfinite(pf):
                item["pf"] = None
                item["pf_infinite"] = True
            periods_out[name] = _json_safe(item)
        wf_out = {
            "ticker": ticker,
            "periods": periods_out,
            "pf_gt1": wf.get("pf_gt1"),
            "min_pf": wf.get("min_pf"),
            "avg_pf": wf.get("avg_pf"),
        }
        return {
            "ticker": ticker,
            "status": full_out["status"],
            "error": full_out.get("error"),
            "full_sample": full_out,
            "walkforward": wf_out,
        }
    except Exception as exc:
        return {
            "ticker": ticker,
            "status": "failed",
            "error": str(exc),
            "full_sample": {"status": "failed", "ticker": ticker, "error": str(exc), "metrics": {}, "trades": []},
            "walkforward": {"ticker": ticker, "periods": {}, "pf_gt1": "0/0", "min_pf": None, "avg_pf": None},
        }
    finally:
        db.close_pool()


def _load_results() -> dict[str, Any]:
    if not RESULTS_PATH.exists():
        return {"by_ticker": {}}
    with RESULTS_PATH.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if "by_ticker" not in payload:
        payload["by_ticker"] = {}
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path} bytes={path.stat().st_size}", flush=True)


def run_backtest(
    inputs: dict[str, Any],
    workers: int,
    tickers: list[str] | None,
    force: bool,
) -> dict[str, Any]:
    config = dict(inputs["config"])
    config["n_runs"] = N_RUNS
    universe = resolve_tickers(inputs["lab_universe"], config)
    selected = universe if not tickers else [name for name in tickers if name in universe]
    missing = [] if not tickers else [name for name in tickers if name not in universe]
    if missing:
        raise RuntimeError(f"requested tickers are not in the Lab universe: {missing}")
    periods = [
        (row["name"], row["date_from"], row["date_to"])
        for row in inputs["walkforward_periods"]
    ]
    results = _load_results()
    results["issue"] = 100
    results["strategy_id"] = STRATEGY_ID
    results["strategy_name"] = STRATEGY_NAME
    results["date_from"] = inputs["date_from"]
    results["date_to"] = inputs["date_to"]
    results["n_runs"] = N_RUNS
    results["engine"] = "run_strategy_backtest"
    results["walkforward_periods"] = inputs["walkforward_periods"]
    results["lab_universe"] = universe
    results["alrs_veto_ts"] = ALRS_VETO_TS
    by_ticker: dict[str, Any] = results.get("by_ticker") or {}

    pending = []
    for ticker in selected:
        existing = by_ticker.get(ticker)
        if existing and existing.get("status") in {"success", "failed"} and not force:
            print(f"skip {ticker}: already {existing.get('status')}", flush=True)
            continue
        pending.append(ticker)

    payloads = [(ticker, config, inputs["date_from"], periods) for ticker in pending]
    print(
        f"backtest tickers={len(selected)} pending={len(pending)} workers={max(1, workers)} "
        f"date_from={inputs['date_from']}",
        flush=True,
    )
    if payloads:
        with ProcessPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {executor.submit(_run_one_ticker, payload): payload[0] for payload in payloads}
            for completed, future in enumerate(as_completed(futures), 1):
                ticker = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    row = {
                        "ticker": ticker,
                        "status": "failed",
                        "error": str(exc),
                        "full_sample": {
                            "status": "failed",
                            "ticker": ticker,
                            "error": str(exc),
                            "metrics": {},
                            "trades": [],
                        },
                        "walkforward": {
                            "ticker": ticker,
                            "periods": {},
                            "pf_gt1": "0/0",
                            "min_pf": None,
                            "avg_pf": None,
                        },
                    }
                by_ticker[ticker] = row
                results["by_ticker"] = by_ticker
                results["updated_at"] = datetime.now().isoformat(sep=" ", timespec="seconds")
                _write_json(RESULTS_PATH, results)
                n_trades = len((row.get("full_sample") or {}).get("trades") or [])
                print(
                    f"[{completed}/{len(pending)}] {ticker} status={row.get('status')} "
                    f"n={n_trades} error={row.get('error')}",
                    flush=True,
                )
    results["by_ticker"] = by_ticker
    results["updated_at"] = datetime.now().isoformat(sep=" ", timespec="seconds")
    results["tickers_done"] = sorted(by_ticker)
    _write_json(RESULTS_PATH, results)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue #100 extract + backtest")
    parser.add_argument("--snapshot-only", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--tickers", nargs="*", default=None, help="optional subset; must be in Lab universe")
    parser.add_argument("--force", action="store_true", help="re-run tickers already present in results.json")
    args = parser.parse_args()

    engine = _engine()
    db = engine["DBManager"]()
    try:
        payload = snapshot(db, engine)
    finally:
        db.close_pool()
    _write_json(INPUTS_PATH, payload)
    if args.snapshot_only:
        print("snapshot-only: skipped backtest", flush=True)
        return 0
    run_backtest(payload, workers=args.workers, tickers=args.tickers, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
