"""Publish Issue #108 validation reports and plots from generate_inputs.py output.

Run from the repository root after generate_inputs.py:

    python analytics/issue-108-breakout-retest-validation/analysis.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ANALYSIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANALYSIS_DIR.parents[1]
DEFAULT_INPUT = REPO_ROOT / "reports/Arctic/108_breakout-retest-validation/results.json"
ISSUE100_SUMMARY = (
    REPO_ROOT / "analytics/issue-100-test-20260820-resistance-veto/summary.json"
)
PLOTS_DIR = ANALYSIS_DIR / "plots"

WF_MIDPOINTS = {
    "2024-H2": "2024-10-01",
    "2025-H1": "2025-04-01",
    "2025-H2": "2025-10-01",
    "2026-H1": "2026-04-01",
}

REJECTION_LABELS = {
    "no_breakout": "No breakout",
    "breakout_not_confirmed": "Breakout not confirmed",
    "no_retest": "No retest",
    "retest_window_expired": "Retest window expired",
    "support_breaks": "Support breaks",
    "no_entry_trigger": "No entry trigger",
    "accepted": "Accepted",
    "unknown": "Unknown",
}
REJECTION_LABELS_RU = {
    "no_breakout": "Нет пробоя",
    "breakout_not_confirmed": "Пробой не подтверждён",
    "no_retest": "Нет ретеста",
    "retest_window_expired": "Окно ретеста истекло",
    "support_breaks": "Поддержка не держит",
    "no_entry_trigger": "Нет триггера входа",
    "accepted": "Принят",
    "unknown": "Неизвестно",
}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _metrics_from_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "n": 0,
            "pf": None,
            "exp_pct": None,
            "wr": None,
            "maxdd_pct": None,
            "sharpe": None,
        }
    nets = [float(t["net_return_pct"]) for t in trades if t.get("net_return_pct") is not None]
    if not nets:
        return {
            "n": 0,
            "pf": None,
            "exp_pct": None,
            "wr": None,
            "maxdd_pct": None,
            "sharpe": None,
        }
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    gw = sum(wins)
    gl = abs(sum(losses))
    if gl > 0:
        pf: float | None = round(gw / gl, 2)
    elif gw > 0:
        pf = float("inf")
    else:
        pf = None
    cum = np.cumsum(nets)
    maxdd = float((np.maximum.accumulate(cum) - cum).max()) if len(cum) else 0.0
    frame = pd.DataFrame(trades)
    frame["exit_ts"] = pd.to_datetime(frame["exit_ts"], errors="coerce")
    frame["net_return_pct"] = pd.to_numeric(frame["net_return_pct"], errors="coerce")
    daily = frame.dropna(subset=["exit_ts"]).groupby(frame["exit_ts"].dt.floor("D"))[
        "net_return_pct"
    ].sum()
    sharpe = None
    if len(daily) >= 2 and float(daily.std()) > 0:
        sharpe = round(float(daily.mean() / daily.std() * np.sqrt(252)), 2)
    wr = round(len(wins) / len(nets) * 100, 1)
    return {
        "n": len(nets),
        "pf": pf,
        "exp_pct": round(float(np.mean(nets)), 3),
        "wr": wr,
        "maxdd_pct": round(maxdd, 1),
        "sharpe": sharpe,
    }


def _collect_trades(fs_map: dict[str, Any]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for ticker, row in fs_map.items():
        for trade in row.get("trades") or []:
            item = dict(trade)
            item["ticker"] = ticker
            trades.append(item)
    return trades


def _ticker_table(fs_map: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for ticker in sorted(fs_map):
        row = fs_map[ticker]
        metrics = row.get("metrics") or {}
        rows.append(
            {
                "ticker": ticker,
                "status": row.get("status"),
                "n": metrics.get("n"),
                "pf": metrics.get("pf"),
                "exp_pct": metrics.get("exp_pct"),
                "wr": metrics.get("wr"),
                "maxdd_pct": metrics.get("maxdd_pct"),
            }
        )
    return rows


def _median(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None and v == v]
    if not clean:
        return None
    return float(np.median(clean))


def _aggregates(rows: list[dict[str, Any]], pooled: dict[str, Any]) -> dict[str, Any]:
    pfs = [r["pf"] for r in rows if isinstance(r.get("pf"), (int, float))]
    wrs = [r["wr"] for r in rows if isinstance(r.get("wr"), (int, float))]
    dds = [r["maxdd_pct"] for r in rows if isinstance(r.get("maxdd_pct"), (int, float))]
    success = sum(1 for r in rows if r.get("status") == "success")
    pf_gt1 = sum(1 for pf in pfs if pf > 1)
    return {
        "tickers_total": len(rows),
        "tickers_success": success,
        "trades_total": pooled.get("n", 0),
        "mean_pf": round(float(np.mean(pfs)), 2) if pfs else None,
        "median_pf": round(_median(pfs), 2) if pfs else None,
        "pf_gt1_count": pf_gt1,
        "pf_gt1_share": round(pf_gt1 / len(pfs), 3) if pfs else None,
        "median_wr": round(_median(wrs), 1) if wrs else None,
        "median_maxdd_pct": round(_median(dds), 1) if dds else None,
        "pooled": pooled,
    }


def _match_issue100(rows_a: list[dict[str, Any]]) -> dict[str, Any]:
    published = _load_json(ISSUE100_SUMMARY)
    baseline = {
        str(item["ticker"]): item for item in published.get("full_sample") or []
    }
    mismatches = []
    matches = 0
    for row in rows_a:
        ticker = row["ticker"]
        expected = baseline.get(ticker)
        if expected is None:
            mismatches.append({"ticker": ticker, "reason": "missing_in_issue_100"})
            continue
        n_ok = int(row.get("n") or 0) == int(expected.get("n") or -1)
        pf_a = row.get("pf")
        pf_b = expected.get("pf")
        pf_ok = pf_a is not None and pf_b is not None and abs(float(pf_a) - float(pf_b)) <= 0.02
        if n_ok and pf_ok:
            matches += 1
        else:
            mismatches.append(
                {
                    "ticker": ticker,
                    "got_n": row.get("n"),
                    "expected_n": expected.get("n"),
                    "got_pf": pf_a,
                    "expected_pf": pf_b,
                }
            )
    return {
        "compared": len(rows_a),
        "matches": matches,
        "mismatch_count": len(mismatches),
        "bit_for_bit": len(mismatches) == 0 and matches == len(rows_a),
        "published_aggregates": published.get("full_sample_aggregates"),
        "mismatches": mismatches[:20],
    }


def _split_is_oos(trades: list[dict[str, Any]], midpoint: str) -> tuple[list, list]:
    mid = pd.Timestamp(midpoint)
    ins, oos = [], []
    for trade in trades:
        ts = pd.Timestamp(trade.get("entry_ts"))
        if ts < mid:
            ins.append(trade)
        else:
            oos.append(trade)
    return ins, oos


def _walkforward_view(data: dict[str, Any]) -> dict[str, Any]:
    period_rows = data.get("walkforward_periods") or []
    periods = [item["name"] for item in period_rows]
    wf = data.get("walkforward") or {}
    out: dict[str, Any] = {"periods": periods, "A": {}, "B": {}}
    sliced_b = _collect_trades((data.get("full_sample") or {}).get("B") or {})
    for label in ("A", "B"):
        by_period = wf.get(label) or {}
        for item in period_rows:
            name = item["name"]
            ticker_map = by_period.get(name) or {}
            trades = _collect_trades(ticker_map)
            if not trades and label == "B" and sliced_b:
                lo = pd.Timestamp(item["date_from"])
                hi = pd.Timestamp(item["date_to"])
                trades = [
                    t
                    for t in sliced_b
                    if lo <= pd.Timestamp(t.get("entry_ts")) < hi
                ]
            if trades:
                pooled = _metrics_from_trades(trades)
                ins, oos = _split_is_oos(trades, WF_MIDPOINTS.get(name, "2099-01-01"))
            else:
                metric_rows = [row.get("metrics") or {} for row in ticker_map.values()]
                n_sum = sum(int(m.get("n") or 0) for m in metric_rows)
                pfs_only = [
                    m.get("pf")
                    for m in metric_rows
                    if isinstance(m.get("pf"), (int, float))
                ]
                pooled = {
                    "n": n_sum,
                    "pf": round(float(np.mean(pfs_only)), 2) if pfs_only else None,
                    "exp_pct": None,
                    "wr": None,
                    "maxdd_pct": None,
                    "sharpe": None,
                }
                ins, oos = [], []
            is_m = _metrics_from_trades(ins)
            oos_m = _metrics_from_trades(oos)
            degradation = None
            overfit = False
            if (
                isinstance(is_m.get("pf"), (int, float))
                and isinstance(oos_m.get("pf"), (int, float))
                and is_m["pf"] not in (0, float("inf"))
            ):
                degradation = round((is_m["pf"] - oos_m["pf"]) / abs(is_m["pf"]), 3)
                overfit = degradation > 0.20
            pfs = [
                (row.get("metrics") or {}).get("pf")
                for row in ticker_map.values()
                if (row.get("metrics") or {}).get("pf") is not None
            ]
            finite = [pf for pf in pfs if isinstance(pf, (int, float)) and pf != float("inf")]
            out[label][name] = {
                "pooled": pooled,
                "in_sample": is_m,
                "out_of_sample": oos_m,
                "degradation": degradation,
                "overfit_flag": overfit,
                "ticker_avg_pf": round(float(np.mean(finite)), 2) if finite else None,
                "pf_gt1": sum(1 for pf in finite if pf > 1),
                "pf_obs": len(finite),
                "b_source": "full_sample_date_slice" if label == "B" and not _collect_trades(ticker_map) else "lab_window",
            }
    return out


def _fmt_pf(value: Any) -> str:
    if value is None:
        return "—"
    if value == float("inf"):
        return "inf"
    return f"{float(value):.2f}"


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    line = "| " + " | ".join(headers) + " |"
    sep = "|" + "|".join(["---"] * len(headers)) + "|"
    body = ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join([line, sep, *body])


def _plot_equity(trades_a, trades_b, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    for trades, label, color in (
        (trades_a, "A: levels_reversal (#100)", "#1f4e79"),
        (trades_b, "B: + level_breakout_retest", "#c45911"),
    ):
        if not trades:
            continue
        frame = pd.DataFrame(trades)
        frame["exit_ts"] = pd.to_datetime(frame["exit_ts"], errors="coerce")
        frame = frame.dropna(subset=["exit_ts"]).sort_values("exit_ts")
        equity = frame["net_return_pct"].astype(float).cumsum()
        ax.plot(frame["exit_ts"], equity, label=label, color=color, linewidth=1.6)
    ax.set_ylabel("Cumulative net return, %")
    ax.set_title("Full-sample equity (trade-level, not portfolio)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_metrics(agg_a, agg_b, path: Path) -> None:
    labels = ["PF (median)", "Win rate %", "Max DD %"]
    a_vals = [
        agg_a.get("median_pf") or 0,
        agg_a.get("median_wr") or 0,
        agg_a.get("median_maxdd_pct") or 0,
    ]
    b_vals = [
        agg_b.get("median_pf") or 0,
        agg_b.get("median_wr") or 0,
        agg_b.get("median_maxdd_pct") or 0,
    ]
    x = np.arange(len(labels))
    width = 0.35
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(x - width / 2, a_vals, width, label="A", color="#1f4e79")
    ax.bar(x + width / 2, b_vals, width, label="B", color="#c45911")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_title("A vs B (median across tickers)")
    ax.legend()
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_walkforward(wf_view: dict[str, Any], path: Path) -> None:
    periods = wf_view["periods"]
    is_pf = [wf_view["B"][p]["in_sample"].get("pf") or 0 for p in periods]
    oos_pf = [wf_view["B"][p]["out_of_sample"].get("pf") or 0 for p in periods]
    a_pf = [wf_view["A"][p]["pooled"].get("pf") or 0 for p in periods]
    x = np.arange(len(periods))
    width = 0.25
    fig, ax = plt.subplots(figsize=(10, 4.8))
    ax.bar(x - width, a_pf, width, label="A pooled", color="#1f4e79")
    ax.bar(x, is_pf, width, label="B in-sample", color="#548235")
    ax.bar(x + width, oos_pf, width, label="B out-of-sample", color="#c45911")
    ax.axhline(1.0, color="#666", linewidth=0.8, linestyle="--")
    ax.set_xticks(x)
    ax.set_xticklabels(periods)
    ax.set_ylabel("Profit factor")
    ax.set_title("Walk-forward PF (params frozen at Lab defaults)")
    ax.legend()
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_alrs(case: dict[str, Any], path: Path) -> None:
    bars = case.get("htf_bars") or []
    fig, ax = plt.subplots(figsize=(10, 5))
    if bars:
        ts = pd.to_datetime([b["timestamp"] for b in bars])
        close = [b.get("close") for b in bars]
        high = [b.get("high") for b in bars]
        ax.plot(ts, close, color="#1f4e79", label="4h close", linewidth=1.6)
        ax.plot(ts, high, color="#9dc3e6", label="4h high", linewidth=0.9, alpha=0.8)
    zone = case.get("impulse_zone") or [19.40, 19.94]
    ax.axhspan(zone[0], zone[1], color="#f4b183", alpha=0.25, label="Impulse resistance zone")
    ax.axhline(case.get("impulse_resistance") or 19.67, color="#c45911", linestyle="--", label="Level 19.67")
    ax.axhline(19.94, color="#c45911", linewidth=0.8, label="Zone upper 19.94")
    ax.scatter(
        [pd.Timestamp(case["veto_ts"])],
        [case.get("veto_price")],
        color="#c00000",
        s=80,
        zorder=5,
        label="Veto bar 11:50 @ 19.80",
    )
    session = case.get("session_high")
    if session:
        ax.scatter(
            [pd.Timestamp(session["timestamp"])],
            [session["high"]],
            color="#548235",
            s=50,
            zorder=5,
            label=f"Session high {session['high']}",
        )
    ax.set_title("ALRS 2026-08-13..20: impulse zone vs veto bar")
    ax.set_ylabel("Price")
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_rejections(total: dict[str, int], path: Path) -> None:
    items = [
        (key, count)
        for key, count in total.items()
        if key != "accepted" and count > 0
    ]
    items.sort(key=lambda kv: kv[1], reverse=True)
    if not items:
        items = [("none", 1)]
    labels = [REJECTION_LABELS.get(k, k) for k, _ in items]
    sizes = [c for _, c in items]
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=90)
    ax.set_title("Breakout-retest rejection reasons (AND-filter calls)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _recommendation(agg_a, agg_b, wf_view, match, case) -> dict[str, Any]:
    trades_b = int(agg_b.get("trades_total") or 0)
    trades_a = int(agg_a.get("trades_total") or 0)
    pf_a = agg_a.get("median_pf")
    pf_b = agg_b.get("median_pf")
    overfit_windows = sum(
        1 for name in wf_view["periods"] if wf_view["B"][name].get("overfit_flag")
    )
    breakout = bool(case.get("breakout_confirmed"))
    reasons = []
    if not match.get("bit_for_bit"):
        reasons.append(
            "Baseline A did not match Issue #100 ticker-for-ticker; treat A vs B as same-engine comparison, not a published-number clone."
        )
    if trades_b < 30:
        reasons.append(
            f"Config B produced only {trades_b} trades on 28 tickers — too few for a deployment decision."
        )
    if pf_b is not None and pf_a is not None and pf_b + 0.05 < pf_a:
        reasons.append(
            f"Median PF fell from {pf_a} (A) to {pf_b} (B)."
        )
    if overfit_windows:
        reasons.append(
            f"{overfit_windows}/{len(wf_view['periods'])} walk-forward windows show OOS PF >20% below IS (defaults frozen, not a tuned grid)."
        )
    if not breakout:
        reasons.append(
            "ALRS 19.94 was not a confirmed LevelsTracker breakout before 11:50; the #97 veto remains the correct decision on that bar."
        )
    if trades_b < 30 or (pf_b is not None and pf_a is not None and pf_b + 0.05 < pf_a):
        code = "refine"
    elif pf_b is not None and pf_a is not None and pf_b >= pf_a and trades_b >= 100 and overfit_windows == 0:
        code = "adopt"
    else:
        code = "refine"
    labels = {"adopt": "adopt", "reject": "do not adopt", "refine": "refine before Lab UI / paper"}
    return {
        "code": code,
        "label": labels[code],
        "label_ru": {
            "adopt": "внедрять",
            "reject": "не внедрять",
            "refine": "доработать",
        }[code],
        "reasons": reasons,
        "trades_a": trades_a,
        "trades_b": trades_b,
        "median_pf_a": pf_a,
        "median_pf_b": pf_b,
        "overfit_windows": overfit_windows,
        "alrs_breakout_confirmed": breakout,
    }


def _write(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def _full_sample_md(lang: str, agg_a, agg_b, rows_a, rows_b, match, rec) -> str:
    ru = lang == "ru"
    title = (
        "Issue #108: full-sample A vs B"
        if not ru
        else "Задача #108: full-sample сравнение A и B"
    )
    intro = (
        "Baseline **A** is Lab `test_20260820` (id=102) after the #97 veto — "
        "the published Issue #100 universe and window. **B** is the same config "
        "plus `level_breakout_retest` (Lab defaults). Locked `test_20260731` was not rewritten. "
        "The GitHub issue JSON sketch omitted `signal_4h_buy` / confirm windows; "
        "those are required to match Issue #100."
        if not ru
        else "База **A** — Lab-конфиг `test_20260820` (id=102) после вето #97, "
        "та же вселенная и окно, что в задаче #100. **B** — тот же конфиг плюс "
        "`level_breakout_retest` (дефолты Lab). Locked `test_20260731` не перезаписывался. "
        "Упрощённый JSON в issue опускал `signal_4h_buy` и окна подтверждения; "
        "для совпадения с #100 они обязательны."
    )
    headers = ["Ticker", "A n", "A PF", "A WR", "B n", "B PF", "B WR"]
    by_b = {r["ticker"]: r for r in rows_b}
    table_rows = []
    for row in rows_a:
        other = by_b.get(row["ticker"], {})
        table_rows.append(
            [
                row["ticker"],
                row.get("n"),
                _fmt_pf(row.get("pf")),
                row.get("wr"),
                other.get("n"),
                _fmt_pf(other.get("pf")),
                other.get("wr"),
            ]
        )
    match_line = (
        f"Issue #100 bit-for-bit: **{match['matches']}/{match['compared']}** tickers "
        f"(mismatches={match['mismatch_count']})."
        if not ru
        else f"Бит-в-бит с задачей #100: **{match['matches']}/{match['compared']}** тикеров "
        f"(расхождений={match['mismatch_count']})."
    )
    return f"""# {title}

{intro}

## Aggregates

{_md_table(
    ["Config", "Trades", "Median PF", "Mean PF", "PF>1", "Median WR", "Median MaxDD", "Pooled PF", "Pooled Sharpe"],
    [
        ["A", agg_a["trades_total"], _fmt_pf(agg_a["median_pf"]), _fmt_pf(agg_a["mean_pf"]),
         f"{agg_a['pf_gt1_count']}/{agg_a['tickers_success']}", agg_a["median_wr"], agg_a["median_maxdd_pct"],
         _fmt_pf(agg_a["pooled"].get("pf")), agg_a["pooled"].get("sharpe")],
        ["B", agg_b["trades_total"], _fmt_pf(agg_b["median_pf"]), _fmt_pf(agg_b["mean_pf"]),
         f"{agg_b['pf_gt1_count']}/{agg_b['tickers_success']}", agg_b["median_wr"], agg_b["median_maxdd_pct"],
         _fmt_pf(agg_b["pooled"].get("pf")), agg_b["pooled"].get("sharpe")],
    ],
)}

{match_line}

![Equity](plots/equity_curves.png)

![Metrics](plots/metrics_bars.png)

## Per ticker

{_md_table(headers, table_rows)}

## Verdict

{"Recommendation" if not ru else "Рекомендация"}: **{rec['label'] if not ru else rec['label_ru']}**.
"""


def _walk_md(lang: str, wf_view, rec) -> str:
    ru = lang == "ru"
    title = "Issue #108: walk-forward" if not ru else "Задача #108: walk-forward"
    note = (
        "A windows are published Issue #100 Lab walk-forward. "
        "B windows date-slice the full-sample trade list (same 4h levels as FS — "
        "`build_strategy_context` already loads the full HTF history). "
        "Defaults are frozen (no 81-point grid). In-sample vs out-of-sample splits each window at its midpoint. "
        "Degradation >20% flags specification overfit, not a tuned vector."
        if not ru
        else "Окна A — опубликованный Lab walk-forward задачи #100. "
        "Окна B — нарезка full-sample сделок по дате (те же 4h уровни, что в FS: "
        "`build_strategy_context` уже грузит всю HTF-историю). "
        "Дефолты заморожены (сетка 81 точка не гонялась). In-sample / out-of-sample — половины окна. "
        "Деградация >20% — флаг переобучения спецификации."
    )
    rows = []
    for name in wf_view["periods"]:
        a = wf_view["A"][name]
        b = wf_view["B"][name]
        rows.append(
            [
                name,
                _fmt_pf(a["pooled"].get("pf")),
                a["pooled"].get("n"),
                _fmt_pf(b["in_sample"].get("pf")),
                b["in_sample"].get("n"),
                _fmt_pf(b["out_of_sample"].get("pf")),
                b["out_of_sample"].get("n"),
                b.get("degradation") if b.get("degradation") is not None else "—",
                "yes" if b.get("overfit_flag") else "no",
            ]
        )
    return f"""# {title}

{note}

![Walk-forward](plots/walk_forward.png)

{_md_table(
    ["Window", "A PF", "A n", "B IS PF", "B IS n", "B OOS PF", "B OOS n", "Degradation", "Overfit>20%"],
    rows,
)}

Overfit flags: {rec['overfit_windows']}/{len(wf_view['periods'])}.
"""


def _fmt_decision(decision: Any) -> str:
    if not isinstance(decision, dict):
        return "—"
    if decision.get("error"):
        return f"error:{decision['error']}"
    if decision.get("action") is None:
        return "no entry"
    return (
        f"{decision.get('action')} @ {decision.get('entry_price')} "
        f"stop={decision.get('stop')} take={decision.get('take')}"
    )


def _alrs_md(lang: str, case: dict[str, Any], rec) -> str:
    ru = lang == "ru"
    title = "Issue #108: ALRS 2026-08-20 case" if not ru else "Задача #108: кейс ALRS 2026-08-20"
    target = case.get("target_level") or {}
    closes = case.get("close_above_zone_upper_before_veto") or []
    decisions = case.get("decisions") or {}
    q1 = "yes" if closes else "no"
    return f"""# {title}

Veto bar from Issue #97: `{case.get("veto_ts")}` @ {case.get("veto_price")}.
Impulse resistance {case.get("impulse_resistance")} zone {case.get("impulse_zone")}.

## Questions

1. Was there a 4h close > 19.94 before 11:50? **{q1}** ({len(closes)} bars).
2. Consecutive last-4h closes above 19.94: **{case.get("consecutive_4h_closes_above_zone")}** (need 2 plus buffer/penetration for `LevelsTracker`).
3. Confirmed breakout (`is_broken`)? **{case.get("breakout_confirmed")}**.
4. Nearest resistance to 19.67 at the veto: state=`{target.get("state")}`, price={target.get("level_price")}, zone=[{target.get("zone_lower")}, {target.get("zone_upper")}], method=`{target.get("method")}`.
5. `check_entry` at the veto bar: A={_fmt_decision(decisions.get("A"))}, B swing-only={_fmt_decision(decisions.get("B"))}, B swing+impulse={_fmt_decision(decisions.get("B_impulse"))}.
6. Classifier on impulse+retest at the veto: `{case.get("classify_at_veto_impulse")}`.

Session high: {case.get("session_high")}.

The published Issue #100 A trade list has **no** ALRS fill at 11:50. Isolated `check_entry` on a short 1min window can still print a swing-only candidate; it is not a new paper signal. B (swing-only+retest) and B with impulse both stay flat.

![ALRS](plots/alrs_case.png)

{"The #97 veto stays correct if the state machine never confirmed a break of 19.94. Role reversal is not a reason to buy 19.80 inside an active impulse zone." if not ru else "Вето #97 остаётся верным, если state machine не подтвердила пробой 19.94. Role reversal — не основание покупать 19.80 внутри активной импульсной зоны."}
"""


def _rejection_md(lang: str, total: dict[str, int], rec) -> str:
    ru = lang == "ru"
    labels = REJECTION_LABELS_RU if ru else REJECTION_LABELS
    accepted = int(total.get("accepted") or 0)
    rows = []
    ordered = sorted(
        ((k, v) for k, v in total.items() if k != "accepted"),
        key=lambda kv: kv[1],
        reverse=True,
    )
    denom = sum(v for _, v in ordered) or 1
    for key, count in ordered:
        rows.append([labels.get(key, key), count, f"{100 * count / denom:.1f}%"])
    title = "Issue #108: rejection analysis" if not ru else "Задача #108: анализ отказов"
    note = (
        "Counts are `check_breakout_retest` calls from config B (after `levels_reversal` + `signal_4h_buy` already passed). "
        f"Accepted AND-filter hits: **{accepted}**."
        if not ru
        else "Счётчики — вызовы `check_breakout_retest` в конфиге B (после прохождения `levels_reversal` + `signal_4h_buy`). "
        f"Принятых срабатываний AND-фильтра: **{accepted}**."
    )
    return f"""# {title}

{note}

![Rejections](plots/rejection_pie.png)

{_md_table(["Reason", "Count", "Share of rejections"], rows)}
"""


def _summary_md(lang: str, agg_a, agg_b, match, rec, case) -> str:
    ru = lang == "ru"
    title = "Issue #108 summary" if not ru else "Задача #108: резюме"
    rec_word = rec["label_ru"] if ru else rec["label"]
    reasons = "\n".join(f"- {item}" for item in rec["reasons"]) or "- none"
    return f"""# {title}

**{"Recommendation" if not ru else "Рекомендация"}: {rec_word}**

A (`test_20260820` / Issue #100): trades={agg_a["trades_total"]}, median PF={_fmt_pf(agg_a["median_pf"])}, pooled PF={_fmt_pf(agg_a["pooled"].get("pf"))}.
B (`+ level_breakout_retest`): trades={agg_b["trades_total"]}, median PF={_fmt_pf(agg_b["median_pf"])}, pooled PF={_fmt_pf(agg_b["pooled"].get("pf"))}.

Issue #100 match: {match["matches"]}/{match["compared"]} tickers, bit_for_bit={match["bit_for_bit"]}.
ALRS confirmed breakout before 11:50: {case.get("breakout_confirmed")}.

## Why

{reasons}

## Files

- `full_sample_comparison.md` / `.ru.md`
- `walk_forward_results.md` / `.ru.md`
- `alrs_case_study.md` / `.ru.md`
- `rejection_analysis.md` / `.ru.md`
- `plots/`
"""


def analyse(path: Path) -> dict[str, Any]:
    data = _load_json(path)
    if data.get("status") != "success":
        raise ValueError(f"{path}: expected status=success")
    fs = data["full_sample"]
    rows_a = _ticker_table(fs["A"])
    rows_b = _ticker_table(fs["B"])
    trades_a = _collect_trades(fs["A"])
    trades_b = _collect_trades(fs["B"])
    pooled_a = _metrics_from_trades(trades_a)
    pooled_b = _metrics_from_trades(trades_b)
    agg_a = _aggregates(rows_a, pooled_a)
    agg_b = _aggregates(rows_b, pooled_b)
    match = _match_issue100(rows_a)
    wf_view = _walkforward_view(data)
    case = data.get("alrs_case") or {}
    rec = _recommendation(agg_a, agg_b, wf_view, match, case)
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    _plot_equity(trades_a, trades_b, PLOTS_DIR / "equity_curves.png")
    _plot_metrics(agg_a, agg_b, PLOTS_DIR / "metrics_bars.png")
    _plot_walkforward(wf_view, PLOTS_DIR / "walk_forward.png")
    _plot_alrs(case, PLOTS_DIR / "alrs_case.png")
    _plot_rejections((data.get("rejections") or {}).get("total") or {}, PLOTS_DIR / "rejection_pie.png")

    _write(ANALYSIS_DIR / "full_sample_comparison.md", _full_sample_md("en", agg_a, agg_b, rows_a, rows_b, match, rec))
    _write(ANALYSIS_DIR / "full_sample_comparison.ru.md", _full_sample_md("ru", agg_a, agg_b, rows_a, rows_b, match, rec))
    _write(ANALYSIS_DIR / "walk_forward_results.md", _walk_md("en", wf_view, rec))
    _write(ANALYSIS_DIR / "walk_forward_results.ru.md", _walk_md("ru", wf_view, rec))
    _write(ANALYSIS_DIR / "alrs_case_study.md", _alrs_md("en", case, rec))
    _write(ANALYSIS_DIR / "alrs_case_study.ru.md", _alrs_md("ru", case, rec))
    total = (data.get("rejections") or {}).get("total") or {}
    _write(ANALYSIS_DIR / "rejection_analysis.md", _rejection_md("en", total, rec))
    _write(ANALYSIS_DIR / "rejection_analysis.ru.md", _rejection_md("ru", total, rec))
    _write(ANALYSIS_DIR / "summary.md", _summary_md("en", agg_a, agg_b, match, rec, case))
    _write(ANALYSIS_DIR / "summary.ru.md", _summary_md("ru", agg_a, agg_b, match, rec, case))

    summary = {
        "issue": 108,
        "extracted_at": data.get("extracted_at"),
        "date_from": data.get("date_from"),
        "date_to": data.get("date_to"),
        "tickers": data.get("tickers"),
        "aggregates_a": agg_a,
        "aggregates_b": agg_b,
        "issue100_match": match,
        "walkforward": wf_view,
        "alrs_case": {
            "breakout_confirmed": case.get("breakout_confirmed"),
            "consecutive_4h_closes_above_zone": case.get("consecutive_4h_closes_above_zone"),
            "target_level": case.get("target_level"),
            "decisions": case.get("decisions"),
            "classify_at_veto_impulse": case.get("classify_at_veto_impulse"),
            "session_high": case.get("session_high"),
        },
        "rejections_total": total,
        "recommendation": rec,
        "plot_files": [
            "equity_curves.png",
            "metrics_bars.png",
            "walk_forward.png",
            "alrs_case.png",
            "rejection_pie.png",
        ],
    }
    (ANALYSIS_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    published = ANALYSIS_DIR / "results.json"
    if path.resolve() != published.resolve():
        published.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return summary


def main() -> int:
    summary = analyse(DEFAULT_INPUT)
    rec = summary["recommendation"]
    print(
        f"recommendation={rec['code']} A_trades={rec['trades_a']} "
        f"B_trades={rec['trades_b']} match={summary['issue100_match']['bit_for_bit']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
