"""Issue #147 v4: production trailing-stop parity gate (subprocess shards, no multiprocessing).

v4 removes ProcessPoolExecutor entirely: on this Windows host detached parents
sporadically fail to spawn pool workers (_winapi.DuplicateHandle -> WinError 5).
Shards are plain OS processes launched by bash (`--stage shard --book X
--shard-index i --shard-count N`); each shard processes its tickers sequentially
in-process and writes per-ticker cache. `--stage assemble` replays slots from
cache and builds extract.json + report.json + report.md.

Cache is versioned: cache/<book>-<sha8(normalized book config)>/<TICKER>.json,
so a config change invalidates a book unambiguously without fingerprint files.

Books and gate criteria are unchanged from v3 (run.md v3):
  A_prod  — trailing off, tight parity vs #139 book A;
  B_prod  — ref139 injected, directional/scale gate vs #139 book B;
  B_default — steps imported from trading_config.TRAILING_STOP (ultra_late_tight),
              directional/scale gate vs #143 grid row.
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
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve()
PKG_DIR = HERE.parent
REPO_ROOT = PKG_DIR.parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
ANALYTICS_139_DIR = REPO_ROOT / "analytics" / "issue-139-trailing-stop-new-level"
ANALYTICS_143_DIR = REPO_ROOT / "analytics" / "issue-143-trailing-robustness"
SUMMARY_139_PATH = ANALYTICS_139_DIR / "summary.json"
SUMMARY_143_PATH = ANALYTICS_143_DIR / "summary.json"
RESULTS_139_PATH = ANALYTICS_139_DIR / "results.json"
CACHE_DIR = PKG_DIR / "cache"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

ISSUE = 147
STRATEGY_ID = 126
STRATEGY_NAME = "test_20260830_new_level"
DATE_FROM = "2024-08-01"
DATE_TO = "2026-08-21"
INITIAL_CAPITAL = 50_000.0
SLOT_SIZE = 10_000.0
MAX_POSITIONS = 5
BOOKS = ("A_prod", "B_prod", "B_default")

VOLUME_ORDER = [
    "FEES", "IRAO", "AFKS", "VTBR", "GAZP", "SNGS", "SBER", "RUAL", "ALRS",
    "GMKN", "MTLR", "CBOM", "NLMK", "ROSN", "RTKM", "MOEX", "FLOT", "MTSS",
    "NVTK", "PIKK", "TATN", "CHMF", "SIBN", "PLZL", "LKOH", "TRNFP", "MGNT",
    "PHOR",
]

REF139_STEPS = [{"trigger": 2.0, "stop": 1.5}, {"trigger": 2.5, "stop": 2.0}]

PROTECTED = {126: STRATEGY_NAME, 36: "test_20260731", 102: "test_20260820", 118: "test_20260821"}

IDENTITY_MAP = {"stop": "stop", "take": "take", "trailing": "trailing"}
B_MAP = {"stop": "initial_stop", "take": "take", "trailing": "trailing"}


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


def _to_dict(raw: Any) -> dict:
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


def config_sha(config: dict) -> str:
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fetch_strategy(db, strategy_id: int) -> dict:
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


def _fetch_flags(db) -> List[dict]:
    ids = list(PROTECTED)
    names = list(PROTECTED.values())
    frame = db.select(
        "SELECT id, name, in_paper_test, locked FROM trading.strategies "
        "WHERE id = ANY(%s) OR name = ANY(%s) ORDER BY id",
        (ids, names),
    ).to_dataframe()
    return [
        {"id": int(r["id"]), "name": str(r["name"]),
         "in_paper_test": bool(r["in_paper_test"]), "locked": bool(r["locked"])}
        for _, r in frame.iterrows()
    ]


def _assert_protected(before: List[dict], after: List[dict]) -> None:
    if sorted(map(str, before)) != sorted(map(str, after)):
        raise RuntimeError(f"protected strategies changed:\nbefore={before}\nafter={after}")


def _default_steps() -> List[Dict[str, float]]:
    from app.analytics.trading_config import TRAILING_STOP
    return [dict(s) for s in TRAILING_STOP["steps"]]


def book_config(raw_cfg: dict, book: str, default_steps: List[Dict[str, float]]) -> dict:
    from app.analytics.pattern_registry import normalize_patterns
    cfg = dict(raw_cfg)
    if book == "A_prod":
        cfg.pop("trailing_stop", None)
    elif book == "B_prod":
        cfg["trailing_stop"] = {"enabled": True, "steps": [dict(s) for s in REF139_STEPS]}
    elif book == "B_default":
        cfg["trailing_stop"] = {"enabled": True, "steps": [dict(s) for s in default_steps]}
    else:
        raise ValueError(f"unknown book {book}")
    return normalize_patterns(cfg)


def book_cache_dir(book: str, sha: str) -> Path:
    return CACHE_DIR / f"{book}-{sha[:8]}"


def universe_of(raw_cfg: dict) -> List[str]:
    run_params = _to_dict(raw_cfg.get("run_params", {}))
    universe = sorted(run_params.get("tickers") or [])
    if len(universe) != 28:
        raise RuntimeError(f"expected 28 tickers, got {len(universe)}")
    return [t for t in VOLUME_ORDER if t in universe]


def _ticker_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    """One ticker, one book config, through the PRODUCTION plugin path (in-process)."""
    logging.basicConfig(level=logging.CRITICAL, stream=sys.stderr)
    t0 = time.time()
    ticker = payload["ticker"]
    from app.db.db_manager import DBManager
    from app.analytics.portfolio_backtest import run_portfolio_backtest
    from app.analytics.strategies.registry import register_default_strategies
    register_default_strategies()

    db = DBManager()
    try:
        pr = run_portfolio_backtest(
            db, "levels_reversal", payload["config"], [ticker],
            date_from=payload["date_from"], date_to=payload["date_to"],
        )
    finally:
        db.close_pool()
    tr = (pr.get("results") or [{}])[0]
    if tr.get("status") != "success":
        return {"ticker": ticker, "status": "failed",
                "error": tr.get("error", "failed"), "trades": []}
    return {"ticker": ticker, "status": "success",
            "trades": _json_safe(tr.get("trades", [])),
            "elapsed_sec": round(time.time() - t0, 1)}


def stage_print_shas() -> int:
    _load_env()
    from app.db.db_manager import DBManager
    db = DBManager()
    try:
        raw_cfg = _fetch_strategy(db, STRATEGY_ID)["config"]
    finally:
        db.close_pool()
    steps = _default_steps()
    for book in BOOKS:
        sha = config_sha(book_config(raw_cfg, book, steps))
        print(f"{book} {sha[:8]}", flush=True)
    return 0


def stage_shard(book: str, shard_index: int, shard_count: int) -> int:
    _load_env()
    from app.db.db_manager import DBManager
    db = DBManager()
    try:
        raw_cfg = _fetch_strategy(db, STRATEGY_ID)["config"]
    finally:
        db.close_pool()
    steps = _default_steps()
    cfg = book_config(raw_cfg, book, steps)
    sha = config_sha(cfg)
    cache = book_cache_dir(book, sha)
    cache.mkdir(parents=True, exist_ok=True)
    tickers = universe_of(raw_cfg)
    shard = tickers[shard_index::shard_count]
    print(f"[{book}] shard {shard_index}/{shard_count} tickers={len(shard)} "
          f"cache={cache.name}", flush=True)
    for t in shard:
        cf = cache / f"{t}.json"
        if cf.exists():
            try:
                if json.loads(cf.read_text(encoding="utf-8")).get("status") == "success":
                    print(f"[{book}] {t} cached", flush=True)
                    continue
            except (ValueError, OSError):
                pass
        res = _ticker_job({"ticker": t, "config": cfg,
                           "date_from": DATE_FROM, "date_to": DATE_TO})
        if res.get("status") == "success":
            cf.write_text(json.dumps(res, ensure_ascii=False, default=str), encoding="utf-8")
            print(f"[{book}] {t} ok n={len(res['trades'])} {res.get('elapsed_sec')}s", flush=True)
        else:
            print(f"[{book}] {t} FAILED {res.get('error')}", flush=True)
            return 1
    return 0


def build_book_from_cache(book: str, raw_cfg: dict,
                          default_steps: List[Dict[str, float]]) -> Dict[str, Any]:
    from app.analytics.portfolio_simulator import _replay_portfolio_trades, _portfolio_metrics
    cfg = book_config(raw_cfg, book, default_steps)
    sha = config_sha(cfg)
    cache = book_cache_dir(book, sha)
    tickers = universe_of(raw_cfg)
    candidates: List[Dict[str, Any]] = []
    missing: List[str] = []
    for t in tickers:
        cf = cache / f"{t}.json"
        if not cf.exists():
            missing.append(t)
            continue
        row = json.loads(cf.read_text(encoding="utf-8"))
        if row.get("status") != "success":
            missing.append(t)
            continue
        candidates.extend({"ticker": t, **tr} for tr in row["trades"])
    if missing:
        return {"status": "failed", "book_label": book, "missing_tickers": missing}
    volume_rank = {t: i for i, t in enumerate(tickers)}
    trades, equity_curve, game_over, go_ts, skipped = _replay_portfolio_trades(
        candidates, volume_rank, INITIAL_CAPITAL, SLOT_SIZE, MAX_POSITIONS)
    metrics = _portfolio_metrics(trades, equity_curve, INITIAL_CAPITAL)
    return {
        "status": "success", "book_label": book, "config_sha256": sha,
        "tickers_loaded": len(tickers), "candidate_trades": len(candidates),
        "failed_tickers": [], "game_over": game_over, "game_over_ts": go_ts,
        "skipped_entries_no_slot": skipped, "metrics": metrics,
        "candidate_entries": _json_safe([[c["ticker"], str(c["entry_ts"])] for c in candidates]),
        "trades": _json_safe(trades), "equity_curve": _json_safe(equity_curve),
    }


def load_ref139_summary() -> Dict[str, Any]:
    with SUMMARY_139_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def load_ref143_summary() -> Dict[str, Any]:
    with SUMMARY_143_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def load_ref139_entries() -> set:
    with RESULTS_139_PATH.open(encoding="utf-8") as f:
        result = json.load(f)
    return {(tr["ticker"], str(tr["entry_ts"])) for tr in result.get("trades", [])}


def daily_max_drawdown(trades: List[Dict[str, Any]]) -> float:
    if not trades:
        return 0.0
    by_day: Dict[str, float] = {}
    for tr in trades:
        ts = str(tr.get("exit_ts") or "")[:10]
        if not ts:
            continue
        by_day[ts] = by_day.get(ts, 0.0) + float(tr.get("pnl_rub") or 0.0)
    if not by_day:
        return 0.0
    eq = INITIAL_CAPITAL
    peak = eq
    max_dd = 0.0
    for day in sorted(by_day):
        eq += by_day[day]
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100.0)
    return round(max_dd, 2)


def book_entries(trades: List[Dict[str, Any]]) -> set:
    return {(tr["ticker"], str(tr["entry_ts"])) for tr in trades}


def compare_books(prod: Dict[str, Any], ref: Dict[str, Any], label: str,
                  reason_map: Dict[str, str]) -> Dict[str, Any]:
    pm = dict(prod.get("metrics", {}))
    rb = ref.get("book_B_trailing", ref.get("book_A_baseline", {}))
    daily_dd = daily_max_drawdown(prod.get("trades", []))
    comparisons = []
    for field, tol in [("final_equity_rub", 206.35), ("n_trades", 0),
                       ("profit_factor", 0.05), ("win_rate", 0.5), ("max_drawdown_pct", 0.5)]:
        pv = daily_dd if field == "max_drawdown_pct" else pm.get(field)
        rv = rb.get(field, rb.get("win_rate_pct") if field == "win_rate" else None)
        if pv is None or rv is None:
            comparisons.append({"field": field, "prod": pv, "ref": rv, "ok": False, "tol": tol})
            continue
        delta = abs(float(pv) - float(rv))
        comparisons.append({"field": field, "prod": pv, "ref": rv,
                            "delta": round(delta, 4), "tol": tol, "ok": delta <= tol})
    prod_exits = pm.get("exit_reason_counts", {})
    ref_exits = rb.get("exit_type_counts", {})
    mapped: Dict[str, int] = {}
    for reason, count in prod_exits.items():
        key = reason_map.get(reason, reason)
        mapped[key] = mapped.get(key, 0) + count
    return {
        "label": label, "field_comparisons": comparisons,
        "all_fields_ok": all(c.get("ok", False) for c in comparisons),
        "daily_max_drawdown_pct": daily_dd,
        "event_max_drawdown_pct": pm.get("max_drawdown_pct"),
        "exit_reason_prod": prod_exits, "exit_reason_ref": ref_exits,
        "exit_reason_mapped_prod": mapped, "exit_reasons_match": mapped == ref_exits,
    }


def stage_assemble() -> int:
    _load_env()
    from app.db.db_manager import DBManager
    db = DBManager()
    try:
        strategy = _fetch_strategy(db, STRATEGY_ID)
        flags_before = _fetch_flags(db)
    finally:
        db.close_pool()
    if strategy["name"] != STRATEGY_NAME:
        raise RuntimeError(f"strategy {STRATEGY_ID} is {strategy['name']}")
    raw_cfg = strategy["config"]
    steps = _default_steps()

    books = {}
    for book in BOOKS:
        books[book] = build_book_from_cache(book, raw_cfg, steps)
        print(f"[{book}] assembled status={books[book].get('status')} "
              f"missing={books[book].get('missing_tickers', [])}", flush=True)

    db = DBManager()
    try:
        flags_after = _fetch_flags(db)
    finally:
        db.close_pool()
    _assert_protected(flags_before, flags_after)

    for book in BOOKS:
        if books[book].get("status") != "success":
            print(f"ASSEMBLE_FAIL: {book} missing {books[book].get('missing_tickers')}",
                  file=sys.stderr)
            return 1

    extract = {
        "issue": ISSUE, "status": "success",
        "strategy_id": STRATEGY_ID, "strategy_name": STRATEGY_NAME,
        "config_sha256": config_sha(raw_cfg),
        "default_steps_source": "trading_config.TRAILING_STOP",
        "default_steps": steps,
        "date_from": DATE_FROM, "date_to": DATE_TO,
        "universe": universe_of(raw_cfg), "volume_order": [t for t in VOLUME_ORDER],
        "initial_capital_rub": INITIAL_CAPITAL, "slot_size_rub": SLOT_SIZE,
        "max_positions": MAX_POSITIONS, "protected_untouched": True,
        "books": _json_safe(books),
    }
    (PKG_DIR / "extract.json").write_text(
        json.dumps(extract, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    summary = stage_report(extract)
    g = summary["gate"]
    print(f"GATE: A_prod={'PASS' if g['A_prod_parity'] else 'FAIL'} "
          f"B_prod={'PASS' if g['B_prod_gate'] else 'FAIL'} "
          f"B_default={'PASS' if g['B_default_gate'] else 'FAIL'} "
          f"OVERALL={'PASS' if g['overall'] else 'FAIL'}", flush=True)
    return 0 if g["overall"] else 1


def stage_report(extract: Dict[str, Any]) -> Dict[str, Any]:
    ref139 = load_ref139_summary()
    ref143 = load_ref143_summary()
    ref_entries = load_ref139_entries()
    books = extract["books"]

    cmp_a = compare_books(books["A_prod"], {"book_A_baseline": ref139.get("book_A_baseline", {})},
                          "A_prod vs #139 A", IDENTITY_MAP)
    cmp_b = compare_books(books["B_prod"], {"book_B_trailing": ref139.get("book_B_trailing", {})},
                          "B_prod vs #139 B", B_MAP)
    ult_row = next((g for g in ref143.get("grid_rows", [])
                    if g.get("grid_id") == "ultra_late_tight"), None)
    cmp_d = compare_books(books["B_default"],
                          {"book_B_trailing": ult_row} if ult_row else {},
                          "B_default vs #143 ultra_late_tight", B_MAP)

    cand_a = {(t, ts) for t, ts in books["A_prod"].get("candidate_entries", [])}
    cand_b = {(t, ts) for t, ts in books["B_prod"].get("candidate_entries", [])}
    book_a_set = book_entries(books["A_prod"].get("trades", []))
    book_b_set = book_entries(books["B_prod"].get("trades", []))
    entry_delta = {
        "ref139_candidates": len(ref_entries),
        "level": "candidates (pre slot-replay)",
        "A_prod_new_vs_ref": len(cand_a - ref_entries),
        "A_prod_missing_vs_ref": len(ref_entries - cand_a),
        "B_prod_new_vs_ref": len(cand_b - ref_entries),
        "B_prod_missing_vs_ref": len(ref_entries - cand_b),
        "book_level": {
            "A_prod_new_vs_ref": len(book_a_set - ref_entries),
            "A_prod_missing_vs_ref": len(ref_entries - book_a_set),
            "B_prod_new_vs_ref": len(book_b_set - ref_entries),
            "B_prod_missing_vs_ref": len(ref_entries - book_b_set),
        },
    }

    ma = books["A_prod"].get("metrics", {})
    mb = books["B_prod"].get("metrics", {})
    md_ = books["B_default"].get("metrics", {})
    dd_b = cmp_b["daily_max_drawdown_pct"]
    dd_d = cmp_d["daily_max_drawdown_pct"]
    exits_b = cmp_b["exit_reason_mapped_prod"]
    exits_d = cmp_d["exit_reason_mapped_prod"]
    n_b = mb.get("n_trades") or 0
    n_d = md_.get("n_trades") or 0

    gate_a = cmp_a["all_fields_ok"] and entry_delta["A_prod_new_vs_ref"] == 0 \
        and entry_delta["A_prod_missing_vs_ref"] == 0
    gate_b = bool(
        mb.get("final_equity_rub", 0) > ma.get("final_equity_rub", 0)
        and 1.45 <= (mb.get("profit_factor") or 0) <= 1.65
        and dd_b <= 4.0 and abs(dd_b - 2.74) <= 1.0
        and abs(n_b - 3118) <= 0.20 * 3118
        and exits_b.get("initial_stop", 0) >= 0.5 * n_b
    )
    gate_d = bool(
        md_.get("final_equity_rub", 0) > ma.get("final_equity_rub", 0)
        and abs((md_.get("final_equity_rub") or 0) - 110433.68) <= 0.10 * 110433.68
        and abs((md_.get("profit_factor") or 0) - 1.60) <= 0.15
        and abs(n_d - 3162) <= 0.20 * 3162
        and abs(dd_d - 3.06) <= 1.0
        and exits_d.get("initial_stop", 0) > exits_d.get("trailing", 0) > exits_d.get("take", 0)
    )
    gate = {"A_prod_parity": gate_a, "B_prod_gate": gate_b, "B_default_gate": gate_d}
    gate["overall"] = all(gate.values())

    summary = {
        "issue": ISSUE, "generated_at": datetime.now(timezone.utc).isoformat(),
        "extract": {k: v for k, v in extract.items() if k != "books"},
        "entry_stream_delta": entry_delta,
        "comparisons": {"A_prod_vs_ref139_A": cmp_a, "B_prod_vs_ref139_B": cmp_b,
                        "B_default_vs_ultra_late_tight": cmp_d},
        "gate": gate,
        "gate_criteria": "run.md v3: A tight parity; B/D directional-scale (live-loop "
                         "trailing changes the entry stream, overlay tolerances +/-206.35 RUB "
                         "are structurally unreachable; per-trade exit parity evidenced by "
                         "#145 grid_check 0/3305 on both grids)",
    }
    (PKG_DIR / "report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    md = f"# Issue #147 — Production trailing-stop parity report (v4)\n\n"
    md += f"## Gate verdict: {'PASS' if gate['overall'] else 'FAIL'}\n\n"
    md += "| Gate | Criteria | Status |\n|---|---|---|\n"
    md += f"| A_prod = #139 A | tight: equity ±206.35, n/PF/WR/dailyDD tols, exits exact, entries 0/0 | {'PASS' if gate['A_prod_parity'] else 'FAIL'} |\n"
    md += f"| B_prod vs #139 B | directional: equity>A, PF 1.45-1.65, dailyDD<=4.0 and |dd-2.74|<=1.0, n ±20%, initial_stop>=50% | {'PASS' if gate['B_prod_gate'] else 'FAIL'} |\n"
    md += f"| B_default vs #143 ultra_late_tight | directional: equity>A and ±10% of 110434, PF ±0.15 of 1.60, n ±20%, |dd-3.06|<=1.0, split order stop>trailing>take | {'PASS' if gate['B_default_gate'] else 'FAIL'} |\n"
    for title, cmp in [("A_prod vs #139 book A (daily MaxDD)", cmp_a),
                       ("B_prod vs #139 book B (ref139, daily MaxDD)", cmp_b),
                       ("B_default vs #143 ultra_late_tight (daily MaxDD)", cmp_d)]:
        md += f"\n## {title}\n\n| Field | Production | Reference | Delta | Tol | OK |\n|---|---:|---:|---:|---:|:---:|\n"
        for c in cmp.get("field_comparisons", []):
            md += (f"| {c['field']} | {c.get('prod')} | {c.get('ref')} | "
                   f"{c.get('delta', '-')} | {c['tol']} | {'yes' if c.get('ok') else 'NO'} |\n")
        md += (f"\n- daily MaxDD prod {cmp.get('daily_max_drawdown_pct')} vs event {cmp.get('event_max_drawdown_pct')}\n"
               f"- exits prod: {cmp.get('exit_reason_prod')}\n"
               f"- exits mapped: {cmp.get('exit_reason_mapped_prod')}\n"
               f"- exits ref: {cmp.get('exit_reason_ref')} match={cmp.get('exit_reasons_match')}\n")
    md += (f"\n## Entry-stream delta (live-loop vs overlay)\n\n"
           f"- #139 candidates: {entry_delta['ref139_candidates']}\n"
           f"- A_prod new/missing vs ref: {entry_delta['A_prod_new_vs_ref']}/{entry_delta['A_prod_missing_vs_ref']}\n"
           f"- B_prod new/missing vs ref: {entry_delta['B_prod_new_vs_ref']}/{entry_delta['B_prod_missing_vs_ref']}\n"
           f"- book-level (post-replay) A: {entry_delta['book_level']['A_prod_new_vs_ref']}/{entry_delta['book_level']['A_prod_missing_vs_ref']}"
           f" (missing = skipped_entries_no_slot, не расхождение), B: {entry_delta['book_level']['B_prod_new_vs_ref']}/{entry_delta['book_level']['B_prod_missing_vs_ref']}\n"
           f"- Причина дельты B: ранний trailing-выход освобождает тикер внутри цикла движка,\n"
           f"  движок берёт входы, которых оверлей #139/#143 по построению иметь не может\n"
           f"  (single-difference rule держит входы фиксированными). Механика выхода бит-в-бит:\n"
           f"  #145 grid_check 0 расхождений на 3305 сделках на обеих сетках.\n"
           f"- Плотные допуски ±206.35 RUB для B-книг структурно недостижимы на live-контуре;\n"
           f"  критерии пересказаны в run.md v3 ДО прогона, требуется подпись TL/PO.\n")
    md += (f"\n## Configuration\n- Strategy `{extract['strategy_name']}` (id={extract['strategy_id']}), "
           f"SHA-256 `{extract['config_sha256']}`\n- B_default steps source: "
           f"`{extract.get('default_steps_source')}` = {extract.get('default_steps')}\n"
           f"- Period {extract['date_from']} … timestamp < {extract['date_to']}, "
           f"{len(extract['universe'])} tickers\n- Protected untouched: {extract.get('protected_untouched')}\n"
           f"- Исполнение: v4 shard-процессы (bash nohup), без multiprocessing spawn\n"
           f"  (WinError 5 DuplicateHandle на detached-родителе, лог шага 4.5).\n")
    (PKG_DIR / "report.md").write_text(md, encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue #147 parity gate (v4, shard mode)")
    parser.add_argument("--stage", choices=["print-shas", "shard", "assemble", "report"],
                        default="assemble")
    parser.add_argument("--book", choices=list(BOOKS), default=None)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--extract-json", type=Path, default=None)
    args = parser.parse_args()

    if args.stage == "print-shas":
        return stage_print_shas()
    if args.stage == "shard":
        if args.book is None:
            print("ERROR: --shard needs --book", file=sys.stderr)
            return 1
        return stage_shard(args.book, args.shard_index, args.shard_count)
    if args.stage == "report":
        path = args.extract_json or (PKG_DIR / "extract.json")
        if not path.exists():
            print(f"ERROR: {path} not found", file=sys.stderr)
            return 1
        stage_report(json.loads(path.read_text(encoding="utf-8")))
        return 0
    return stage_assemble()


if __name__ == "__main__":
    raise SystemExit(main())
