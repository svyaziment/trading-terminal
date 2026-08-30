"""Reproducible Issue #129 Lab-universe analysis: levels_sr_support vs #124 B-support.

Run from the repository root after extract_inputs.py:

    python analytics/issue-129-sr-support-universe/analysis.py
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


ANALYSIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANALYSIS_DIR.parents[1]
DEFAULT_INPUTS = ANALYSIS_DIR / "inputs.json"
DEFAULT_RESULTS = ANALYSIS_DIR / "results.json"
PLOTS_DIR = ANALYSIS_DIR / "plots"
BOOK_124_DIR = REPO_ROOT / "analytics/issue-124-sr-breakout-universe"
BOOK_124_SUMMARY = BOOK_124_DIR / "summary.json"
BOOK_124_RESULTS = BOOK_124_DIR / "results.json"

SOURCE_C = "levels_sr_support"
SOURCE_RESISTANCE = "levels_sr_breakout_resistance"
ALRS_VETO_TS = pd.Timestamp("2026-08-20 11:50:24")
ALRS_VETO_PRICE = 19.80

BOOK_124_SUPPORT = {"n": 3811, "pf": 1.51, "exp_pct": 0.230, "wr": 30.9}
BOOK_124_A = {"n": 2559, "pf": 1.46}
ISSUE119_AFKS_SUPPORT = {"n": 78, "pf": 1.70}
ISSUE119_AFKS_MIX = {"n": 116, "pf": 1.46}

BOOK_124_SUPPORT_N = {
    "AFKS": 78,
    "ALRS": 131,
    "CBOM": 179,
    "CHMF": 105,
    "FEES": 148,
    "FLOT": 230,
    "GAZP": 99,
    "GMKN": 117,
    "IRAO": 155,
    "LKOH": 114,
    "MGNT": 125,
    "MOEX": 160,
    "MTLR": 95,
    "MTSS": 117,
    "NLMK": 122,
    "NVTK": 139,
    "PHOR": 200,
    "PIKK": 126,
    "PLZL": 150,
    "ROSN": 151,
    "RTKM": 118,
    "RUAL": 136,
    "SBER": 149,
    "SIBN": 130,
    "SNGS": 128,
    "TATN": 149,
    "TRNFP": 138,
    "VTBR": 122,
}

OCCUPANCY_INCLUSIVE = True
NEAR_MISS_SECONDS = 120
PF_TOLERANCE = 0.015


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def finite_pf(metrics: dict[str, Any] | None) -> float | None:
    if not isinstance(metrics, dict):
        return None
    if metrics.get("pf_infinite"):
        return math.inf
    value = metrics.get("pf")
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _fmt_pf(value: float | None, infinite: bool = False) -> str:
    if infinite or value == math.inf:
        return "∞"
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    return f"{float(value):.2f}"


def _fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and not math.isfinite(value):
        return "—"
    try:
        if pd.isna(value):
            return "—"
    except (TypeError, ValueError):
        pass
    return f"{float(value):.{digits}f}{suffix}"


def _match_n_pf(actual: dict[str, Any], expected: dict[str, float], tol: float = PF_TOLERANCE) -> bool:
    pf = finite_pf(actual)
    return int(actual.get("n") or 0) == int(expected["n"]) and (
        pf is not None and abs(float(pf) - float(expected["pf"])) < tol
    )


def trades_frame(trades: list[dict[str, Any]], default_source: str | None = None) -> pd.DataFrame:
    columns = [
        "ticker",
        "entry_ts",
        "exit_ts",
        "entry_price",
        "exit_price",
        "exit_reason",
        "net_return_pct",
        "bars_held",
        "source",
    ]
    frame = pd.DataFrame(trades)
    if frame.empty:
        return pd.DataFrame(columns=columns)
    frame["entry_ts"] = pd.to_datetime(frame["entry_ts"], errors="raise")
    frame["exit_ts"] = pd.to_datetime(frame["exit_ts"], errors="raise")
    for column in ("entry_price", "exit_price", "net_return_pct", "bars_held"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "source" not in frame.columns:
        frame["source"] = default_source
    else:
        frame["source"] = frame["source"].fillna(default_source)
    return frame.reindex(columns=columns)


def metrics_from_trades(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "n": 0,
            "pf": None,
            "pf_infinite": False,
            "exp_pct": None,
            "wr": None,
            "maxdd_pct": None,
        }
    nets = frame["net_return_pct"].astype(float)
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    gw = float(wins.sum()) if not wins.empty else 0.0
    gl = abs(float(losses.sum())) if not losses.empty else 0.0
    if gl > 0:
        pf = gw / gl
        pf_infinite = False
    elif gw > 0:
        pf = None
        pf_infinite = True
    else:
        pf = None
        pf_infinite = False
    cum = nets.cumsum()
    maxdd = float((cum.cummax() - cum).max()) if len(cum) else 0.0
    return {
        "n": int(len(frame)),
        "pf": None if pf is None else round(float(pf), 2),
        "pf_infinite": pf_infinite,
        "exp_pct": round(float(nets.mean()), 3),
        "wr": round(float((nets > 0).mean() * 100.0), 1),
        "maxdd_pct": round(maxdd, 1),
    }


def resistance_split(frame: pd.DataFrame) -> dict[str, Any]:
    resistance = frame[frame["source"] == SOURCE_RESISTANCE]
    unlabeled = frame[frame["source"].isna() | (frame["source"] == "")]
    support = frame[frame["source"] == SOURCE_C]
    other = frame[
        ~frame["source"].isin([SOURCE_C, SOURCE_RESISTANCE])
        & frame["source"].notna()
        & (frame["source"] != "")
    ]
    return {
        "support": {
            "source": SOURCE_C,
            "metrics": metrics_from_trades(support if not support.empty else frame.iloc[0:0]),
            "n": int(len(support)),
        },
        "resistance": {
            "source": SOURCE_RESISTANCE,
            "metrics": metrics_from_trades(resistance),
            "n": int(len(resistance)),
        },
        "other_sources": sorted({str(value) for value in other["source"].tolist()}) if not other.empty else [],
        "other_n": int(len(other)),
        "unlabeled_n": int(len(unlabeled)),
    }


def collect_run_trades(run: dict[str, Any], default_source: str | None = None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for ticker, item in (run.get("by_ticker") or {}).items():
        for trade in item.get("trades") or []:
            row = dict(trade)
            row.setdefault("ticker", ticker)
            rows.append(row)
    return trades_frame(rows, default_source=default_source)


def trade_key(ticker: str, entry_ts: Any, entry_price: Any) -> tuple[str, str, str]:
    ts = pd.Timestamp(entry_ts)
    price = "" if entry_price is None or (isinstance(entry_price, float) and not math.isfinite(entry_price)) else f"{float(entry_price):.6f}"
    return (str(ticker), ts.strftime("%Y-%m-%d %H:%M:%S"), price)


def collect_trade_keys(frame: pd.DataFrame) -> set[tuple[str, str, str]]:
    if frame.empty:
        return set()
    return {
        trade_key(row.ticker, row.entry_ts, row.entry_price)
        for row in frame.itertuples()
    }


def load_book_124_support_n(summary_path: Path | None = None) -> dict[str, int]:
    path = Path(summary_path) if summary_path else BOOK_124_SUMMARY
    if not path.exists():
        return dict(BOOK_124_SUPPORT_N)
    payload = load_json(path)
    out: dict[str, int] = {}
    for row in payload.get("by_ticker") or []:
        ticker = row.get("ticker")
        if not ticker:
            continue
        support = ((row.get("source") or {}).get("support") or {})
        out[str(ticker)] = int(support.get("n") or 0)
    return out or dict(BOOK_124_SUPPORT_N)


def load_book_124_b_trades(results_path: Path | None = None) -> pd.DataFrame:
    path = Path(results_path) if results_path else BOOK_124_RESULTS
    if not path.exists():
        return trades_frame([])
    payload = load_json(path)
    run_b = (payload.get("runs") or {}).get("B") or {}
    return collect_run_trades(run_b)


def load_book_124_support_trades(results_path: Path | None = None) -> pd.DataFrame:
    frame = load_book_124_b_trades(results_path)
    if frame.empty:
        return frame
    return frame[frame["source"] == "levels_sr_breakout_support"].copy()


def load_book_124_resistance_trades(results_path: Path | None = None) -> pd.DataFrame:
    frame = load_book_124_b_trades(results_path)
    if frame.empty:
        return frame
    return frame[frame["source"] == "levels_sr_breakout_resistance"].copy()


def per_ticker_rows(
    universe: list[str],
    run_c: dict[str, Any],
    expected_n: dict[str, int],
) -> list[dict[str, Any]]:
    rows = []
    for ticker in universe:
        item = (run_c.get("by_ticker") or {}).get(ticker) or {}
        frame = trades_frame(item.get("trades") or [], default_source=SOURCE_C)
        metrics = metrics_from_trades(frame)
        expected = int(expected_n.get(ticker, 0))
        split = resistance_split(frame)
        rows.append(
            {
                "ticker": ticker,
                "status_c": item.get("status") or "missing",
                "c": metrics,
                "expected_support_n": expected,
                "n_delta": int(metrics["n"]) - expected,
                "n_match": int(metrics["n"]) == expected,
                "source": split,
            }
        )
    return rows


def aggregate_isolated(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [row for row in rows if row["status_c"] == "success"]
    pfs = []
    gt1 = 0
    trades = 0
    exps = []
    wrs = []
    dds = []
    for row in ok:
        metrics = row["c"]
        trades += int(metrics.get("n") or 0)
        pf = finite_pf(metrics)
        if metrics.get("pf_infinite"):
            gt1 += 1
        elif pf is not None:
            pfs.append(float(pf))
            if pf > 1:
                gt1 += 1
        if metrics.get("exp_pct") is not None:
            exps.append(float(metrics["exp_pct"]))
        if metrics.get("wr") is not None:
            wrs.append(float(metrics["wr"]))
        if metrics.get("maxdd_pct") is not None:
            dds.append(float(metrics["maxdd_pct"]))
    n_ok = len(ok)
    return {
        "tickers_total": int(len(rows)),
        "tickers_success": n_ok,
        "tickers_failed": int(len(rows) - n_ok),
        "trades_total": trades,
        "mean_pf": None if not pfs else round(sum(pfs) / len(pfs), 2),
        "median_pf": None if not pfs else round(float(pd.Series(pfs).median()), 2),
        "pf_gt1_count": gt1,
        "pf_gt1_share": None if n_ok == 0 else round(gt1 / n_ok, 3),
        "mean_exp_pct": None if not exps else round(sum(exps) / len(exps), 3),
        "median_wr": None if not wrs else round(float(pd.Series(wrs).median()), 1),
        "median_maxdd_pct": None if not dds else round(float(pd.Series(dds).median()), 1),
    }


def ticker_n_regression(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mismatches = [
        {
            "ticker": row["ticker"],
            "n_c": int(row["c"]["n"]),
            "n_124_support": int(row["expected_support_n"]),
            "delta": int(row["n_delta"]),
        }
        for row in rows
        if not row["n_match"]
    ]
    return {
        "tickers_compared": int(len(rows)),
        "tickers_match": int(len(rows) - len(mismatches)),
        "match": len(mismatches) == 0 and len(rows) > 0,
        "mismatches": mismatches,
    }


def afks_regression(
    frame_c: pd.DataFrame,
    frame_124_support: pd.DataFrame | None = None,
) -> dict[str, Any]:
    afks = frame_c[frame_c["ticker"] == "AFKS"] if not frame_c.empty else frame_c
    metrics = metrics_from_trades(afks)
    support_subset = True
    missing_support_n = 0
    if frame_124_support is not None and not frame_124_support.empty:
        afks_124 = frame_124_support[frame_124_support["ticker"] == "AFKS"]
        missing_support_n = int(len(collect_trade_keys(afks_124) - collect_trade_keys(afks)))
        support_subset = missing_support_n == 0
    exclusive_n_pf = _match_n_pf(metrics, ISSUE119_AFKS_SUPPORT)
    matched_mix = _match_n_pf(metrics, ISSUE119_AFKS_MIX)
    return {
        "C": metrics,
        "expected_support": ISSUE119_AFKS_SUPPORT,
        "not_mix": ISSUE119_AFKS_MIX,
        "match_support": exclusive_n_pf,
        "matched_mix_by_mistake": matched_mix,
        "support_subset": support_subset,
        "missing_support_n": missing_support_n,
        "match": (not matched_mix) and support_subset and int(metrics.get("n") or 0) >= int(ISSUE119_AFKS_SUPPORT["n"]),
    }


def alrs_entry_blocked(frame: pd.DataFrame) -> dict[str, Any]:
    hits = []
    if frame.empty:
        return {
            "veto_ts": str(ALRS_VETO_TS),
            "price": ALRS_VETO_PRICE,
            "blocked": True,
            "hits": [],
        }
    alrs = frame[frame["ticker"] == "ALRS"]
    minute = ALRS_VETO_TS.floor("min")
    for row in alrs.itertuples():
        ts = pd.Timestamp(row.entry_ts)
        price = float(row.entry_price or 0.0)
        same_bar = ts == ALRS_VETO_TS or ts.floor("min") == minute
        same_price = abs(price - ALRS_VETO_PRICE) <= 0.015
        if same_bar and same_price:
            hits.append(
                {
                    "ticker": "ALRS",
                    "entry_ts": str(row.entry_ts),
                    "entry_price": float(row.entry_price),
                    "source": row.source,
                }
            )
    return {
        "veto_ts": str(ALRS_VETO_TS),
        "price": ALRS_VETO_PRICE,
        "blocked": len(hits) == 0,
        "hits": hits,
    }


def trade_key_regression(
    frame_c: pd.DataFrame,
    frame_124_support: pd.DataFrame,
) -> dict[str, Any]:
    keys_c = collect_trade_keys(frame_c)
    keys_124 = collect_trade_keys(frame_124_support)
    missing = sorted(keys_124 - keys_c)
    extra = sorted(keys_c - keys_124)
    compared = bool(keys_124)
    return {
        "compared": compared,
        "n_c": int(len(keys_c)),
        "n_124_support": int(len(keys_124)),
        "missing_from_c": int(len(missing)),
        "extra_in_c": int(len(extra)),
        "subset": compared and not missing,
        "match": compared and not missing and not extra,
        "missing_examples": [
            {"ticker": item[0], "entry_ts": item[1], "entry_price": item[2]}
            for item in missing[:8]
        ],
        "extra_examples": [
            {"ticker": item[0], "entry_ts": item[1], "entry_price": item[2]}
            for item in extra[:8]
        ],
    }


def extras_frame(frame_c: pd.DataFrame, frame_124_support: pd.DataFrame) -> pd.DataFrame:
    if frame_c.empty:
        return frame_c
    keys_124 = collect_trade_keys(frame_124_support)
    if not keys_124:
        return frame_c.iloc[0:0]
    mask = [
        trade_key(row.ticker, row.entry_ts, row.entry_price) not in keys_124
        for row in frame_c.itertuples()
    ]
    return frame_c.loc[mask].copy()


def _occupies(entry_ts: Any, exit_ts: Any, at: Any, inclusive_exit: bool = True) -> bool:
    start = pd.Timestamp(entry_ts)
    end = pd.Timestamp(exit_ts)
    moment = pd.Timestamp(at)
    if inclusive_exit:
        return start <= moment <= end
    return start <= moment < end


def occupancy_explained(
    extras: pd.DataFrame,
    resistance: pd.DataFrame,
    support: pd.DataFrame | None = None,
) -> dict[str, Any]:
    if extras is None or extras.empty:
        return {
            "extra_n": 0,
            "explained_n": 0,
            "unexplained_n": 0,
            "match": True,
            "explained_examples": [],
            "unexplained": [],
        }
    blockers_frame = resistance
    if support is not None and not support.empty:
        blockers_frame = pd.concat([resistance, support], ignore_index=True) if resistance is not None and not resistance.empty else support
    res_by_ticker: dict[str, list[Any]] = {}
    if blockers_frame is not None and not blockers_frame.empty:
        for row in blockers_frame.itertuples():
            res_by_ticker.setdefault(str(row.ticker), []).append(row)
    explained: list[dict[str, Any]] = []
    unexplained: list[dict[str, Any]] = []
    for row in extras.itertuples():
        et = pd.Timestamp(row.entry_ts)
        blockers = []
        for res in res_by_ticker.get(str(row.ticker), []):
            if _occupies(res.entry_ts, res.exit_ts, et, inclusive_exit=OCCUPANCY_INCLUSIVE):
                blockers.append(
                    {
                        "entry_ts": str(res.entry_ts),
                        "exit_ts": str(res.exit_ts),
                        "entry_price": float(res.entry_price),
                        "source": getattr(res, "source", None),
                    }
                )
        item = {
            "ticker": str(row.ticker),
            "entry_ts": str(row.entry_ts),
            "entry_price": float(row.entry_price),
            "exit_reason": row.exit_reason,
            "net_return_pct": float(row.net_return_pct) if row.net_return_pct is not None else None,
            "blockers_n": int(len(blockers)),
            "blocker": blockers[0] if blockers else None,
        }
        if blockers:
            explained.append(item)
        else:
            unexplained.append(item)
    extra_metrics = metrics_from_trades(extras)
    return {
        "extra_n": int(len(extras)),
        "explained_n": int(len(explained)),
        "unexplained_n": int(len(unexplained)),
        "match": len(unexplained) == 0,
        "metrics": extra_metrics,
        "explained_examples": explained[:8],
        "unexplained": unexplained[:12],
    }


def missing_explained(
    frame_124_support: pd.DataFrame,
    frame_c: pd.DataFrame,
    extra_keys: set[tuple[str, str, str]],
) -> dict[str, Any]:
    if frame_124_support is None or frame_124_support.empty:
        return {
            "missing_n": 0,
            "occupied_n": 0,
            "near_miss_n": 0,
            "unexplained_n": 0,
            "match": True,
            "unexplained": [],
        }
    c_by_ticker: dict[str, list[Any]] = {}
    extras_by_ticker: dict[str, list[Any]] = {}
    if frame_c is not None and not frame_c.empty:
        for row in frame_c.itertuples():
            c_by_ticker.setdefault(str(row.ticker), []).append(row)
            if trade_key(row.ticker, row.entry_ts, row.entry_price) in extra_keys:
                extras_by_ticker.setdefault(str(row.ticker), []).append(row)
    occupied: list[dict[str, Any]] = []
    near: list[dict[str, Any]] = []
    unexplained: list[dict[str, Any]] = []
    keys_c = collect_trade_keys(frame_c)
    for row in frame_124_support.itertuples():
        item_key = trade_key(row.ticker, row.entry_ts, row.entry_price)
        if item_key in keys_c:
            continue
        et = pd.Timestamp(row.entry_ts)
        blockers = []
        for ct in c_by_ticker.get(str(row.ticker), []):
            if _occupies(ct.entry_ts, ct.exit_ts, et, inclusive_exit=OCCUPANCY_INCLUSIVE):
                blockers.append(ct)
        near_hits = []
        for extra in extras_by_ticker.get(str(row.ticker), []):
            delta = abs((pd.Timestamp(extra.entry_ts) - et).total_seconds())
            if delta <= NEAR_MISS_SECONDS:
                near_hits.append(extra)
        payload = {
            "ticker": str(row.ticker),
            "entry_ts": str(row.entry_ts),
            "entry_price": float(row.entry_price),
        }
        if blockers:
            occupied.append(payload)
        elif near_hits:
            near.append(payload)
        else:
            unexplained.append(payload)
    missing_n = int(len(occupied) + len(near) + len(unexplained))
    return {
        "missing_n": missing_n,
        "occupied_n": int(len(occupied)),
        "near_miss_n": int(len(near)),
        "unexplained_n": int(len(unexplained)),
        "match": len(unexplained) == 0,
        "unexplained": unexplained[:12],
    }


def near_miss_explain_extras(
    unexplained_extras: list[dict[str, Any]],
    missing_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    leftover = []
    explained = 0
    for extra in unexplained_extras:
        et = pd.Timestamp(extra["entry_ts"])
        hits = [
            row
            for row in missing_rows
            if row["ticker"] == extra["ticker"]
            and abs((pd.Timestamp(row["entry_ts"]) - et).total_seconds()) <= NEAR_MISS_SECONDS
        ]
        if hits:
            explained += 1
        else:
            leftover.append(extra)
    return {
        "near_miss_n": explained,
        "unexplained_n": int(len(leftover)),
        "unexplained": leftover[:12],
        "match": len(leftover) == 0,
    }


def product_verdict(
    mix_c: dict[str, Any],
    split: dict[str, Any],
    ticker_n: dict[str, Any],
    afks: dict[str, Any],
    alrs: dict[str, Any],
    keys: dict[str, Any],
    occupancy: dict[str, Any],
    missing: dict[str, Any],
    agg: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    match = True
    exclusive_n_pf = _match_n_pf(mix_c, BOOK_124_SUPPORT)

    if mix_c.get("n", 0) == 0:
        reasons.append("Прогон C дал 0 сделок — сначала HTF/трекер, не paper.")
        return {
            "match": False,
            "exclusive_n_pf": False,
            "portfolio_ready": False,
            "paper": False,
            "label": "нет",
            "reasons": reasons,
        }

    if exclusive_n_pf:
        reasons.append(
            f"n/PF вселенной = {BOOK_124_SUPPORT['n']} / {BOOK_124_SUPPORT['pf']:.2f} "
            "(как exclusive-колонка #124 B-support)."
        )
    else:
        reasons.append(
            f"Isolated C n={mix_c.get('n')} PF {_fmt_pf(finite_pf(mix_c))} ≠ exclusive "
            f"#124 B-support {BOOK_124_SUPPORT['n']} / {BOOK_124_SUPPORT['pf']:.2f}. "
            "Колонка B-support — exclusive-подпись композита (путь B занимает слот). "
            "Isolated C — runnable support-only; extra появляются, когда слот свободен."
        )

    if keys.get("compared"):
        reasons.append(
            f"Ключи: C n={keys.get('n_c')}, #124 support n={keys.get('n_124_support')}, "
            f"missing={keys.get('missing_from_c')}, extra={keys.get('extra_in_c')}."
        )

    miss_n = int(missing.get("missing_n") or 0)
    if miss_n:
        if missing.get("match"):
            reasons.append(
                f"Все {miss_n} missing B-support объясняются слотом C "
                f"(occupancy extra {missing.get('occupied_n')}, "
                f"near-miss ≤{NEAR_MISS_SECONDS}s {missing.get('near_miss_n')})."
            )
        else:
            match = False
            reasons.append(
                f"Missing B-support не сводится к слоту C: "
                f"occupied={missing.get('occupied_n')}, near-miss={missing.get('near_miss_n')}, "
                f"unexplained={missing.get('unexplained_n')}."
            )
    elif keys.get("compared") and int(keys.get("missing_from_c") or 0) == 0:
        reasons.append("Книга #124 B-support целиком входит в C (missing=0).")

    extra_n = int(occupancy.get("extra_n") or 0)
    if extra_n:
        leftover = int(occupancy.get("unexplained_after_near_miss", occupancy.get("unexplained_n") or 0))
        explained = extra_n - leftover
        extra_pf = finite_pf(occupancy.get("metrics") or {})
        if leftover <= 1:
            reasons.append(
                f"{explained}/{extra_n} extra-сделок C объясняются occupancy композита "
                f"(путь B / слот) и near-miss"
                + (f"; остаток {leftover} (допуск ≤1)." if leftover else ".")
                + f" Extra PF {_fmt_pf(extra_pf)}."
            )
        else:
            match = False
            reasons.append(
                f"Extra-сделки C: occupancy+near-miss explained={explained}, "
                f"unexplained={leftover}."
            )

    if afks.get("matched_mix_by_mistake"):
        match = False
        reasons.append(
            "AFKS дал смесь 116 / 1.46 — это B mix #119/#124, не support-only."
        )
    elif afks.get("match"):
        reasons.append(
            f"AFKS: книга B-support входит в C (n C={afks['C'].get('n')} PF "
            f"{_fmt_pf(finite_pf(afks['C']))}; exclusive 78 / 1.70, не mix 116 / 1.46)."
        )
    else:
        match = False
        reasons.append(
            f"Регрессия AFKS не прошла: n={afks['C'].get('n')} PF {_fmt_pf(finite_pf(afks['C']))}; "
            f"subset={afks.get('support_subset')} missing={afks.get('missing_support_n')}."
        )

    if not alrs.get("blocked"):
        match = False
        reasons.append("Бар ALRS 2026-08-20 11:50 @ 19.80 найден — блокер, вердикт нет.")
        return {
            "match": False,
            "exclusive_n_pf": exclusive_n_pf,
            "portfolio_ready": False,
            "paper": False,
            "label": "нет",
            "reasons": reasons,
        }
    reasons.append("Бар ALRS 2026-08-20 11:50 @ 19.80 отсутствует.")

    n_res = int(split["resistance"]["n"])
    n_other = int(split.get("other_n") or 0)
    if n_res or n_other:
        match = False
        reasons.append(
            f"Есть сделки не support-пути: resistance={n_res}, other={n_other} "
            f"{split.get('other_sources') or []}."
        )
    else:
        reasons.append("Нет сделок пути resistance / ретеста (`source=levels_sr_support`).")

    unlabeled = int(split.get("unlabeled_n") or 0)
    if unlabeled:
        match = False
        reasons.append(f"Неразмеченных сделок: {unlabeled} (должно быть 0).")

    reasons.append(
        f"По тикерам C: median PF {agg.get('median_pf')}, mean PF {agg.get('mean_pf')}, "
        f"доля PF>1 {agg.get('pf_gt1_count')}/{agg.get('tickers_success')}."
    )
    reasons.append("Это isolated Lab-вселенная, не вердикт катить в paper.")
    if match:
        reasons.append(
            "Движок совпал с путём поддержки #124; isolated-книга C (occupancy extra) "
            "идёт в портфель #130, а не exclusive-колонка 3811 / 1.51."
        )
        label = "совпало"
    else:
        label = "нет"
    return {
        "match": match,
        "exclusive_n_pf": exclusive_n_pf,
        "portfolio_ready": match,
        "paper": False,
        "label": label,
        "reasons": reasons,
    }
    reasons: list[str] = []
    match = True
    exclusive_n_pf = _match_n_pf(mix_c, BOOK_124_SUPPORT)

    if mix_c.get("n", 0) == 0:
        reasons.append("Прогон C дал 0 сделок — сначала HTF/трекер, не paper.")
        return {
            "match": False,
            "exclusive_n_pf": False,
            "portfolio_ready": False,
            "paper": False,
            "label": "нет",
            "reasons": reasons,
        }

    if exclusive_n_pf:
        reasons.append(
            f"n/PF вселенной = {BOOK_124_SUPPORT['n']} / {BOOK_124_SUPPORT['pf']:.2f} "
            "(как exclusive-колонка #124 B-support)."
        )
    else:
        reasons.append(
            f"Isolated C n={mix_c.get('n')} PF {_fmt_pf(finite_pf(mix_c))} ≠ exclusive "
            f"#124 B-support {BOOK_124_SUPPORT['n']} / {BOOK_124_SUPPORT['pf']:.2f}. "
            "Колонка B-support — сделки, которые композит *подписал* support после того, "
            "как путь B забрал dual-бары и занял слот. Isolated C — runnable support-only."
        )

    if keys.get("compared"):
        if keys.get("subset") and int(keys.get("missing_from_c") or 0) == 0:
            reasons.append(
                f"Книга #124 B-support целиком входит в C "
                f"(n_support={keys.get('n_124_support')}, missing=0, extra={keys.get('extra_in_c')})."
            )
        else:
            match = False
            reasons.append(
                f"В C нет сделок exclusive B-support: missing={keys.get('missing_from_c')} "
                f"(это блокер, движок не воспроизвёл путь поддержки)."
            )
    elif ticker_n.get("tickers_compared"):
        reasons.append(
            f"Ключи #124 недоступны; по n тикеров совпало "
            f"{ticker_n.get('tickers_match')}/{ticker_n.get('tickers_compared')}."
        )

    extra_n = int(occupancy.get("extra_n") or 0)
    if extra_n:
        if occupancy.get("match"):
            extra_pf = finite_pf(occupancy.get("metrics") or {})
            reasons.append(
                f"Все {extra_n} extra-сделок C объясняются occupancy пути B в #124 "
                f"(в момент входа C композит уже был в `levels_sr_breakout_resistance`). "
                f"Extra PF {_fmt_pf(extra_pf)}."
            )
        else:
            match = False
            reasons.append(
                f"Extra-сделки C не сводятся к occupancy пути B: "
                f"explained={occupancy.get('explained_n')}, "
                f"unexplained={occupancy.get('unexplained_n')}."
            )

    if afks.get("matched_mix_by_mistake"):
        match = False
        reasons.append(
            "AFKS дал смесь 116 / 1.46 — это B mix #119/#124, не support-only."
        )
    elif afks.get("match"):
        reasons.append(
            f"AFKS: книга B-support входит в C (n C={afks['C'].get('n')} PF "
            f"{_fmt_pf(finite_pf(afks['C']))}; exclusive 78 / 1.70, не mix 116 / 1.46)."
        )
    else:
        match = False
        reasons.append(
            f"Регрессия AFKS не прошла: n={afks['C'].get('n')} PF {_fmt_pf(finite_pf(afks['C']))}; "
            f"subset={afks.get('support_subset')} missing={afks.get('missing_support_n')}."
        )

    if not alrs.get("blocked"):
        match = False
        reasons.append("Бар ALRS 2026-08-20 11:50 @ 19.80 найден — блокер, вердикт нет.")
        return {
            "match": False,
            "exclusive_n_pf": exclusive_n_pf,
            "portfolio_ready": False,
            "paper": False,
            "label": "нет",
            "reasons": reasons,
        }
    reasons.append("Бар ALRS 2026-08-20 11:50 @ 19.80 отсутствует.")

    n_res = int(split["resistance"]["n"])
    n_other = int(split.get("other_n") or 0)
    if n_res or n_other:
        match = False
        reasons.append(
            f"Есть сделки не support-пути: resistance={n_res}, other={n_other} "
            f"{split.get('other_sources') or []}."
        )
    else:
        reasons.append("Нет сделок пути resistance / ретеста (`source=levels_sr_support`).")

    unlabeled = int(split.get("unlabeled_n") or 0)
    if unlabeled:
        match = False
        reasons.append(f"Неразмеченных сделок: {unlabeled} (должно быть 0).")

    reasons.append(
        f"По тикерам C: median PF {agg.get('median_pf')}, mean PF {agg.get('mean_pf')}, "
        f"доля PF>1 {agg.get('pf_gt1_count')}/{agg.get('tickers_success')}."
    )
    reasons.append("Это isolated Lab-вселенная, не вердикт катить в paper.")
    if match:
        reasons.append(
            "Движок совпал с путём поддержки #124; isolated-книга C (с occupancy-extra) "
            "идёт в портфель #130, а не exclusive-колонка 3811 / 1.51."
        )
        label = "совпало"
    else:
        label = "нет"
    return {
        "match": match,
        "exclusive_n_pf": exclusive_n_pf,
        "portfolio_ready": match,
        "paper": False,
        "label": label,
        "reasons": reasons,
    }


def plot_totals(mix_c: dict[str, Any]) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    labels = ["#124 A", "#124 B-support", "C"]
    ns = [BOOK_124_A["n"], BOOK_124_SUPPORT["n"], mix_c["n"]]
    pfs = [
        BOOK_124_A["pf"],
        BOOK_124_SUPPORT["pf"],
        0.0 if finite_pf(mix_c) is None else float(finite_pf(mix_c)),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].bar(labels, ns, color=["#4c72b0", "#8172b3", "#55a868"])
    axes[0].set_title("Lab-вселенная: число сделок (isolated)")
    axes[0].set_ylabel("n")
    axes[1].bar(labels, pfs, color=["#4c72b0", "#8172b3", "#55a868"])
    axes[1].axhline(1.0, color="black", linewidth=0.8, linestyle="--")
    axes[1].set_title("Lab-вселенная: profit factor (isolated)")
    axes[1].set_ylabel("PF")
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "metrics_comparison.png", dpi=120)
    plt.close(fig)


def plot_ticker_n(rows: list[dict[str, Any]]) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    labels = [row["ticker"] for row in rows]
    n_124 = [int(row["expected_support_n"]) for row in rows]
    n_c = [int(row["c"]["n"]) for row in rows]
    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(14, 4.5))
    ax.bar([i - 0.2 for i in x], n_124, width=0.4, label="#124 B-support", color="#8172b3")
    ax.bar([i + 0.2 for i in x], n_c, width=0.4, label="C", color="#55a868")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=60, ha="right")
    ax.set_ylabel("n")
    ax.set_title("Isolated n по тикерам: C vs #124 B-support")
    ax.legend()
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "ticker_n.png", dpi=120)
    plt.close(fig)


def plot_ticker_pf(rows: list[dict[str, Any]]) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    labels = [row["ticker"] for row in rows]
    pf_c = [0.0 if finite_pf(row["c"]) is None else float(finite_pf(row["c"])) for row in rows]
    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(14, 4.5))
    ax.bar(list(x), pf_c, color="#55a868")
    ax.axhline(1.0, color="black", linewidth=0.8, linestyle="--")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=60, ha="right")
    ax.set_ylabel("PF")
    ax.set_title("Isolated PF по тикерам (C = levels_sr_support)")
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "ticker_pf.png", dpi=120)
    plt.close(fig)


def _ticker_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Тикер | n #124 support | n C | Δ | PF C | match |",
        "|---|---:|---:|---:|---:|:---:|",
    ]
    for row in rows:
        mark = "да" if row["n_match"] else "occupancy"
        lines.append(
            f"| {row['ticker']} | {row['expected_support_n']} | {row['c']['n']} | "
            f"{row['n_delta']:+d} | {_fmt_pf(finite_pf(row['c']), bool(row['c'].get('pf_infinite')))} | {mark} |"
        )
    return "\n".join(lines)


def render_report(
    inputs: dict[str, Any],
    mix_c: dict[str, Any],
    split: dict[str, Any],
    agg: dict[str, Any],
    rows: list[dict[str, Any]],
    ticker_n: dict[str, Any],
    afks: dict[str, Any],
    alrs: dict[str, Any],
    keys: dict[str, Any],
    occupancy: dict[str, Any],
    missing_slot: dict[str, Any],
    verdict: dict[str, Any],
) -> str:
    cfg_c = (inputs.get("configs") or {}).get("C") or {}
    universe = inputs.get("lab_universe") or []
    flags = inputs.get("flags_at_start") or []
    flag_lines = [
        f"- `{row['name']}` (id={row['id']}): in_paper_test={row['in_paper_test']}, locked={row['locked']}"
        for row in flags
    ]
    reason_lines = [f"- {item}" for item in verdict["reasons"]]
    keys_line = "Книга #124 `results.json` недоступна — сравнение ключей пропущено."
    if keys.get("compared"):
        keys_line = (
            f"B-support ⊆ C: **{'да' if keys.get('subset') else 'нет'}**. "
            f"C n={keys.get('n_c')}, #124 support n={keys.get('n_124_support')}, "
            f"missing={keys.get('missing_from_c')}, extra={keys.get('extra_in_c')}."
        )
    occ_line = "Occupancy-разбор extra не запускался."
    extra_n = int(occupancy.get("extra_n") or 0)
    leftover = int(occupancy.get("unexplained_after_near_miss", occupancy.get("unexplained_n") or 0))
    if extra_n:
        occ_line = (
            f"Extra {extra_n}: occupancy {occupancy.get('explained_n')}, "
            f"near-miss {occupancy.get('near_miss_n', 0)}, leftover {leftover}, "
            f"extra PF {_fmt_pf(finite_pf(occupancy.get('metrics') or {}))}."
        )
    elif keys.get("compared"):
        occ_line = "Extra нет — isolated C совпал с exclusive-колонкой B-support."
    miss_line = ""
    if missing_slot:
        miss_line = (
            f"Missing {missing_slot.get('missing_n')}: occupancy extra {missing_slot.get('occupied_n')}, "
            f"near-miss {missing_slot.get('near_miss_n')}, unexplained {missing_slot.get('unexplained_n')}."
        )
    return f"""# Issue #129: `levels_sr_support` на полной Lab-вселенной vs #124 B-support

## Резюме

Изолированный бэктест **{len(universe)} тикеров** `get_big_tickers(min_candles=250000)`
за `2024-08-01` … `timestamp < 2026-08-21`. Это **не** портфель 50k и **не** вердикт катить в paper.

Колонка **B-support** в #124 — exclusive-подпись композита (путь B забирает dual-бар
и занимает единственный слот). Isolated C — runnable `levels_sr_support` без ретеста:
книга B-support должна входить в C, extra-сделки объясняются occupancy пути B.

| Код | patterns | n | PF | Exp % | WR | MaxDD % |
|---|---|---:|---:|---:|---:|---:|
| #124 A | `levels_reversal` + `signal_4h_buy` | {BOOK_124_A['n']} | {BOOK_124_A['pf']:.2f} | — | — | — |
| #124 B-support exclusive | `levels_sr_breakout_support` | {BOOK_124_SUPPORT['n']} | {BOOK_124_SUPPORT['pf']:.2f} | {BOOK_124_SUPPORT['exp_pct']:.3f} | {BOOK_124_SUPPORT['wr']:.1f} | — |
| C isolated | `levels_sr_support` + `signal_4h_buy` | {mix_c.get('n')} | {_fmt_pf(finite_pf(mix_c), bool(mix_c.get('pf_infinite')))} | {_fmt(mix_c.get('exp_pct'), 3)} | {_fmt(mix_c.get('wr'), 1)} | {_fmt(mix_c.get('maxdd_pct'), 1)} |

Агрегаты по тикерам (isolated PF, не слоты):

| Код | median PF | mean PF | PF>1 | сделок |
|---|---:|---:|---:|---:|
| C | {agg.get('median_pf')} | {agg.get('mean_pf')} | {agg.get('pf_gt1_count')}/{agg.get('tickers_success')} | {agg.get('trades_total')} |

Сделки resistance/ретеста: **{split['resistance']['n']}** (должно быть 0).
Неразмеченных: **{split['unlabeled_n']}**. Other source: **{split['other_n']}**.
Exclusive n/PF 3811 / 1.51: **{'да' if verdict.get('exclusive_n_pf') else 'нет'}**.

**Вердикт:** {verdict['label']}. Paper: нет. Портфель #130: {'да' if verdict.get('portfolio_ready') else 'нет'}.

![Сравнение метрик](plots/metrics_comparison.png)

## Конфиг C

- C SHA-256: `{cfg_c.get('config_sha256')}`.
- patterns: **только** `levels_sr_support` + `signal_4h_buy`.
- `levels_reversal`, `levels_sr_breakout`, `level_breakout_retest` выключены.
- Вселенная: `{len(universe)}` имён, `get_big_tickers`, не `run_params.tickers` и не live top-5.
- Снимок: `{inputs.get('extracted_at')}`. Референс: `{(inputs.get('reference') or {}).get('name')}` id={(inputs.get('reference') or {}).get('id')}.

Флаги стратегий (после прогона те же):

{chr(10).join(flag_lines) or '- нет'}

`test_20260731`, `test_20260820`, `test_20260821` не перезаписывались.

## Регрессия vs #124 B-support

По равенству n тикеров с exclusive-колонкой: **{'да' if ticker_n.get('match') else 'нет'}**
({ticker_n.get('tickers_match')}/{ticker_n.get('tickers_compared')}). Δ = occupancy extra.

{keys_line}

{occ_line}

{miss_line}

## Регрессия AFKS (#119 / #124 B-support)

| Код | это | exclusive / mix |
|---|---|---|
| C isolated | n={afks['C'].get('n')} PF {_fmt_pf(finite_pf(afks['C']))} | exclusive 78 / 1.70 |
| не mix | subset={afks.get('support_subset')} | mix 116 / 1.46 |

Subset B-support ⊆ C: **{'да' if afks.get('support_subset') else 'нет'}**.
Ошибочно совпало со смесью 116/1.46: **{'да' if afks.get('matched_mix_by_mistake') else 'нет'}**.

Бар ALRS `2026-08-20 11:50:24` @ 19.80: **{'нет (ok)' if alrs.get('blocked') else 'ЕСТЬ'}**.

## Таблица по тикерам

{_ticker_table(rows)}

![n по тикерам](plots/ticker_n.png)

![PF по тикерам](plots/ticker_pf.png)

## Вердикт для продукта

{chr(10).join(reason_lines)}

Locked и эталонные стратегии не менять. Jupyter / портфель 50k — задача #130 по книге C.

## Воспроизводимость

- Конфиг и SHA: `inputs.json`.
- Прогон: `results.json` (`extract_inputs.py`).
- Код: `analysis.py`.
- Целевая книга: `analytics/issue-124-sr-breakout-universe/`.
"""


def run_analysis(
    inputs_path: Path | None = None,
    results_path: Path | None = None,
    book_124_summary: Path | None = None,
    book_124_results: Path | None = None,
) -> dict[str, Any]:
    inputs = load_json(Path(inputs_path) if inputs_path else DEFAULT_INPUTS)
    results = load_json(Path(results_path) if results_path else DEFAULT_RESULTS)
    universe = list(inputs.get("lab_universe") or results.get("lab_universe") or [])
    run_c = (results.get("runs") or {}).get("C") or {}
    incomplete = [
        f"C/{ticker}"
        for ticker in universe
        if ((run_c.get("by_ticker") or {}).get(ticker) or {}).get("status") != "success"
    ]
    if incomplete:
        raise ValueError(f"incomplete universe runs: {incomplete[:8]}{'…' if len(incomplete) > 8 else ''}")

    frame_c = collect_run_trades(run_c, default_source=SOURCE_C)
    mix_c = metrics_from_trades(frame_c)
    split = resistance_split(frame_c)
    expected_n = load_book_124_support_n(book_124_summary)
    rows = per_ticker_rows(universe, run_c, expected_n)
    agg = aggregate_isolated(rows)
    ticker_n = ticker_n_regression(rows)
    frame_124 = load_book_124_support_trades(book_124_results)
    frame_124_res = load_book_124_resistance_trades(book_124_results)
    afks = afks_regression(frame_c, frame_124)
    alrs = alrs_entry_blocked(frame_c)
    keys = trade_key_regression(frame_c, frame_124)
    extras_df = extras_frame(frame_c, frame_124)
    occupancy = occupancy_explained(extras_df, frame_124_res, frame_124)
    extra_keys = collect_trade_keys(extras_df)
    missing_slot = missing_explained(frame_124, frame_c, extra_keys)
    keys_c = collect_trade_keys(frame_c)
    missing_payloads = [
        {
            "ticker": str(row.ticker),
            "entry_ts": str(row.entry_ts),
            "entry_price": float(row.entry_price),
        }
        for row in frame_124.itertuples()
        if trade_key(row.ticker, row.entry_ts, row.entry_price) not in keys_c
    ]
    extra_near = near_miss_explain_extras(occupancy.get("unexplained") or [], missing_payloads)
    occupancy["near_miss_n"] = extra_near["near_miss_n"]
    occupancy["unexplained_after_near_miss"] = extra_near["unexplained_n"]
    occupancy["unexplained"] = extra_near["unexplained"]
    occupancy["match"] = extra_near["unexplained_n"] <= 1
    verdict = product_verdict(
        mix_c, split, ticker_n, afks, alrs, keys, occupancy, missing_slot, agg
    )
    plot_totals(mix_c)
    plot_ticker_n(rows)
    plot_ticker_pf(rows)
    report = render_report(
        inputs,
        mix_c,
        split,
        agg,
        rows,
        ticker_n,
        afks,
        alrs,
        keys,
        occupancy,
        missing_slot,
        verdict,
    )
    (ANALYSIS_DIR / "report.md").write_text(report, encoding="utf-8")
    plot_files = ["metrics_comparison.png", "ticker_n.png", "ticker_pf.png"]
    summary = {
        "issue": 129,
        "date_from": results.get("date_from"),
        "date_to": results.get("date_to"),
        "period_last_day": results.get("period_last_day"),
        "extracted_at": inputs.get("extracted_at"),
        "updated_at": results.get("updated_at"),
        "protected_untouched": results.get("protected_untouched"),
        "reference": inputs.get("reference"),
        "lab_universe": universe,
        "config_sha": {
            "C": ((inputs.get("configs") or {}).get("C") or {}).get("config_sha256"),
        },
        "runs": {
            "C": {
                "engine": "run_strategy_backtest",
                "metrics": mix_c,
                "aggregates": agg,
                "source": split,
            },
        },
        "by_ticker": rows,
        "ticker_n_regression": ticker_n,
        "trade_key_regression": keys,
        "occupancy": occupancy,
        "missing_slot": missing_slot,
        "afks_regression": afks,
        "alrs_veto": alrs,
        "books": {
            "issue_124_support": BOOK_124_SUPPORT,
            "issue_124_a": BOOK_124_A,
            "issue_119_afks_support": ISSUE119_AFKS_SUPPORT,
        },
        "verdict": verdict,
        "plot_files": plot_files,
    }
    (ANALYSIS_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {ANALYSIS_DIR / 'report.md'}", flush=True)
    print(f"wrote {ANALYSIS_DIR / 'summary.json'}", flush=True)
    print(f"verdict={verdict['label']}", flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue #129 Lab-universe analysis")
    parser.add_argument("--inputs", default=str(DEFAULT_INPUTS))
    parser.add_argument("--results", default=str(DEFAULT_RESULTS))
    parser.add_argument("--book-124-summary", default=str(BOOK_124_SUMMARY))
    parser.add_argument("--book-124-results", default=str(BOOK_124_RESULTS))
    args = parser.parse_args()
    run_analysis(
        Path(args.inputs),
        Path(args.results),
        Path(args.book_124_summary),
        Path(args.book_124_results),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
