"""Generate Issue #130 portfolio inputs.

Default: replay published isolated C trades from Issue #129 through
Issue #44 slot rules (50k / 10k / max 5). Optional --source db re-runs
the per-ticker backtest via the #129 extractor (does not lock/overwrite
reference strategies).

Run from the repository root:

    python analytics/issue-130-sr-support-portfolio/generate_inputs.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd


ANALYSIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANALYSIS_DIR.parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
ISSUE129_DIR = REPO_ROOT / "analytics/issue-129-sr-support-universe"
REPORTS_DIR = REPO_ROOT / "reports/Vulpec/130_sr-support-portfolio"
DEFAULT_OUTPUT = REPORTS_DIR / "full_run.json"

ISSUE = 130
DATE_FROM = "2024-08-01"
DATE_TO = "2026-08-21"
PERIOD_LAST_DAY = "2026-08-20"
N_RUNS = 1
INITIAL_CAPITAL = 50_000.0
SLOT_SIZE = 10_000.0
MAX_POSITIONS = 5
EXPECTED_CANDIDATE_N = 4380
EXPECTED_SHA_C = "3b7864c4de2cb2c7d271be8c21c7d99c29bfd8a7dd05980b3c5497b6b2aedb1b"
SOURCE_C = "levels_sr_support"
FORBIDDEN_RETEST_KEYS = (
    "retest_window_bars",
    "retest_zone_atr",
    "entry_trigger_bullish",
    "stop_atr",
    "risk_reward",
)
LOCKED_NAME = "test_20260731"
LOCKED_ID = 36
SWING_ONLY_NAME = "test_20260820"
SWING_ONLY_ID = 102
REFERENCE_NAME = "test_20260821"
REFERENCE_ID = 118
PROTECTED_NAMES = (LOCKED_NAME, SWING_ONLY_NAME, REFERENCE_NAME)
ALRS_VETO_TS = pd.Timestamp("2026-08-20 11:50:24")
ALRS_VETO_PRICE = 19.80
VOLUME_ORDER_103 = [
    "FEES",
    "IRAO",
    "AFKS",
    "VTBR",
    "GAZP",
    "SNGS",
    "SBER",
    "RUAL",
    "ALRS",
    "GMKN",
    "MTLR",
    "CBOM",
    "NLMK",
    "ROSN",
    "RTKM",
    "MOEX",
    "FLOT",
    "MTSS",
    "NVTK",
    "PIKK",
    "TATN",
    "CHMF",
    "SIBN",
    "PLZL",
    "LKOH",
    "TRNFP",
    "MGNT",
    "PHOR",
]


def config_sha(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def assert_isolated(config: dict[str, Any]) -> None:
    patterns = config.get("patterns") or {}
    ids = set(patterns) if isinstance(patterns, dict) else set(patterns)
    if ids != {"levels_sr_support", "signal_4h_buy"}:
        raise RuntimeError(
            f"C patterns must be levels_sr_support+signal_4h_buy, got {sorted(ids)}"
        )
    params = patterns.get("levels_sr_support") if isinstance(patterns, dict) else None
    if isinstance(params, dict):
        leaked = [key for key in FORBIDDEN_RETEST_KEYS if key in params]
        if leaked:
            raise RuntimeError(f"C: retest keys leaked into support schema: {leaked}")


def resolve_tickers(big_tickers: list[str], volume_order: list[str]) -> list[str]:
    """Intersection of Lab universe with #103/#44 volume order, volume rank kept."""
    allowed = set(big_tickers)
    ordered = [name for name in volume_order if name in allowed]
    for name in big_tickers:
        if name not in ordered:
            ordered.append(name)
    return ordered


def alrs_hits(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    minute = ALRS_VETO_TS.floor("min")
    hits: list[dict[str, Any]] = []
    for trade in trades:
        if trade.get("ticker") != "ALRS":
            continue
        ts = pd.Timestamp(trade["entry_ts"])
        price = float(trade.get("entry_price") or 0.0)
        same_bar = ts == ALRS_VETO_TS or ts.floor("min") == minute
        same_price = abs(price - ALRS_VETO_PRICE) <= 0.015
        if same_bar and same_price:
            hits.append(
                {
                    "ticker": "ALRS",
                    "entry_ts": str(trade.get("entry_ts")),
                    "entry_price": trade.get("entry_price"),
                }
            )
    return hits


def _prepare_backend() -> None:
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)


def load_published_c(
    inputs_path: Path | None = None,
    results_path: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    inputs = load_json(Path(inputs_path or ISSUE129_DIR / "inputs.json"))
    results = load_json(Path(results_path or ISSUE129_DIR / "results.json"))
    block = (inputs.get("configs") or {}).get("C") or {}
    config = dict(block.get("config") or {})
    assert_isolated(config)
    sha = str(block.get("config_sha256") or config_sha(config))
    if sha != EXPECTED_SHA_C:
        raise RuntimeError(f"published C SHA {sha} != expected {EXPECTED_SHA_C}")
    if config_sha(config) != EXPECTED_SHA_C:
        raise RuntimeError(
            f"recomputed C SHA {config_sha(config)} != expected {EXPECTED_SHA_C}"
        )
    universe = list(inputs.get("lab_universe") or results.get("lab_universe") or [])
    volume_order = resolve_tickers(
        universe, list(inputs.get("volume_order") or VOLUME_ORDER_103)
    )
    by_ticker = ((results.get("runs") or {}).get("C") or {}).get("by_ticker") or {}
    candidates: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for ticker in volume_order:
        row = by_ticker.get(ticker) or {}
        if row.get("status") != "success":
            failed.append(
                {"ticker": ticker, "error": str(row.get("error") or "missing")}
            )
            continue
        for trade in row.get("trades") or []:
            payload = dict(trade)
            payload["ticker"] = ticker
            candidates.append(payload)
    if failed:
        raise RuntimeError(f"published C has failed tickers: {failed}")
    if len(candidates) != EXPECTED_CANDIDATE_N:
        raise RuntimeError(
            f"published C n={len(candidates)}, expected {EXPECTED_CANDIDATE_N}"
        )
    return {
        "config": config,
        "config_sha256": sha,
        "lab_universe": universe,
        "volume_order": volume_order,
        "failed_tickers": failed,
        "inputs": inputs,
    }, candidates, volume_order


def _attach_source(
    trades: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    index = {
        (str(row["ticker"]), str(row["entry_ts"])): row.get("source")
        for row in candidates
    }
    out = []
    for trade in trades:
        payload = dict(trade)
        payload["source"] = index.get((str(trade["ticker"]), str(trade["entry_ts"])))
        out.append(payload)
    return out


def replay_slots(
    candidates: list[dict[str, Any]],
    volume_order: list[str],
) -> dict[str, Any]:
    _prepare_backend()
    from app.analytics.portfolio_simulator import _portfolio_metrics, _replay_portfolio_trades

    volume_rank = {ticker: rank for rank, ticker in enumerate(volume_order)}
    trades, equity_curve, game_over, game_over_ts, skipped = _replay_portfolio_trades(
        candidates,
        volume_rank,
        initial_capital=INITIAL_CAPITAL,
        slot_size=SLOT_SIZE,
        max_positions=MAX_POSITIONS,
    )
    trades = _attach_source(trades, candidates)
    metrics = _portfolio_metrics(trades, equity_curve, INITIAL_CAPITAL)
    pf = metrics.get("profit_factor")
    if isinstance(pf, float) and not math.isfinite(pf):
        metrics["profit_factor"] = None
        metrics["pf_infinite"] = True
    return {
        "trades": trades,
        "equity_curve": equity_curve,
        "game_over": game_over,
        "game_over_ts": game_over_ts,
        "skipped_entries_no_slot": skipped,
        "metrics": metrics,
    }


def build_run(
    snapshot: dict[str, Any],
    candidates: list[dict[str, Any]],
    replay: dict[str, Any],
    *,
    source: str,
) -> dict[str, Any]:
    alrs_candidates = alrs_hits(candidates)
    alrs_portfolio = alrs_hits(replay["trades"])
    sources = sorted(
        {
            str(trade.get("source"))
            for trade in candidates
            if trade.get("source")
        }
    )
    return {
        "status": "success",
        "issue": ISSUE,
        "source": source,
        "engine": "run_strategy_backtest",
        "strategy": "levels_reversal",
        "strategy_id": None,
        "strategy_config_name": "levels_sr_support",
        "display_name": "levels_sr_support + signal_4h_buy",
        "in_paper_test": False,
        "locked": False,
        "locked_reference_id_untouched": LOCKED_ID,
        "swing_only_reference_id_untouched": SWING_ONLY_ID,
        "reference_id_untouched": REFERENCE_ID,
        "protected_names": list(PROTECTED_NAMES),
        "strategy_config": snapshot["config"],
        "config_sha256": snapshot["config_sha256"],
        "initial_capital_rub": INITIAL_CAPITAL,
        "slot_size_rub": SLOT_SIZE,
        "max_positions": MAX_POSITIONS,
        "n_runs": N_RUNS,
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
        "period_last_day": PERIOD_LAST_DAY,
        "tickers_volume_order": snapshot["volume_order"],
        "tickers": snapshot["volume_order"],
        "tickers_loaded": len(snapshot["volume_order"]),
        "lab_universe": snapshot["lab_universe"],
        "candidate_trades": len(candidates),
        "failed_tickers": snapshot.get("failed_tickers") or [],
        "candidate_sources": sources,
        "game_over": replay["game_over"],
        "game_over_ts": replay["game_over_ts"],
        "skipped_entries_no_slot": replay["skipped_entries_no_slot"],
        "metrics": replay["metrics"],
        "alrs_veto_check": {
            "timestamp": str(ALRS_VETO_TS),
            "price": ALRS_VETO_PRICE,
            "found_in_candidates": bool(alrs_candidates),
            "found_in_portfolio_trades": bool(alrs_portfolio),
            "candidate_hits": alrs_candidates,
            "portfolio_hits": alrs_portfolio,
        },
        "notes": [
            "Portfolio C uses isolated levels_sr_support trades from #129, "
            "not a source= filter of composite #124 B-mix.",
            "Exclusive #124 B-support 3811/1.51 is a composite label; runnable C is 4380/1.45.",
            "Do not lock/overwrite test_20260731, test_20260820, test_20260821.",
        ],
        "trades": replay["trades"],
        "candidates": candidates,
        "equity_curve": replay["equity_curve"],
    }


def replay_from_129(
    inputs_path: Path | None = None,
    results_path: Path | None = None,
) -> dict[str, Any]:
    snapshot, candidates, _volume = load_published_c(inputs_path, results_path)
    replay = replay_slots(candidates, snapshot["volume_order"])
    return build_run(snapshot, candidates, replay, source="issue-129")


def _run_from_db(workers: int) -> dict[str, Any]:
    import subprocess

    command = [
        sys.executable,
        str(ISSUE129_DIR / "extract_inputs.py"),
        "--workers",
        str(max(1, workers)),
    ]
    completed = subprocess.run(command, cwd=str(REPO_ROOT), check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"issue-129 extract_inputs.py failed with code {completed.returncode}"
        )
    result = replay_from_129()
    result["source"] = "db"
    return result


def generate(source: str, workers: int, output: Path | None = None) -> Path:
    if source == "db":
        result = _run_from_db(workers)
        result["source"] = "db"
    else:
        result = replay_from_129()
    path = Path(output or DEFAULT_OUTPUT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"Wrote {path}", flush=True)
    print(
        f"candidates={result['candidate_trades']} "
        f"n={result['metrics']['n_trades']} "
        f"pf={result['metrics']['profit_factor']} "
        f"equity={result['metrics']['final_equity_rub']}",
        flush=True,
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Issue #130 portfolio inputs")
    parser.add_argument(
        "--source",
        choices=("129", "db"),
        default="129",
        help="129 = published isolated C (default); db = re-run extract_inputs.py",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate(args.source, max(1, args.workers), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
