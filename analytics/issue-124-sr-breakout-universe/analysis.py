"""Reproducible Issue #124 Lab-universe analysis: levels_sr_breakout vs #103/#119.

Run from the repository root after extract_inputs.py:

    python analytics/issue-124-sr-breakout-universe/analysis.py
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
BACKEND_ROOT = REPO_ROOT / "backend"
DEFAULT_INPUTS = ANALYSIS_DIR / "inputs.json"
DEFAULT_RESULTS = ANALYSIS_DIR / "results.json"
PLOTS_DIR = ANALYSIS_DIR / "plots"

SOURCE_SUPPORT = "levels_sr_breakout_support"
SOURCE_RESISTANCE = "levels_sr_breakout_resistance"
ALRS_VETO_TS = pd.Timestamp("2026-08-20 11:50:24")
ALRS_VETO_PRICE = 19.80

ISSUE119_AFKS = {
    "A": {"n": 39, "pf": 1.50},
    "B": {"n": 116, "pf": 1.46},
    "B_support": {"n": 78, "pf": 1.70},
    "B_resistance": {"n": 38, "pf": 1.20},
}
BOOK_103 = {
    "issue": 103,
    "kind": "portfolio_replay",
    "strategy_name": "test_20260821",
    "resistance_veto": True,
    "date_from": "2024-08-01",
    "date_to": "2026-08-21",
    "portfolio_n_trades": 2070,
    "portfolio_pf": 1.34,
    "final_equity_rub": 89055.31,
    "note": (
        "Published #103 is a 28-ticker portfolio replay of levels_reversal "
        "+ signal_4h_buy after veto #97. Isolated A in this package is the "
        "same geometry without slot competition."
    ),
}
BOOK_44 = {
    "issue": 44,
    "kind": "portfolio_replay",
    "strategy_name": "test_20260731",
    "resistance_veto": False,
    "n_trades": 3500,
    "profit_factor": 1.31,
    "note": "Portfolio book before veto #97; not an isolated PF.",
}


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


def source_split(frame: pd.DataFrame) -> dict[str, Any]:
    support = frame[frame["source"] == SOURCE_SUPPORT]
    resistance = frame[frame["source"] == SOURCE_RESISTANCE]
    unlabeled = frame[frame["source"].isna() | (frame["source"] == "")]
    return {
        "support": {
            "source": SOURCE_SUPPORT,
            "metrics": metrics_from_trades(support),
            "n": int(len(support)),
        },
        "resistance": {
            "source": SOURCE_RESISTANCE,
            "metrics": metrics_from_trades(resistance),
            "n": int(len(resistance)),
        },
        "unlabeled_n": int(len(unlabeled)),
    }


def extra_vs_baseline(n_a: int, n_b: int) -> dict[str, int]:
    return {
        "n_a": int(n_a),
        "n_b": int(n_b),
        "added": int(n_b) - int(n_a),
    }


def collect_run_trades(run: dict[str, Any], default_source: str | None = None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for ticker, item in (run.get("by_ticker") or {}).items():
        for trade in item.get("trades") or []:
            row = dict(trade)
            row.setdefault("ticker", ticker)
            rows.append(row)
    return trades_frame(rows, default_source=default_source)


def per_ticker_rows(
    universe: list[str],
    run_a: dict[str, Any],
    run_b: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for ticker in universe:
        item_a = (run_a.get("by_ticker") or {}).get(ticker) or {}
        item_b = (run_b.get("by_ticker") or {}).get(ticker) or {}
        frame_a = trades_frame(item_a.get("trades") or [], default_source="levels_reversal")
        frame_b = trades_frame(item_b.get("trades") or [])
        metrics_a = metrics_from_trades(frame_a)
        metrics_b = metrics_from_trades(frame_b)
        split = source_split(frame_b)
        extra_support = int(split["support"]["n"]) - int(metrics_a["n"])
        rows.append(
            {
                "ticker": ticker,
                "status_a": item_a.get("status") or "missing",
                "status_b": item_b.get("status") or "missing",
                "a": metrics_a,
                "b": metrics_b,
                "source": split,
                "extra_support_from_tracker": extra_support,
                "path_b_n": int(split["resistance"]["n"]),
            }
        )
    return rows


def aggregate_isolated(rows: list[dict[str, Any]], side: str) -> dict[str, Any]:
    ok = [row for row in rows if row[f"status_{side.lower()}"] == "success"]
    pfs = []
    gt1 = 0
    trades = 0
    exps = []
    wrs = []
    dds = []
    for row in ok:
        metrics = row[side]
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


def afks_regression(frame_a: pd.DataFrame, frame_b: pd.DataFrame) -> dict[str, Any]:
    metrics_a = metrics_from_trades(frame_a[frame_a["ticker"] == "AFKS"] if not frame_a.empty else frame_a)
    metrics_b = metrics_from_trades(frame_b[frame_b["ticker"] == "AFKS"] if not frame_b.empty else frame_b)
    split = source_split(frame_b[frame_b["ticker"] == "AFKS"] if not frame_b.empty else frame_b)

    def _match(actual: dict[str, Any], expected: dict[str, float]) -> bool:
        pf = finite_pf(actual)
        return int(actual.get("n") or 0) == int(expected["n"]) and (
            pf is not None and abs(float(pf) - float(expected["pf"])) < 0.015
        )

    return {
        "A": metrics_a,
        "B": metrics_b,
        "source": split,
        "expected": ISSUE119_AFKS,
        "match_a": _match(metrics_a, ISSUE119_AFKS["A"]),
        "match_b": _match(metrics_b, ISSUE119_AFKS["B"]),
        "match": _match(metrics_a, ISSUE119_AFKS["A"]) and _match(metrics_b, ISSUE119_AFKS["B"]),
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


def pick_path_b_examples(frame: pd.DataFrame, limit_tickers: int = 2, per_ticker: int = 1) -> list[dict[str, Any]]:
    rows = frame[frame["source"] == SOURCE_RESISTANCE].sort_values(["ticker", "entry_ts"])
    examples = []
    seen = []
    for row in rows.itertuples():
        if row.ticker not in seen:
            seen.append(row.ticker)
        if seen.index(row.ticker) >= limit_tickers:
            continue
        already = sum(1 for item in examples if item["ticker"] == row.ticker)
        if already >= per_ticker:
            continue
        examples.append(
            {
                "ticker": row.ticker,
                "entry_ts": str(row.entry_ts),
                "exit_ts": str(row.exit_ts),
                "entry_price": float(row.entry_price),
                "exit_price": float(row.exit_price),
                "exit_reason": row.exit_reason,
                "net_return_pct": float(row.net_return_pct),
                "source": row.source,
                "note": (
                    "Path B: confirmed resistance break + retest. "
                    "No native support zone required. Stop/take are ATR×RR, "
                    "not a purchase inside an *active* resistance without a break."
                ),
            }
        )
    return examples


def product_verdict(
    agg_a: dict[str, Any],
    agg_b: dict[str, Any],
    mix_a: dict[str, Any],
    mix_b: dict[str, Any],
    split: dict[str, Any],
    extra: dict[str, int],
    afks: dict[str, Any],
    alrs_a: dict[str, Any],
    alrs_b: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    tune_retest = False
    portfolio = False
    pf_mix_a = finite_pf(mix_a)
    pf_mix_b = finite_pf(mix_b)
    median_b = agg_b.get("median_pf")
    n_res = int(split["resistance"]["n"])
    pf_res = finite_pf(split["resistance"]["metrics"])
    extra_support = int(split["support"]["n"]) - int(extra["n_a"])

    if not afks.get("match"):
        reasons.append(
            f"Регрессия AFKS не совпала с #119 "
            f"(A n={afks['A'].get('n')}/PF {_fmt_pf(finite_pf(afks['A']))}, "
            f"B n={afks['B'].get('n')}/PF {_fmt_pf(finite_pf(afks['B']))}; "
            f"ожидали 39/1.50 и 116/1.46)."
        )
    else:
        reasons.append("AFKS совпал с #119: A 39/1.50, B 116/1.46.")

    if not alrs_a.get("blocked") or not alrs_b.get("blocked"):
        reasons.append("Бар ALRS 2026-08-20 11:50 @ 19.80 найден — блокер, вердикт нет.")
        return {
            "portfolio_replay": False,
            "tune_retest": False,
            "paper": False,
            "label": "нет",
            "reasons": reasons,
        }

    reasons.append("Бар ALRS 2026-08-20 11:50 @ 19.80 отсутствует в A и в B.")

    if mix_b.get("n", 0) == 0:
        reasons.append("Кандидат B дал 0 сделок на вселенной — сначала HTF/трекер, не paper.")
        return {
            "portfolio_replay": False,
            "tune_retest": False,
            "paper": False,
            "label": "нет",
            "reasons": reasons,
        }

    if n_res == 0:
        tune_retest = True
        reasons.append("Путь B (ретест) не дал сделок на 28 тикерах при дефолтах.")
    else:
        reasons.append(
            f"Путь B добавил {n_res} сделок `{SOURCE_RESISTANCE}` "
            f"(PF пути B {_fmt_pf(pf_res, bool(split['resistance']['metrics'].get('pf_infinite')))})."
        )
        if pf_res is not None and pf_res < 1.0:
            tune_retest = True
            reasons.append("PF книги пути B < 1 — путь B разваливает isolated-книгу, крутить ретест.")

    if extra_support:
        reasons.append(
            f"Support-путь B дал {split['support']['n']} сделок против {extra['n_a']} у A "
            f"({extra_support:+d}). Композит передаёт LevelsTracker в вето."
        )
    reasons.append(
        f"Смесь isolated: A n={extra['n_a']} PF {_fmt_pf(pf_mix_a)} → "
        f"B n={extra['n_b']} PF {_fmt_pf(pf_mix_b)} (добавлено {extra['added']})."
    )
    reasons.append(
        f"По тикерам B: median PF {agg_b.get('median_pf')}, mean PF {agg_b.get('mean_pf')}, "
        f"доля PF>1 {agg_b.get('pf_gt1_count')}/{agg_b.get('tickers_success')} "
        f"({_fmt((agg_b.get('pf_gt1_share') or 0) * 100, 1)}%)."
    )

    stable = (
        not tune_retest
        and median_b is not None
        and median_b > 1
        and (pf_mix_b is None or pf_mix_b >= 1)
        and n_res > 0
    )
    if stable:
        portfolio = True
        reasons.append(
            "Isolated B устойчив (median PF>1, путь B не уводит смесь ниже PF 1) — "
            "имеет смысл один портфельный replay 50k/10k, не paper."
        )
    elif not tune_retest:
        reasons.append(
            f"Isolated B не проходит порог устойчивости "
            f"(median PF={agg_b.get('median_pf')}, mix PF={_fmt_pf(pf_mix_b)})."
        )

    reasons.append("Это isolated Lab-вселенная, не вердикт катить в paper.")
    if portfolio:
        label = "портфельный replay"
    elif tune_retest:
        label = "крутить ретест"
    else:
        label = "нет"
    return {
        "portfolio_replay": portfolio,
        "tune_retest": tune_retest,
        "paper": False,
        "label": label,
        "reasons": reasons,
    }


def replay_portfolio_b(
    frame_b: pd.DataFrame,
    volume_order: list[str],
) -> dict[str, Any]:
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    from app.analytics.portfolio_simulator import _portfolio_metrics, _replay_portfolio_trades

    candidates = []
    for row in frame_b.itertuples():
        candidates.append(
            {
                "ticker": row.ticker,
                "entry_ts": str(row.entry_ts),
                "exit_ts": str(row.exit_ts),
                "entry_price": float(row.entry_price),
                "exit_price": float(row.exit_price),
                "exit_reason": row.exit_reason,
                "net_return_pct": float(row.net_return_pct),
                "bars_held": row.bars_held,
                "source": row.source,
            }
        )
    volume_rank = {ticker: rank for rank, ticker in enumerate(volume_order)}
    trades, equity_curve, game_over, game_over_ts, skipped = _replay_portfolio_trades(
        candidates,
        volume_rank,
        initial_capital=50_000.0,
        slot_size=10_000.0,
        max_positions=5,
    )
    metrics = _portfolio_metrics(trades, equity_curve, 50_000.0)
    pf = metrics.get("profit_factor")
    if isinstance(pf, float) and not math.isfinite(pf):
        metrics["profit_factor"] = None
        metrics["pf_infinite"] = True
    portfolio_frame = trades_frame(
        [
            {
                "ticker": trade["ticker"],
                "entry_ts": trade["entry_ts"],
                "exit_ts": trade["exit_ts"],
                "entry_price": trade["entry_price"],
                "exit_price": trade["exit_price"],
                "exit_reason": trade["exit_reason"],
                "net_return_pct": trade["net_return_pct"],
                "bars_held": trade.get("bars_held"),
                "source": next(
                    (
                        cand["source"]
                        for cand in candidates
                        if cand["ticker"] == trade["ticker"]
                        and cand["entry_ts"] == str(trade["entry_ts"])
                    ),
                    None,
                ),
            }
            for trade in trades
        ]
    )
    split = source_split(portfolio_frame)
    return {
        "ran": True,
        "kind": "portfolio_replay",
        "initial_capital_rub": 50000.0,
        "slot_size_rub": 10000.0,
        "max_positions": 5,
        "candidate_n": int(len(candidates)),
        "metrics": metrics,
        "game_over": game_over,
        "game_over_ts": game_over_ts,
        "skipped_entries_no_slot": skipped,
        "source": split,
        "alrs": alrs_entry_blocked(portfolio_frame),
        "equity_curve": equity_curve,
        "note": (
            "Optional #44/#103 slot replay of isolated B candidates. "
            "Do not mix these PF/equity numbers with isolated ticker PF. "
            "Not a paper verdict."
        ),
    }


def plot_totals(mix_a: dict[str, Any], mix_b: dict[str, Any], split: dict[str, Any]) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    labels = ["A", "B mix", "B support", "B resist"]
    ns = [mix_a["n"], mix_b["n"], split["support"]["n"], split["resistance"]["n"]]
    pfs = [
        0.0 if finite_pf(mix_a) is None else float(finite_pf(mix_a)),
        0.0 if finite_pf(mix_b) is None else float(finite_pf(mix_b)),
        0.0 if finite_pf(split["support"]["metrics"]) is None else float(finite_pf(split["support"]["metrics"])),
        0.0
        if finite_pf(split["resistance"]["metrics"]) is None
        else float(finite_pf(split["resistance"]["metrics"])),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].bar(labels, ns, color=["#4c72b0", "#55a868", "#8172b3", "#c44e52"])
    axes[0].set_title("Lab-вселенная: число сделок (isolated)")
    axes[0].set_ylabel("n")
    axes[1].bar(labels, pfs, color=["#4c72b0", "#55a868", "#8172b3", "#c44e52"])
    axes[1].axhline(1.0, color="black", linewidth=0.8, linestyle="--")
    axes[1].set_title("Lab-вселенная: profit factor (isolated)")
    axes[1].set_ylabel("PF")
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "metrics_comparison.png", dpi=120)
    plt.close(fig)


def plot_ticker_pf(rows: list[dict[str, Any]]) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    labels = [row["ticker"] for row in rows]
    pf_a = [0.0 if finite_pf(row["a"]) is None else float(finite_pf(row["a"])) for row in rows]
    pf_b = [0.0 if finite_pf(row["b"]) is None else float(finite_pf(row["b"])) for row in rows]
    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(14, 4.5))
    ax.bar([i - 0.2 for i in x], pf_a, width=0.4, label="A", color="#4c72b0")
    ax.bar([i + 0.2 for i in x], pf_b, width=0.4, label="B", color="#55a868")
    ax.axhline(1.0, color="black", linewidth=0.8, linestyle="--")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=60, ha="right")
    ax.set_ylabel("PF")
    ax.set_title("Isolated PF по тикерам")
    ax.legend()
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "ticker_pf.png", dpi=120)
    plt.close(fig)


def plot_source_split(split: dict[str, Any]) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(
        ["support A-path", "resistance B-path"],
        [split["support"]["n"], split["resistance"]["n"]],
        color=["#4c72b0", "#c44e52"],
    )
    ax.set_title("B: сделки по source (isolated)")
    ax.set_ylabel("n")
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "source_split.png", dpi=120)
    plt.close(fig)


def plot_portfolio_equity(portfolio: dict[str, Any] | None) -> None:
    if not portfolio or not portfolio.get("ran"):
        return
    curve = portfolio.get("equity_curve") or []
    if not curve:
        return
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = [pd.Timestamp(row["ts"]) for row in curve]
    eq = [row["equity_rub"] for row in curve]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(ts, eq, label="B portfolio 50k")
    ax.axhline(50000.0, color="black", linewidth=0.8, linestyle="--")
    ax.set_title("Опциональный портфель B (50k / 10k слоты) — не isolated PF")
    ax.set_ylabel("equity RUB")
    ax.legend()
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "portfolio_equity.png", dpi=120)
    plt.close(fig)


def _ticker_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Тикер | n A | PF A | n B | PF B | B support | B resist | extra support |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['ticker']} | {row['a']['n']} | {_fmt_pf(finite_pf(row['a']), bool(row['a'].get('pf_infinite')))} "
            f"| {row['b']['n']} | {_fmt_pf(finite_pf(row['b']), bool(row['b'].get('pf_infinite')))} "
            f"| {row['source']['support']['n']} | {row['source']['resistance']['n']} "
            f"| {row['extra_support_from_tracker']:+d} |"
        )
    return "\n".join(lines)


def render_report(
    inputs: dict[str, Any],
    mix_a: dict[str, Any],
    mix_b: dict[str, Any],
    split: dict[str, Any],
    extra: dict[str, int],
    agg_a: dict[str, Any],
    agg_b: dict[str, Any],
    rows: list[dict[str, Any]],
    afks: dict[str, Any],
    alrs_a: dict[str, Any],
    alrs_b: dict[str, Any],
    examples: list[dict[str, Any]],
    verdict: dict[str, Any],
    portfolio: dict[str, Any] | None,
) -> str:
    cfg_a = (inputs.get("configs") or {}).get("A") or {}
    cfg_b = (inputs.get("configs") or {}).get("B") or {}
    universe = inputs.get("lab_universe") or []
    flags = inputs.get("flags_at_start") or []
    flag_lines = [
        f"- `{row['name']}` (id={row['id']}): in_paper_test={row['in_paper_test']}, locked={row['locked']}"
        for row in flags
    ]
    example_lines = []
    if examples:
        for item in examples:
            example_lines.append(
                f"- `{item['ticker']}` `{item['entry_ts']}` вход {item['entry_price']}, "
                f"выход {item['exit_price']} ({item['exit_reason']}, {item['net_return_pct']:+.3f}%), "
                f"source=`{item['source']}`. {item['note']}"
            )
    else:
        example_lines.append("- Сделок пути B нет — описывать нечего.")
    portfolio_block = "Опциональный портфельный replay не запускался (isolated B не прошёл порог устойчивости)."
    if portfolio and portfolio.get("ran"):
        metrics = portfolio.get("metrics") or {}
        psplit = portfolio.get("source") or {}
        portfolio_block = (
            f"Отдельный блок, **не** isolated PF. Кандидаты B n={portfolio.get('candidate_n')}, "
            f"портфель n={metrics.get('n_trades')}, PF {_fmt_pf(metrics.get('profit_factor'))}, "
            f"equity {metrics.get('final_equity_rub')} RUB ({_fmt(metrics.get('pnl_pct'), 2)}%), "
            f"пропущено слотов {portfolio.get('skipped_entries_no_slot')}, "
            f"GAME OVER={portfolio.get('game_over')}. "
            f"В портфеле support={psplit.get('support', {}).get('n')} / "
            f"resistance={psplit.get('resistance', {}).get('n')}. "
            f"ALRS 19.80: {'нет' if (portfolio.get('alrs') or {}).get('blocked') else 'ЕСТЬ'}. "
            "Это не вердикт катить в paper."
        )
        if (PLOTS_DIR / "portfolio_equity.png").exists():
            portfolio_block += "\n\n![Портфельная equity B](plots/portfolio_equity.png)"
    reason_lines = [f"- {item}" for item in verdict["reasons"]]
    return f"""# Issue #124: `levels_sr_breakout` на полной Lab-вселенной vs #103/#119

## Резюме

Изолированный бэктест **{len(universe)} тикеров** `get_big_tickers(min_candles=250000)`
за `2024-08-01` … `timestamp < 2026-08-21`. Это **не** портфель 50k и **не** вердикт катить в paper.

| Код | patterns | n | PF | Exp % | WR | MaxDD % |
|---|---|---:|---:|---:|---:|---:|
| A | `levels_reversal` + `signal_4h_buy` | {mix_a.get('n')} | {_fmt_pf(finite_pf(mix_a), bool(mix_a.get('pf_infinite')))} | {_fmt(mix_a.get('exp_pct'), 3)} | {_fmt(mix_a.get('wr'), 1)} | {_fmt(mix_a.get('maxdd_pct'), 1)} |
| B | `levels_sr_breakout` + `signal_4h_buy` | {mix_b.get('n')} | {_fmt_pf(finite_pf(mix_b), bool(mix_b.get('pf_infinite')))} | {_fmt(mix_b.get('exp_pct'), 3)} | {_fmt(mix_b.get('wr'), 1)} | {_fmt(mix_b.get('maxdd_pct'), 1)} |
| B support | `{SOURCE_SUPPORT}` | {split['support']['n']} | {_fmt_pf(finite_pf(split['support']['metrics']), bool(split['support']['metrics'].get('pf_infinite')))} | {_fmt(split['support']['metrics'].get('exp_pct'), 3)} | {_fmt(split['support']['metrics'].get('wr'), 1)} | {_fmt(split['support']['metrics'].get('maxdd_pct'), 1)} |
| B resistance | `{SOURCE_RESISTANCE}` | {split['resistance']['n']} | {_fmt_pf(finite_pf(split['resistance']['metrics']), bool(split['resistance']['metrics'].get('pf_infinite')))} | {_fmt(split['resistance']['metrics'].get('exp_pct'), 3)} | {_fmt(split['resistance']['metrics'].get('wr'), 1)} | {_fmt(split['resistance']['metrics'].get('maxdd_pct'), 1)} |

Агрегаты по тикерам (isolated PF, не слоты):

| Код | median PF | mean PF | PF>1 | сделок |
|---|---:|---:|---:|---:|
| A | {agg_a.get('median_pf')} | {agg_a.get('mean_pf')} | {agg_a.get('pf_gt1_count')}/{agg_a.get('tickers_success')} | {agg_a.get('trades_total')} |
| B | {agg_b.get('median_pf')} | {agg_b.get('mean_pf')} | {agg_b.get('pf_gt1_count')}/{agg_b.get('tickers_success')} | {agg_b.get('trades_total')} |

B добавил **{extra['added']}** сделок относительно A ({extra['n_a']} → {extra['n_b']}).
Неразмеченных сделок B: **{split['unlabeled_n']}** (должно быть 0).

**Вердикт:** {verdict['label']}. Paper: нет.

![Сравнение метрик](plots/metrics_comparison.png)

## Конфиги

Те же SHA, что в #119.

- A SHA-256: `{cfg_a.get('config_sha256')}`.
- B SHA-256: `{cfg_b.get('config_sha256')}`.
- Вселенная: `{len(universe)}` имён, `get_big_tickers`, не `run_params.tickers` и не live top-5.
- Снимок: `{inputs.get('extracted_at')}`. Референс: `{(inputs.get('reference') or {}).get('name')}` id={(inputs.get('reference') or {}).get('id')}.

Флаги стратегий (после прогона те же):

{chr(10).join(flag_lines) or '- нет'}

`test_20260731`, `test_20260820`, `test_20260821` не перезаписывались.

## Регрессия AFKS (#119)

| Код | это | #119 |
|---|---|---|
| A | n={afks['A'].get('n')} PF {_fmt_pf(finite_pf(afks['A']))} | n=39 PF 1.50 |
| B | n={afks['B'].get('n')} PF {_fmt_pf(finite_pf(afks['B']))} | n=116 PF 1.46 |

Совпадение: **{'да' if afks.get('match') else 'нет'}**.

Бар ALRS `2026-08-20 11:50:24` @ 19.80: A **{'нет (ok)' if alrs_a.get('blocked') else 'ЕСТЬ'}**, B **{'нет (ok)' if alrs_b.get('blocked') else 'ЕСТЬ'}**.

## Таблица по тикерам

{_ticker_table(rows)}

![PF по тикерам](plots/ticker_pf.png)

![Разбивка source](plots/source_split.png)

## Выборочные сделки пути B

{chr(10).join(example_lines)}

## Книги #44 / #103 (другая методика)

| Книга | Что это | Цифра |
|---|---|---|
| #44 | Портфель 50k, без вето | n={BOOK_44['n_trades']}, PF {BOOK_44['profit_factor']} |
| #103 | Портфель 50k, после вето, swing+impulse | n={BOOK_103['portfolio_n_trades']}, PF {BOOK_103['portfolio_pf']}, equity {BOOK_103['final_equity_rub']} |

Цифры книг нельзя вычитать из isolated A/B как «дельта PF». Isolated A — честная база после вето без слотов. Isolated B — OR двух путей и вето с `LevelsTracker`.

## Опциональный портфельный replay B

{portfolio_block}

## Вердикт для продукта

{chr(10).join(reason_lines)}

Locked и эталонные стратегии не менять.

## Воспроизводимость

- Конфиги и SHA: `inputs.json`.
- Прогоны: `results.json` (`extract_inputs.py`).
- Код: `analysis.py`.
"""


def run_analysis(
    inputs_path: Path | None = None,
    results_path: Path | None = None,
    skip_portfolio: bool = False,
) -> dict[str, Any]:
    inputs = load_json(Path(inputs_path) if inputs_path else DEFAULT_INPUTS)
    results = load_json(Path(results_path) if results_path else DEFAULT_RESULTS)
    universe = list(inputs.get("lab_universe") or results.get("lab_universe") or [])
    run_a = (results.get("runs") or {}).get("A") or {}
    run_b = (results.get("runs") or {}).get("B") or {}
    missing = [
        f"{code}/{ticker}"
        for code, run in (("A", run_a), ("B", run_b))
        for ticker in universe
        if ((run.get("by_ticker") or {}).get(ticker) or {}).get("status") != "success"
    ]
    if missing:
        raise ValueError(f"incomplete universe runs: {missing[:8]}{'…' if len(missing) > 8 else ''}")

    frame_a = collect_run_trades(run_a, default_source="levels_reversal")
    frame_b = collect_run_trades(run_b)
    mix_a = metrics_from_trades(frame_a)
    mix_b = metrics_from_trades(frame_b)
    split = source_split(frame_b)
    extra = extra_vs_baseline(mix_a["n"], mix_b["n"])
    rows = per_ticker_rows(universe, run_a, run_b)
    agg_a = aggregate_isolated(rows, "a")
    agg_b = aggregate_isolated(rows, "b")
    afks = afks_regression(frame_a, frame_b)
    alrs_a = alrs_entry_blocked(frame_a)
    alrs_b = alrs_entry_blocked(frame_b)
    examples = pick_path_b_examples(frame_b)
    verdict = product_verdict(agg_a, agg_b, mix_a, mix_b, split, extra, afks, alrs_a, alrs_b)
    portfolio = None
    if verdict["portfolio_replay"] and not skip_portfolio:
        portfolio = replay_portfolio_b(frame_b, list(inputs.get("volume_order") or universe))
    plot_totals(mix_a, mix_b, split)
    plot_ticker_pf(rows)
    plot_source_split(split)
    plot_portfolio_equity(portfolio)
    report = render_report(
        inputs,
        mix_a,
        mix_b,
        split,
        extra,
        agg_a,
        agg_b,
        rows,
        afks,
        alrs_a,
        alrs_b,
        examples,
        verdict,
        portfolio,
    )
    (ANALYSIS_DIR / "report.md").write_text(report, encoding="utf-8")
    plot_files = ["metrics_comparison.png", "ticker_pf.png", "source_split.png"]
    if portfolio and (PLOTS_DIR / "portfolio_equity.png").exists():
        plot_files.append("portfolio_equity.png")
    summary = {
        "issue": 124,
        "date_from": results.get("date_from"),
        "date_to": results.get("date_to"),
        "period_last_day": results.get("period_last_day"),
        "extracted_at": inputs.get("extracted_at"),
        "updated_at": results.get("updated_at"),
        "protected_untouched": results.get("protected_untouched"),
        "reference": inputs.get("reference"),
        "lab_universe": universe,
        "config_sha": {
            "A": ((inputs.get("configs") or {}).get("A") or {}).get("config_sha256"),
            "B": ((inputs.get("configs") or {}).get("B") or {}).get("config_sha256"),
        },
        "runs": {
            "A": {
                "engine": "run_strategy_backtest",
                "metrics": mix_a,
                "aggregates": agg_a,
            },
            "B": {
                "engine": "run_strategy_backtest",
                "metrics": mix_b,
                "aggregates": agg_b,
                "source": split,
            },
        },
        "by_ticker": rows,
        "extra_vs_a": extra,
        "extra_support_from_tracker": int(split["support"]["n"]) - int(extra["n_a"]),
        "afks_regression": afks,
        "alrs_veto": {"A": alrs_a, "B": alrs_b},
        "path_b_examples": examples,
        "books": {"issue_44": BOOK_44, "issue_103": BOOK_103, "issue_119_afks": ISSUE119_AFKS},
        "portfolio_b": None
        if not portfolio
        else {key: value for key, value in portfolio.items() if key != "equity_curve"},
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
    parser = argparse.ArgumentParser(description="Issue #124 Lab-universe analysis")
    parser.add_argument("--inputs", default=str(DEFAULT_INPUTS))
    parser.add_argument("--results", default=str(DEFAULT_RESULTS))
    parser.add_argument("--skip-portfolio", action="store_true")
    args = parser.parse_args()
    run_analysis(Path(args.inputs), Path(args.results), skip_portfolio=args.skip_portfolio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
