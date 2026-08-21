"""Reproducible Issue #100 analysis of test_20260820 after the resistance-zone veto.

Run from the repository root:

    python analytics/issue-100-test-20260820-resistance-veto/analysis.py
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ANALYSIS_DIR = Path(__file__).resolve().parent
DEFAULT_INPUTS = ANALYSIS_DIR / "inputs.json"
DEFAULT_RESULTS = ANALYSIS_DIR / "results.json"
PLOTS_DIR = ANALYSIS_DIR / "plots"
ALRS_VETO_TS = pd.Timestamp("2026-08-20 11:50:24")
ALRS_VETO_PRICE = 19.80
EXPRESS_BASELINE_ID = 271


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


def full_sample_frame(results: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for ticker in results.get("lab_universe") or sorted((results.get("by_ticker") or {})):
        item = (results.get("by_ticker") or {}).get(ticker) or {}
        full = item.get("full_sample") or {}
        metrics = full.get("metrics") or {}
        rows.append(
            {
                "ticker": ticker,
                "status": item.get("status") or full.get("status") or "missing",
                "n": metrics.get("n"),
                "pf": finite_pf(metrics),
                "pf_infinite": bool(metrics.get("pf_infinite")),
                "exp_pct": metrics.get("exp_pct"),
                "wr": metrics.get("wr"),
                "maxdd_pct": metrics.get("maxdd_pct"),
                "bars_1min": full.get("bars_1min"),
                "error": item.get("error") or full.get("error"),
            }
        )
    return pd.DataFrame(rows)


def walkforward_frame(results: dict[str, Any]) -> pd.DataFrame:
    period_names = [row["name"] for row in results.get("walkforward_periods") or []]
    rows = []
    for ticker in results.get("lab_universe") or sorted((results.get("by_ticker") or {})):
        wf = ((results.get("by_ticker") or {}).get(ticker) or {}).get("walkforward") or {}
        periods = wf.get("periods") or {}
        row: dict[str, Any] = {
            "ticker": ticker,
            "pf_gt1": wf.get("pf_gt1"),
            "min_pf": wf.get("min_pf"),
            "avg_pf": wf.get("avg_pf"),
        }
        for name in period_names:
            cell = periods.get(name) or {}
            row[name] = math.inf if cell.get("pf_infinite") else cell.get("pf")
            row[f"{name}_n"] = cell.get("n")
        rows.append(row)
    frame = pd.DataFrame(rows)
    frame.attrs["period_names"] = period_names
    return frame


def aggregate_full_sample(frame: pd.DataFrame) -> dict[str, Any]:
    ok = frame[frame["status"] == "success"].copy()
    pf_series = ok["pf"]
    finite = pf_series.replace([math.inf, -math.inf], np.nan).dropna()
    gt1 = 0
    for value, infinite in zip(ok["pf"], ok["pf_infinite"]):
        if infinite or (value is not None and value > 1):
            gt1 += 1
    n_ok = int(len(ok))
    return {
        "tickers_total": int(len(frame)),
        "tickers_success": n_ok,
        "tickers_failed": int((frame["status"] != "success").sum()),
        "trades_total": int(pd.to_numeric(ok["n"], errors="coerce").fillna(0).sum()),
        "mean_pf": None if finite.empty else round(float(finite.mean()), 2),
        "median_pf": None if finite.empty else round(float(finite.median()), 2),
        "pf_gt1_count": gt1,
        "pf_gt1_share": None if n_ok == 0 else round(gt1 / n_ok, 3),
        "mean_exp_pct": None
        if ok["exp_pct"].dropna().empty
        else round(float(ok["exp_pct"].dropna().mean()), 3),
        "median_wr": None if ok["wr"].dropna().empty else round(float(ok["wr"].dropna().median()), 1),
        "median_maxdd_pct": None
        if ok["maxdd_pct"].dropna().empty
        else round(float(ok["maxdd_pct"].dropna().median()), 1),
        "infinite_pf_count": int(ok["pf_infinite"].sum()),
    }


def aggregate_walkforward(frame: pd.DataFrame) -> dict[str, Any]:
    period_names = frame.attrs.get("period_names") or []
    values = []
    gt1 = 0
    counted = 0
    for name in period_names:
        col = pd.to_numeric(frame[name], errors="coerce") if name in frame else pd.Series(dtype=float)
        for value in col:
            if pd.isna(value):
                continue
            counted += 1
            if value == math.inf or value > 1:
                gt1 += 1
            if value != math.inf:
                values.append(float(value))
    return {
        "periods": period_names,
        "pf_observations": counted,
        "pf_gt1_count": gt1,
        "min_pf": None if not values else round(min(values), 2),
        "avg_pf": None if not values else round(sum(values) / len(values), 2),
        "median_avg_pf": None
        if frame["avg_pf"].dropna().empty
        else round(float(frame["avg_pf"].dropna().median()), 2),
    }


def alrs_trades(results: dict[str, Any]) -> list[dict[str, Any]]:
    item = (results.get("by_ticker") or {}).get("ALRS") or {}
    return list((item.get("full_sample") or {}).get("trades") or [])


def alrs_entry_blocked(trades: list[dict[str, Any]], veto_ts: pd.Timestamp | None = None) -> dict[str, Any]:
    target = pd.Timestamp(veto_ts or ALRS_VETO_TS)
    hits = []
    same_day = []
    for trade in trades:
        entry_ts = pd.Timestamp(trade.get("entry_ts"))
        entry_price = trade.get("entry_price")
        record = {
            "entry_ts": str(trade.get("entry_ts")),
            "entry_price": entry_price,
            "exit_reason": trade.get("exit_reason"),
            "net_return_pct": trade.get("net_return_pct"),
        }
        if entry_ts == target:
            hits.append(record)
        if entry_ts.normalize() == target.normalize():
            same_day.append(record)
    return {
        "veto_ts": str(target),
        "blocked": len(hits) == 0,
        "hits": hits,
        "same_day_trades": same_day,
        "n_trades": len(trades),
    }


def express_baseline(inputs: dict[str, Any]) -> dict[str, Any]:
    rows = list(inputs.get("baseline_backtest_results") or [])
    cited = next((row for row in rows if int(row.get("id") or 0) == EXPRESS_BASELINE_ID), None)
    alrs_rows = [
        row
        for row in rows
        if row.get("ticker") == "ALRS" and row.get("test_type") == "full_sample"
    ]
    return {
        "id_271_present": cited is not None,
        "cited_id_271": cited,
        "current_alrs_express": alrs_rows[-1] if alrs_rows else None,
        "express_ticker_count": len(
            {
                row.get("ticker")
                for row in rows
                if row.get("test_type") == "full_sample" and row.get("depth") == "express"
            }
        ),
    }


def compare_alrs_express(
    full_row: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = baseline or {}
    current_row = payload.get("current_alrs_express")
    cited = payload.get("cited_id_271")
    if current_row is None and cited is None:
        return {
            "available": False,
            "id_271_present": False,
            "reason": (
                "Issue #100 cited express Lab id=271 (ALRS n=25, PF=1.05 at 23:15). "
                "That row is no longer in trading.backtest_results (Lab DELETE+re-run). "
                "No replacement ALRS express row was in the snapshot either; numbers are not invented."
            ),
        }
    metrics = (current_row or cited or {}).get("metrics") or {}
    chosen = current_row or cited
    current = {
        "n": None if not full_row else full_row.get("n"),
        "pf": None if not full_row else full_row.get("pf"),
        "exp_pct": None if not full_row else full_row.get("exp_pct"),
        "wr": None if not full_row else full_row.get("wr"),
        "maxdd_pct": None if not full_row else full_row.get("maxdd_pct"),
    }
    caveat = (
        "Issue #100 cited express Lab id=271 (ALRS-only, n=25, PF=1.05). "
        "Lab `_run_job` deletes previous backtest_results for the strategy, so that row is gone. "
        "The snapshot's ALRS express row is still short-window depth=express, not a 2-year full-universe baseline."
    )
    if payload.get("id_271_present"):
        caveat = (
            "Express Lab id=271 is a short ALRS draft, not a 2-year full-universe run. "
            "Do not treat it as the previous full baseline."
        )
    return {
        "available": True,
        "id_271_present": bool(payload.get("id_271_present")),
        "baseline_id": chosen.get("id"),
        "baseline_ticker": chosen.get("ticker"),
        "baseline_depth": chosen.get("depth"),
        "baseline_test_type": chosen.get("test_type"),
        "baseline_created_at": chosen.get("created_at"),
        "express_ticker_count": payload.get("express_ticker_count"),
        "issue_cited_id_271": {"n": 25, "pf": 1.05, "ticker": "ALRS", "present_in_db": bool(payload.get("id_271_present"))},
        "baseline_metrics": {
            "n": metrics.get("n"),
            "pf": metrics.get("pf"),
            "exp_pct": metrics.get("exp_pct"),
            "wr": metrics.get("wr"),
            "maxdd_pct": metrics.get("maxdd_pct"),
        },
        "full_sample": current,
        "caveat": caveat,
    }


def product_verdict(
    aggregates: dict[str, Any],
    wf_agg: dict[str, Any],
    veto: dict[str, Any],
) -> dict[str, Any]:
    median_pf = aggregates.get("median_pf")
    trades_total = int(aggregates.get("trades_total") or 0)
    pf_share = aggregates.get("pf_gt1_share")
    reasons = []
    ready = True
    if not veto.get("blocked"):
        ready = False
        reasons.append("ALRS 2026-08-20 11:50:24 is still in the trade list (blocker)")
    if median_pf is None or median_pf <= 1:
        ready = False
        reasons.append("медианный full-sample PF не больше 1")
    if pf_share is None or pf_share < 0.5:
        ready = False
        reasons.append("меньше половины тикеров имеют PF > 1")
    if trades_total < 30:
        ready = False
        reasons.append("меньше 30 full-sample сделок на Lab-вселенной")
    if (wf_agg.get("avg_pf") or 0) <= 1:
        ready = False
        reasons.append("средний walk-forward PF не больше 1")
    if ready:
        reasons.append("метрики проходят консервативный бар кандидата на paper; этот PR не lock и не paper-flag")
    return {
        "paper_candidate": ready,
        "lock_in_this_pr": False,
        "reasons": reasons,
    }


def _save_figure(figure: plt.Figure, name: str) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(PLOTS_DIR / name, dpi=140, bbox_inches="tight")
    plt.close(figure)


def plot_pf_by_ticker(frame: pd.DataFrame) -> None:
    plot = frame[frame["status"] == "success"].copy()
    plot["pf_plot"] = plot["pf"].replace([math.inf], np.nan)
    plot = plot.sort_values("pf_plot", ascending=True)
    figure, axis = plt.subplots(figsize=(9, max(5.0, 0.32 * max(len(plot), 1))))
    colors = ["#1f7a4c" if (pd.notna(value) and value > 1) else "#b3544a" for value in plot["pf_plot"]]
    axis.barh(plot["ticker"], plot["pf_plot"], color=colors)
    axis.axvline(1.0, color="#333333", linestyle="--", linewidth=1)
    axis.set_xlabel("Profit factor")
    axis.set_title("test_20260820 full-sample PF by ticker")
    _save_figure(figure, "pf_by_ticker.png")


def plot_walkforward(frame: pd.DataFrame) -> None:
    period_names = frame.attrs.get("period_names") or []
    if not period_names or frame.empty:
        figure, axis = plt.subplots(figsize=(8, 3))
        axis.axis("off")
        axis.text(0.5, 0.5, "No walk-forward periods", ha="center")
        _save_figure(figure, "wf_stability.png")
        return
    matrix = frame.set_index("ticker")[period_names].replace([math.inf], np.nan)
    figure_height = max(6.0, 0.34 * max(len(matrix), 1))
    figure, axis = plt.subplots(figsize=(10, figure_height))
    finite = matrix.to_numpy(dtype=float)
    vmax = np.nanpercentile(np.abs(finite[np.isfinite(finite)]), 90) if np.isfinite(finite).any() else 2.0
    vmax = max(float(vmax), 2.0)
    image = axis.imshow(finite, aspect="auto", cmap="RdYlGn", vmin=0.0, vmax=vmax)
    axis.set_title("Walk-forward PF by ticker and half-year")
    axis.set_xticks(range(len(period_names)), period_names)
    axis.set_yticks(range(len(matrix.index)), matrix.index)
    for row in range(len(matrix.index)):
        for column in range(len(period_names)):
            value = matrix.iloc[row, column]
            label = "—" if pd.isna(value) else f"{float(value):.2f}"
            axis.text(column, row, label, ha="center", va="center", fontsize=7)
    colorbar = figure.colorbar(image, ax=axis)
    colorbar.set_label("PF")
    _save_figure(figure, "wf_stability.png")


def plot_equity(results: dict[str, Any]) -> None:
    figure, axis = plt.subplots(figsize=(12, 6))
    all_curves = []
    alrs = None
    for ticker, item in (results.get("by_ticker") or {}).items():
        trades = pd.DataFrame((item.get("full_sample") or {}).get("trades") or [])
        if trades.empty:
            continue
        trades["exit_ts"] = pd.to_datetime(trades["exit_ts"], errors="coerce")
        trades["net_return_pct"] = pd.to_numeric(trades["net_return_pct"], errors="coerce")
        trades = trades.dropna(subset=["exit_ts", "net_return_pct"]).sort_values("exit_ts")
        curve = trades.set_index("exit_ts")["net_return_pct"].cumsum()
        all_curves.append(curve.rename(ticker))
        if ticker == "ALRS":
            alrs = curve
            axis.plot(curve.index, curve.values, color="#1f7a4c", linewidth=2.2, label="ALRS")
        else:
            axis.plot(curve.index, curve.values, color="#8aa0b4", linewidth=0.8, alpha=0.45)
    if all_curves:
        aligned = pd.concat(all_curves, axis=1, sort=True).sort_index().ffill()
        median = aligned.median(axis=1)
        axis.plot(median.index, median.values, color="#1d4f91", linewidth=2, label="Median ticker")
    if alrs is None:
        axis.text(0.5, 0.5, "No ALRS trades", transform=axis.transAxes, ha="center")
    axis.axhline(0.0, color="#333333", linestyle="--", linewidth=1)
    axis.set_title("Cumulative net return % (full-sample, per ticker)")
    axis.set_xlabel("Exit date")
    axis.set_ylabel("Cumulative net return, %")
    axis.grid(alpha=0.25)
    axis.legend()
    _save_figure(figure, "equity_curves.png")


def _pattern_names(inputs: dict[str, Any]) -> str:
    patterns = (inputs.get("strategy") or {}).get("patterns") or {}
    if isinstance(patterns, dict):
        return ", ".join(patterns.keys())
    if isinstance(patterns, list):
        return ", ".join(str(item) for item in patterns)
    return "—"


def render_report(
    inputs: dict[str, Any],
    results: dict[str, Any],
    full: pd.DataFrame,
    wf: pd.DataFrame,
    aggregates: dict[str, Any],
    wf_agg: dict[str, Any],
    veto: dict[str, Any],
    alrs_cmp: dict[str, Any],
    verdict: dict[str, Any],
) -> str:
    strategy = inputs.get("strategy") or {}
    universe = inputs.get("lab_universe") or []
    ignored = inputs.get("run_params_tickers_ignored")
    period_names = wf.attrs.get("period_names") or []
    full_rows = []
    for row in full.itertuples():
        pf = "∞" if row.pf_infinite else _fmt_pf(row.pf)
        full_rows.append(
            f"| {row.ticker} | {row.status} | {_fmt(row.n, 0)} | {pf} | "
            f"{_fmt(row.exp_pct, 3)} | {_fmt(row.wr, 1)} | {_fmt(row.maxdd_pct, 1)} |"
        )
    wf_header = "| Ticker | " + " | ".join(period_names) + " | PF>1 | min PF | avg PF |"
    wf_sep = "|" + "|".join(["---"] * (len(period_names) + 4)) + "|"
    wf_rows = []
    for _, row in wf.iterrows():
        cells = []
        for name in period_names:
            value = row.get(name)
            cells.append(_fmt_pf(None if pd.isna(value) else value, infinite=(value == math.inf)))
        wf_rows.append(
            f"| {row['ticker']} | " + " | ".join(cells) +
            f" | {row.get('pf_gt1') or '—'} | {_fmt_pf(row.get('min_pf'))} | {_fmt_pf(row.get('avg_pf'))} |"
        )
    alrs_full = full[full["ticker"] == "ALRS"]
    alrs_line = "ALRS отсутствует в результатах."
    if not alrs_full.empty:
        row = alrs_full.iloc[0]
        alrs_line = (
            f"полный прогон ALRS: n={_fmt(row['n'], 0)}, PF={_fmt_pf(row['pf'], bool(row['pf_infinite']))}, "
            f"Exp%={_fmt(row['exp_pct'], 3)}, WR={_fmt(row['wr'], 1)}%, MaxDD%={_fmt(row['maxdd_pct'], 1)}"
        )
    if alrs_cmp.get("available"):
        base = alrs_cmp["baseline_metrics"]
        cited = alrs_cmp.get("issue_cited_id_271") or {}
        express_line = (
            f"текущий express ALRS в снимке: id={alrs_cmp['baseline_id']} "
            f"({alrs_cmp.get('baseline_depth')}, {alrs_cmp.get('baseline_ticker')}): "
            f"n={base.get('n')}, PF={base.get('pf')}, Exp%={base.get('exp_pct')}, "
            f"WR={base.get('wr')}, MaxDD%={base.get('maxdd_pct')}. "
            f"id=271 в БД: {'да' if alrs_cmp.get('id_271_present') else 'нет (Lab DELETE+re-run)'}; "
            f"цифра из issue: n={cited.get('n')}, PF={cited.get('pf')}. "
            f"{alrs_cmp.get('caveat')}"
        )
    else:
        express_line = alrs_cmp.get("reason") or "express baseline недоступен; цифры не выдуманы."
    same_day = veto.get("same_day_trades") or []
    same_day_txt = "нет сделок ALRS в этот день" if not same_day else "; ".join(
        f"{item['entry_ts']} @ {item['entry_price']}" for item in same_day
    )
    flags = inputs.get("flags_at_start") or []
    flag_lines = [
        f"- `{row['name']}` (id={row['id']}): in_paper_test={row['in_paper_test']}, locked={row['locked']}"
        for row in flags
    ]
    verdict_txt = (
        "кандидат на paper на полной Lab-вселенной"
        if verdict["paper_candidate"]
        else "пока не кандидат на paper"
    )
    reason_lines = [f"- {item}" for item in verdict["reasons"]]
    return f"""# Issue #100: бэктест `test_20260820` после вето зоны сопротивления

## Резюме

Прогнан **только** Lab-конфиг `{strategy.get('name')}` (id={strategy.get('id')}) на текущем `StrategyEvaluator` после мержа #97.
Вселенная — `get_big_tickers(min_candles=250000)`: **{len(universe)}** тикеров. Черновик Lab `run_params.tickers={ignored}` проигнорирован.
Стратегия **не** lock/paper-flagged этим отчётом; locked `{inputs.get('strategy') and 'test_20260731'}` не перезаписывался.

Вердикт: **{verdict_txt}**.

## Подтверждение конфига

- Снимок: `{inputs.get('extracted_at')}`.
- id={strategy.get('id')}, name=`{strategy.get('name')}`, `in_paper_test={strategy.get('in_paper_test')}`, `locked={strategy.get('locked')}`.
- Паттерны: `{_pattern_names(inputs)}`.
- Уровни: `level_method={strategy.get('level_method')}`, `swing_window={strategy.get('swing_window')}`, `zone_atr_mult={strategy.get('zone_atr_mult')}`, `level_timeframe={strategy.get('level_timeframe')}`.
- Confirm / RR: confirm `{strategy.get('confirm_windows')}`, RR `{strategy.get('risk_reward')}`, commission `{strategy.get('commission_pct')}%`, slippage `{strategy.get('slippage_pct')}`.
- Движок: `{results.get('engine')}`, `n_runs={results.get('n_runs')}` (детерминированный прогон).
- Full-sample окно: `{results.get('date_from')}` → конец доступных 1min (`max_1min_ts={inputs.get('max_1min_ts')}`).
- Фактический список тикеров: `{', '.join(universe)}`.

Флаги стратегий на старте прогона:

{chr(10).join(flag_lines) or '- нет'}

## Full-sample

Агрегаты: median PF `{aggregates.get('median_pf')}`, mean PF `{aggregates.get('mean_pf')}`, доля PF>1 `{aggregates.get('pf_gt1_share')}` ({aggregates.get('pf_gt1_count')}/{aggregates.get('tickers_success')}), сделок `{aggregates.get('trades_total')}`, median WR `{aggregates.get('median_wr')}%`, median MaxDD `{aggregates.get('median_maxdd_pct')}%`.

| Ticker | status | n | PF | Exp % | WR | MaxDD % |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(full_rows)}

![PF by ticker](plots/pf_by_ticker.png)

![Equity curves](plots/equity_curves.png)

## Walk-forward

Периоды: {', '.join(period_names) or 'нет'}. По наблюдениям PF: PF>1 = {wf_agg.get('pf_gt1_count')}/{wf_agg.get('pf_observations')}, min PF `{wf_agg.get('min_pf')}`, avg PF `{wf_agg.get('avg_pf')}`.

{wf_header}
{wf_sep}
{chr(10).join(wf_rows)}

![Walk-forward stability](plots/wf_stability.png)

## ALRS vs express Lab id=271

- {alrs_line}
- {express_line}

Полного 24-месячного baseline по всем тикерам в `trading.backtest_results` до этого прогона не было: более поздний Lab express покрывает те же 28 имён, но это короткое окно, не Very serious.

## Точечная проверка paper #711

Запрещённый бар: `{veto.get('veto_ts')}` @ {ALRS_VETO_PRICE}. В новом trade list: **{'нет (ok)' if veto.get('blocked') else 'ЕСТЬ — блокер'}**.
Сделки ALRS за 2026-08-20: {same_day_txt}.

Это swing-only конфиг. Отсутствие входа не объясняется «особенностью swing-only», если бар всё же попал в список — тогда это блокер гарда #97.

## Вердикт для продукта

{chr(10).join(reason_lines)}

Параметры `test_20260820` и locked `test_20260731` этим issue **не крутились**. Следующий шаг — накопить forward paper на текущем locked конфиге и не подменять его этим прогоном без отдельного решения PO.

## Воспроизводимость

- Входы: `inputs.json` (срез стратегии и baseline без секретов).
- Прогон: `results.json` через `extract_inputs.py` → `run_strategy_backtest` / `run_walkforward`.
- Код: `analysis.py`.
"""


def run_analysis(
    inputs_path: Path | None = None,
    results_path: Path | None = None,
) -> dict[str, Any]:
    inputs = load_json(Path(inputs_path) if inputs_path else DEFAULT_INPUTS)
    results = load_json(Path(results_path) if results_path else DEFAULT_RESULTS)
    if not results.get("by_ticker"):
        raise ValueError("results.json has no by_ticker payload; run extract_inputs.py first")
    full = full_sample_frame(results)
    wf = walkforward_frame(results)
    aggregates = aggregate_full_sample(full)
    wf_agg = aggregate_walkforward(wf)
    veto = alrs_entry_blocked(alrs_trades(results), pd.Timestamp(results.get("alrs_veto_ts") or ALRS_VETO_TS))
    alrs_row = None if full[full["ticker"] == "ALRS"].empty else full[full["ticker"] == "ALRS"].iloc[0].to_dict()
    alrs_cmp = compare_alrs_express(alrs_row, express_baseline(inputs))
    verdict = product_verdict(aggregates, wf_agg, veto)
    plot_pf_by_ticker(full)
    plot_walkforward(wf)
    plot_equity(results)
    report = render_report(inputs, results, full, wf, aggregates, wf_agg, veto, alrs_cmp, verdict)
    (ANALYSIS_DIR / "report.md").write_text(report, encoding="utf-8")
    summary = {
        "extracted_at": inputs.get("extracted_at"),
        "strategy": {
            "id": strategy_id(inputs),
            "name": (inputs.get("strategy") or {}).get("name"),
            "in_paper_test": (inputs.get("strategy") or {}).get("in_paper_test"),
            "locked": (inputs.get("strategy") or {}).get("locked"),
            "level_method": (inputs.get("strategy") or {}).get("level_method"),
            "confirm_windows": (inputs.get("strategy") or {}).get("confirm_windows"),
            "risk_reward": (inputs.get("strategy") or {}).get("risk_reward"),
            "commission_pct": (inputs.get("strategy") or {}).get("commission_pct"),
        },
        "lab_universe": inputs.get("lab_universe"),
        "run_params_tickers_ignored": inputs.get("run_params_tickers_ignored"),
        "date_from": results.get("date_from"),
        "full_sample": full.drop(columns=["error"], errors="ignore").to_dict(orient="records"),
        "full_sample_aggregates": aggregates,
        "walkforward": wf.to_dict(orient="records"),
        "walkforward_aggregates": wf_agg,
        "alrs_veto": veto,
        "alrs_vs_express": alrs_cmp,
        "verdict": verdict,
        "plot_files": sorted(path.name for path in PLOTS_DIR.glob("*.png")),
        "flags_at_start": inputs.get("flags_at_start"),
    }
    (ANALYSIS_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return summary


def strategy_id(inputs: dict[str, Any]) -> int | None:
    value = (inputs.get("strategy") or {}).get("id")
    return None if value is None else int(value)


def _json_default(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return str(value)
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue #100 analysis")
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()
    summary = run_analysis(args.inputs, args.results)
    veto = summary["alrs_veto"]
    print(
        "tickers",
        summary["full_sample_aggregates"]["tickers_success"],
        "median_pf",
        summary["full_sample_aggregates"]["median_pf"],
        "alrs_blocked",
        veto["blocked"],
        "paper_candidate",
        summary["verdict"]["paper_candidate"],
    )
    return 0 if veto["blocked"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
