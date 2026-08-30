"""Extract Issue #124 Lab-universe inputs and run isolated A/B backtests.

Run from the repository root:

    python analytics/issue-124-sr-breakout-universe/extract_inputs.py

Reads unlocked `test_20260821` (id=118) as the #103-like baseline, builds
the same isolated A/B configs as Issue #119, and backtests every
`get_big_tickers(min_candles=250000)` name through `run_strategy_backtest`
(source stays on the trade).

Does not INSERT/UPDATE `trading.strategies` or `trading.backtest_results`.
Does not lock/paper-flag. Does not touch `test_20260731`, `test_20260820`,
or `test_20260821`.
"""
from __future__ import annotations

import argparse
import ast
import copy
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


ANALYSIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANALYSIS_DIR.parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
REPORTS_DIR = REPO_ROOT / "reports/Vulpec/124_sr-breakout-universe"
INPUTS_PATH = ANALYSIS_DIR / "inputs.json"
RESULTS_PATH = ANALYSIS_DIR / "results.json"

DATE_FROM = "2024-08-01"
DATE_TO = "2026-08-21"
PERIOD_LAST_DAY = "2026-08-20"
N_RUNS = 1
ISSUE = 124
MIN_CANDLES = 250_000

REFERENCE_ID = 118
REFERENCE_NAME = "test_20260821"
LOCKED_NAME = "test_20260731"
LOCKED_ID = 36
SWING_ONLY_NAME = "test_20260820"
SWING_ONLY_ID = 102
PROTECTED_NAMES = (LOCKED_NAME, SWING_ONLY_NAME, REFERENCE_NAME)

SOURCE_SUPPORT = "levels_sr_breakout_support"
SOURCE_RESISTANCE = "levels_sr_breakout_resistance"

EXPECTED_SHA_A = "d859dae12afc4316ceef9b4f28f310273f9e5d71dc0afc65acdf8d9a5454167e"
EXPECTED_SHA_B = "e7d5e90c555ba3bc853216492a96ec92e4a8e985a51ce136b28d452d1452482f"

FALLBACK_LEVELS = {
    "level_timeframe": "4h",
    "level_method": ["swing", "impulse"],
    "swing_window": 10,
    "impulse_body_ratio": 0.7,
    "impulse_atr_mult": 1.5,
    "zone_atr_mult": 0.5,
    "confirm_windows": [10],
}


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


def _engine() -> dict[str, Any]:
    _prepare_runtime()
    from app.analytics.pattern_registry import get_pattern_defaults, normalize_patterns
    from app.analytics.portfolio_simulator import get_tickers_by_volume
    from app.analytics.strategy_backtest import get_big_tickers, run_strategy_backtest
    from app.db.db_manager import DBManager

    return {
        "normalize_patterns": normalize_patterns,
        "get_pattern_defaults": get_pattern_defaults,
        "get_big_tickers": get_big_tickers,
        "get_tickers_by_volume": get_tickers_by_volume,
        "run_strategy_backtest": run_strategy_backtest,
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


def config_sha(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


def compact_trade(trade: dict[str, Any], ticker: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "entry_ts": str(trade.get("entry_ts")),
        "exit_ts": str(trade.get("exit_ts")),
        "entry_price": trade.get("entry_price"),
        "exit_price": trade.get("exit_price"),
        "exit_reason": trade.get("exit_reason"),
        "net_return_pct": trade.get("net_return_pct"),
        "bars_held": trade.get("bars_held"),
        "source": trade.get("source"),
    }


def _shared_top_level() -> dict[str, Any]:
    return {
        "confirm_windows": [10],
        "commission_pct": 0.06,
        "slippage_pct": 0.0,
        "risk_reward": {"risk": 1.0, "reward": 2.0},
        "entry_window": [7, 19],
        "n_runs": N_RUNS,
        "strategy_name": "levels_reversal",
    }


def _levels_from_reference(config: dict[str, Any]) -> dict[str, Any]:
    patterns = config.get("patterns") or {}
    raw = patterns.get("levels_reversal") if isinstance(patterns, dict) else {}
    if not isinstance(raw, dict) or not raw:
        return copy.deepcopy(FALLBACK_LEVELS)
    out = copy.deepcopy(FALLBACK_LEVELS)
    out.update(copy.deepcopy(raw))
    out["level_method"] = list(out.get("level_method") or ["swing", "impulse"])
    out["confirm_windows"] = list(out.get("confirm_windows") or [10])
    return out


def build_config_a(reference_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Isolated #103-like baseline: levels_reversal + signal_4h_buy."""
    levels = _levels_from_reference(reference_config or {})
    cfg = _shared_top_level()
    cfg["patterns"] = {
        "levels_reversal": levels,
        "signal_4h_buy": {},
    }
    return cfg


def build_config_b(
    reference_config: dict[str, Any] | None = None,
    get_defaults=None,
) -> dict[str, Any]:
    """Isolated candidate: only levels_sr_breakout + signal_4h_buy."""
    levels = _levels_from_reference(reference_config or {})
    defaults = {}
    if get_defaults is not None:
        defaults = copy.deepcopy(get_defaults("levels_sr_breakout") or {})
    composite = copy.deepcopy(defaults)
    composite.update(levels)
    cfg = _shared_top_level()
    cfg["patterns"] = {
        "levels_sr_breakout": composite,
        "signal_4h_buy": {},
    }
    return cfg


def assert_isolated(config: dict[str, Any], engine: str) -> None:
    patterns = config.get("patterns") or {}
    ids = set(patterns) if isinstance(patterns, dict) else set(patterns)
    if "level_breakout_retest" in ids:
        raise RuntimeError(f"{engine}: level_breakout_retest must stay off")
    if engine == "A" and ids != {"levels_reversal", "signal_4h_buy"}:
        raise RuntimeError(f"A patterns must be levels_reversal+signal_4h_buy, got {sorted(ids)}")
    if engine == "B" and ids != {"levels_sr_breakout", "signal_4h_buy"}:
        raise RuntimeError(f"B patterns must be levels_sr_breakout+signal_4h_buy, got {sorted(ids)}")
    if "levels_reversal" in ids and "levels_sr_breakout" in ids:
        raise RuntimeError("do not AND both entry engines for this universe run")


def resolve_tickers(big_tickers: list[str], config: dict[str, Any] | None = None) -> list[str]:
    """Lab universe is get_big_tickers. run_params.tickers is a Lab draft, not a bound."""
    del config
    return list(big_tickers)


def _fetch_strategy(db, strategy_id: int) -> dict[str, Any]:
    frame = db.select(
        "SELECT id, name, in_paper_test, locked, description, config "
        "FROM trading.strategies WHERE id=%s",
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
        "description": None if pd.isna(row.get("description")) else str(row["description"]),
        "config": _to_dict(row["config"]),
    }


def _fetch_flags(db) -> list[dict[str, Any]]:
    frame = db.select(
        "SELECT id, name, in_paper_test, locked "
        "FROM trading.strategies WHERE id IN (%s, %s, %s) OR name IN (%s, %s, %s) "
        "ORDER BY id",
        (LOCKED_ID, SWING_ONLY_ID, REFERENCE_ID, LOCKED_NAME, SWING_ONLY_NAME, REFERENCE_NAME),
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


def assert_protected_untouched(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    by_id = {row["id"]: row for row in after}
    for row in before:
        later = by_id.get(row["id"])
        if later is None:
            raise RuntimeError(f"protected strategy id={row['id']} disappeared")
        if later != row:
            raise RuntimeError(f"protected strategy changed: {row} -> {later}")


def snapshot(db, engine: dict[str, Any]) -> dict[str, Any]:
    flags = _fetch_flags(db)
    reference = _fetch_strategy(db, REFERENCE_ID)
    if reference["name"] != REFERENCE_NAME:
        raise RuntimeError(
            f"strategy id={REFERENCE_ID} name={reference['name']!r}, expected {REFERENCE_NAME!r}"
        )
    if reference["locked"] or reference["in_paper_test"]:
        raise RuntimeError(
            f"{REFERENCE_NAME} is locked/paper-flagged; Issue #124 must not run against paper"
        )
    normalized = engine["normalize_patterns"](reference["config"])
    config_a = engine["normalize_patterns"](build_config_a(normalized))
    config_b = engine["normalize_patterns"](
        build_config_b(normalized, get_defaults=engine["get_pattern_defaults"])
    )
    assert_isolated(config_a, "A")
    assert_isolated(config_b, "B")
    sha_a = config_sha(config_a)
    sha_b = config_sha(config_b)
    if sha_a != EXPECTED_SHA_A:
        raise RuntimeError(f"config A SHA {sha_a} != published #119 {EXPECTED_SHA_A}")
    if sha_b != EXPECTED_SHA_B:
        raise RuntimeError(f"config B SHA {sha_b} != published #119 {EXPECTED_SHA_B}")
    big_tickers = engine["get_big_tickers"](db, min_candles=MIN_CANDLES)
    tickers = resolve_tickers(big_tickers, normalized)
    if not tickers:
        raise RuntimeError("get_big_tickers returned an empty Lab universe")
    volume_order = [
        name
        for name in engine["get_tickers_by_volume"](
            db, date_from=DATE_FROM, date_to=DATE_TO
        )
        if name in set(tickers)
    ]
    for name in tickers:
        if name not in volume_order:
            volume_order.append(name)
    return {
        "extracted_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "issue": ISSUE,
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
        "period_last_day": PERIOD_LAST_DAY,
        "n_runs": N_RUNS,
        "min_candles": MIN_CANDLES,
        "reference": {
            "id": reference["id"],
            "name": reference["name"],
            "in_paper_test": reference["in_paper_test"],
            "locked": reference["locked"],
        },
        "flags_at_start": flags,
        "protected_names": list(PROTECTED_NAMES),
        "lab_universe": tickers,
        "volume_order": volume_order,
        "run_params_tickers_ignored": (normalized.get("run_params") or {}).get("tickers"),
        "configs": {
            "A": {
                "code": "A",
                "label": "levels_reversal + signal_4h_buy",
                "engine": "run_strategy_backtest",
                "config": config_a,
                "config_sha256": sha_a,
            },
            "B": {
                "code": "B",
                "label": "levels_sr_breakout + signal_4h_buy",
                "engine": "run_strategy_backtest",
                "config": config_b,
                "config_sha256": sha_b,
            },
        },
        "issue_119_reference_sha": {"A": EXPECTED_SHA_A, "B": EXPECTED_SHA_B},
        "notes": [
            "Isolated Lab-universe backtest (get_big_tickers), not a 50k portfolio replay.",
            "A/B configs are the published #119 SHA pair.",
            "Universe is not run_params.tickers and not LIVE_UNIVERSE.",
            "level_breakout_retest stays off. No strategy row is written.",
        ],
    }


def _run_one_ticker(payload: tuple[str, str, dict[str, Any], str, str]) -> dict[str, Any]:
    code, ticker, config, date_from, date_to = payload
    engine = _engine()
    db = engine["DBManager"]()
    try:
        raw = engine["run_strategy_backtest"](
            db, ticker, config, date_from=date_from, date_to=date_to
        )
    except Exception as exc:
        return {
            "code": code,
            "ticker": ticker,
            "status": "failed",
            "error": str(exc),
            "metrics": {},
            "trades": [],
            "n": 0,
        }
    finally:
        db.close_pool()
    if raw.get("status") != "success":
        return {
            "code": code,
            "ticker": ticker,
            "status": "failed",
            "error": raw.get("error") or "failed",
            "bars_1min": raw.get("bars_1min"),
            "metrics": {},
            "trades": [],
            "n": 0,
        }
    trades = [compact_trade(trade, ticker) for trade in raw.get("trades") or []]
    return {
        "code": code,
        "ticker": ticker,
        "status": "success",
        "engine": "run_strategy_backtest",
        "bars_1min": raw.get("bars_1min"),
        "metrics": _sanitize_metrics(raw.get("metrics")),
        "trades": trades,
        "n": len(trades),
        "config_sha256": config_sha(config),
    }


def _empty_results(inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue": ISSUE,
        "date_from": inputs.get("date_from"),
        "date_to": inputs.get("date_to"),
        "period_last_day": inputs.get("period_last_day"),
        "lab_universe": list(inputs.get("lab_universe") or []),
        "engine": "run_strategy_backtest",
        "runs": {"A": {"by_ticker": {}}, "B": {"by_ticker": {}}},
        "tickers_done": {"A": [], "B": []},
    }


def _load_results(inputs: dict[str, Any]) -> dict[str, Any]:
    if not RESULTS_PATH.exists():
        return _empty_results(inputs)
    with RESULTS_PATH.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    runs = payload.get("runs") or {}
    for code in ("A", "B"):
        block = runs.get(code) or {}
        if "by_ticker" not in block:
            block["by_ticker"] = {}
        runs[code] = block
    payload["runs"] = runs
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path} bytes={path.stat().st_size}", flush=True)


def run_universe(
    inputs: dict[str, Any],
    workers: int,
    tickers: list[str] | None,
    codes: list[str],
    force: bool,
) -> dict[str, Any]:
    universe = resolve_tickers(inputs["lab_universe"], None)
    selected = universe if not tickers else [name for name in tickers if name in universe]
    missing = [] if not tickers else [name for name in tickers if name not in universe]
    if missing:
        raise RuntimeError(f"requested tickers are not in the Lab universe: {missing}")
    wanted = [code for code in codes if code in {"A", "B"}]
    if not wanted:
        raise RuntimeError("codes must include A and/or B")

    results = _load_results(inputs)
    results["issue"] = ISSUE
    results["date_from"] = inputs["date_from"]
    results["date_to"] = inputs["date_to"]
    results["period_last_day"] = inputs["period_last_day"]
    results["lab_universe"] = universe
    results["n_runs"] = N_RUNS
    results["engine"] = "run_strategy_backtest"
    results["config_sha"] = {
        "A": inputs["configs"]["A"]["config_sha256"],
        "B": inputs["configs"]["B"]["config_sha256"],
    }

    pending: list[tuple[str, str, dict[str, Any], str, str]] = []
    for code in wanted:
        by_ticker = results["runs"][code].setdefault("by_ticker", {})
        for ticker in selected:
            existing = by_ticker.get(ticker)
            if existing and existing.get("status") == "success" and not force:
                print(f"skip {code}/{ticker}: already success n={existing.get('n')}", flush=True)
                continue
            pending.append(
                (
                    code,
                    ticker,
                    inputs["configs"][code]["config"],
                    inputs["date_from"],
                    inputs["date_to"],
                )
            )

    print(
        f"backtest universe={len(selected)} pending={len(pending)} "
        f"workers={max(1, workers)} codes={wanted}",
        flush=True,
    )
    if pending:
        with ProcessPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {executor.submit(_run_one_ticker, payload): payload[:2] for payload in pending}
            for completed, future in enumerate(as_completed(futures), 1):
                code, ticker = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    row = {
                        "code": code,
                        "ticker": ticker,
                        "status": "failed",
                        "error": str(exc),
                        "metrics": {},
                        "trades": [],
                        "n": 0,
                    }
                results["runs"][code]["by_ticker"][ticker] = row
                results["updated_at"] = datetime.now().isoformat(sep=" ", timespec="seconds")
                results["tickers_done"] = {
                    key: sorted((results["runs"][key].get("by_ticker") or {}))
                    for key in ("A", "B")
                }
                _write_json(RESULTS_PATH, results)
                _write_json(REPORTS_DIR / "results.json", results)
                print(
                    f"[{completed}/{len(pending)}] {code}/{ticker} "
                    f"status={row.get('status')} n={row.get('n')} error={row.get('error')}",
                    flush=True,
                )

    engine = _engine()
    db = engine["DBManager"]()
    try:
        flags_after = _fetch_flags(db)
    finally:
        db.close_pool()
    assert_protected_untouched(inputs["flags_at_start"], flags_after)
    results["flags_at_end"] = flags_after
    results["protected_untouched"] = True
    results["updated_at"] = datetime.now().isoformat(sep=" ", timespec="seconds")
    results["tickers_done"] = {
        key: sorted((results["runs"][key].get("by_ticker") or {}))
        for key in ("A", "B")
    }
    _write_json(RESULTS_PATH, results)
    _write_json(REPORTS_DIR / "results.json", results)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue #124 Lab-universe extract")
    parser.add_argument("--snapshot-only", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--tickers", nargs="*", default=None)
    parser.add_argument("--codes", nargs="*", default=["A", "B"])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    engine = _engine()
    db = engine["DBManager"]()
    try:
        payload = snapshot(db, engine)
    finally:
        db.close_pool()
    _write_json(INPUTS_PATH, payload)
    _write_json(REPORTS_DIR / "inputs.json", payload)
    print(
        f"universe={len(payload['lab_universe'])} "
        f"sha_a={payload['configs']['A']['config_sha256'][:12]} "
        f"sha_b={payload['configs']['B']['config_sha256'][:12]}",
        flush=True,
    )
    if args.snapshot_only:
        print("snapshot-only: skipped backtest", flush=True)
        return 0
    run_universe(
        payload,
        workers=args.workers,
        tickers=args.tickers,
        codes=list(args.codes),
        force=args.force,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
