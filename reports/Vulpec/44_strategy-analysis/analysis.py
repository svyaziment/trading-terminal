"""Reproducible comparison for Issue #44.

Run from the repository root:
    python reports/Vulpec/44_strategy-analysis/analysis.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


STRATEGIES = ("levels_reversal", "atr_reversal")
DISPLAY_NAMES = {
    "levels_reversal": "Levels reversal",
    "atr_reversal": "ATR reversal",
}
ANALYSIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANALYSIS_DIR.parents[2]
DEFAULT_INPUTS = {
    "levels_reversal": REPO_ROOT
    / "reports/Arctic/37_portfolio-backtest/full_run.json",
    "atr_reversal": REPO_ROOT / "reports/Arctic/42_atr-reversal/full_run.json",
}
PLOTS_DIR = ANALYSIS_DIR / "plots"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_result(path: Path, expected_strategy: str) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        result = json.load(stream)

    if result.get("status") != "success":
        raise ValueError(f"{path}: expected status=success, got {result.get('status')!r}")
    if result.get("strategy") != expected_strategy:
        raise ValueError(
            f"{path}: expected strategy={expected_strategy!r}, "
            f"got {result.get('strategy')!r}"
        )
    if float(result.get("initial_capital_rub", 0)) != 50_000.0:
        raise ValueError(f"{path}: Issue #44 requires initial capital of 50,000 RUB")
    if not isinstance(result.get("trades"), list):
        raise ValueError(f"{path}: trades must be a list")
    if not isinstance(result.get("metrics"), dict):
        raise ValueError(f"{path}: metrics must be an object")
    return result


def load_results(
    input_paths: dict[str, Path] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    paths = input_paths or DEFAULT_INPUTS
    results = {
        strategy: _load_result(Path(paths[strategy]), strategy)
        for strategy in STRATEGIES
    }
    baseline = results["levels_reversal"]
    for field in (
        "date_from",
        "date_to",
        "initial_capital_rub",
        "slot_size_rub",
        "max_positions",
        "tickers",
    ):
        if results["atr_reversal"].get(field) != baseline.get(field):
            raise ValueError(f"Input mismatch for fair comparison: {field}")
    hashes = {strategy: _sha256(Path(paths[strategy])) for strategy in STRATEGIES}
    return results, hashes


def trades_frame(result: dict[str, Any], strategy: str) -> pd.DataFrame:
    columns = [
        "strategy",
        "ticker",
        "entry_ts",
        "exit_ts",
        "entry_price",
        "exit_price",
        "exit_reason",
        "allocated_rub",
        "net_return_pct",
        "pnl_rub",
        "bars_held",
    ]
    frame = pd.DataFrame(result["trades"])
    if frame.empty:
        return pd.DataFrame(columns=columns)
    frame["strategy"] = strategy
    frame["entry_ts"] = pd.to_datetime(frame["entry_ts"], errors="raise")
    frame["exit_ts"] = pd.to_datetime(frame["exit_ts"], errors="raise")
    for column in (
        "entry_price",
        "exit_price",
        "allocated_rub",
        "net_return_pct",
        "pnl_rub",
        "bars_held",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.reindex(columns=columns)


def metrics_frame(results: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for strategy in STRATEGIES:
        result = results[strategy]
        metrics = result["metrics"]
        rows.append(
            {
                "strategy": strategy,
                "final_equity_rub": float(metrics["final_equity_rub"]),
                "pnl_rub": float(metrics["pnl_rub"]),
                "pnl_pct": float(metrics["pnl_pct"]),
                "n_trades": int(metrics["n_trades"]),
                "win_rate_pct": metrics["win_rate"],
                "profit_factor": metrics["profit_factor"],
                "max_drawdown_pct": float(metrics["max_drawdown_pct"]),
                "game_over": bool(result.get("game_over")),
                "skipped_entries": int(result.get("skipped_entries_no_slot", 0)),
            }
        )
    return pd.DataFrame(rows).set_index("strategy")


def daily_equity(
    result: dict[str, Any], trades: pd.DataFrame
) -> pd.Series:
    initial = float(result["initial_capital_rub"])
    start = pd.Timestamp(result["date_from"]).normalize()
    end = pd.Timestamp(result["date_to"]).normalize() - pd.Timedelta(days=1)
    if result.get("game_over_ts"):
        end = min(end, pd.Timestamp(result["game_over_ts"]).normalize())
    dates = pd.date_range(start, end, freq="D")
    daily_pnl = (
        trades.assign(day=trades["exit_ts"].dt.normalize())
        .groupby("day")["pnl_rub"]
        .sum()
        .reindex(dates, fill_value=0.0)
    )
    equity = initial + daily_pnl.cumsum()
    equity.name = "equity_rub"
    return equity


def ticker_summary(all_trades: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        all_trades.groupby(["ticker", "strategy"], observed=True)
        .agg(
            n_trades=("pnl_rub", "size"),
            pnl_rub=("pnl_rub", "sum"),
            win_rate_pct=("pnl_rub", lambda values: (values > 0).mean() * 100),
        )
        .reset_index()
    )
    tickers = sorted(all_trades["ticker"].dropna().unique())
    full_index = pd.MultiIndex.from_product(
        [tickers, STRATEGIES], names=["ticker", "strategy"]
    )
    return (
        grouped.set_index(["ticker", "strategy"])
        .reindex(full_index, fill_value=0.0)
        .reset_index()
    )


def monthly_summary(all_trades: pd.DataFrame) -> pd.DataFrame:
    if all_trades.empty:
        return pd.DataFrame(columns=["month", "strategy", "n_trades", "pnl_rub"])
    frame = all_trades.assign(month=all_trades["exit_ts"].dt.to_period("M").astype(str))
    return (
        frame.groupby(["month", "strategy"], observed=True)
        .agg(n_trades=("pnl_rub", "size"), pnl_rub=("pnl_rub", "sum"))
        .reset_index()
        .sort_values(["month", "strategy"])
    )


def _save_figure(figure: plt.Figure, filename: str) -> None:
    figure.tight_layout()
    figure.savefig(PLOTS_DIR / filename, dpi=160, bbox_inches="tight")
    plt.close(figure)


def plot_equity(equity: dict[str, pd.Series]) -> None:
    figure, axis = plt.subplots(figsize=(12, 6))
    for strategy in STRATEGIES:
        series = equity[strategy]
        axis.plot(series.index, series.values, label=DISPLAY_NAMES[strategy], linewidth=2)
    axis.axhline(50_000, color="gray", linestyle="--", linewidth=1, label="Start: 50,000 RUB")
    axis.set_title("Daily realized equity by strategy")
    axis.set_xlabel("Date")
    axis.set_ylabel("Equity (RUB)")
    axis.grid(alpha=0.25)
    axis.legend()
    _save_figure(figure, "equity_curves.png")


def plot_trade_distribution(all_trades: pd.DataFrame) -> None:
    figure, axis = plt.subplots(figsize=(10, 6))
    for strategy in STRATEGIES:
        values = all_trades.loc[all_trades["strategy"] == strategy, "pnl_rub"]
        if not values.empty:
            axis.hist(values, bins=30, alpha=0.55, label=DISPLAY_NAMES[strategy])
    axis.axvline(0, color="black", linewidth=1)
    axis.set_title("Distribution of realized PnL per trade")
    axis.set_xlabel("Trade PnL (RUB)")
    axis.set_ylabel("Number of trades")
    axis.legend()
    _save_figure(figure, "trade_pnl_distribution.png")


def plot_ticker_heatmap(summary: pd.DataFrame) -> None:
    pivot = summary.pivot(index="ticker", columns="strategy", values="pnl_rub")
    pivot = pivot.reindex(columns=STRATEGIES).sort_index()
    limit = max(float(np.abs(pivot.to_numpy()).max()), 1.0)
    figure_height = max(7.0, len(pivot) * 0.34)
    figure, axis = plt.subplots(figsize=(9, figure_height))
    image = axis.imshow(
        pivot.to_numpy(),
        aspect="auto",
        cmap="RdYlGn",
        vmin=-limit,
        vmax=limit,
    )
    axis.set_title("Realized PnL by ticker and strategy")
    axis.set_xlabel("Strategy")
    axis.set_ylabel("Ticker")
    axis.set_xticks(range(len(STRATEGIES)), [DISPLAY_NAMES[s] for s in STRATEGIES])
    axis.set_yticks(range(len(pivot.index)), pivot.index)
    for row in range(len(pivot.index)):
        for column in range(len(STRATEGIES)):
            axis.text(
                column,
                row,
                f"{pivot.iloc[row, column]:,.0f}",
                ha="center",
                va="center",
                fontsize=7,
            )
    colorbar = figure.colorbar(image, ax=axis)
    colorbar.set_label("PnL (RUB)")
    _save_figure(figure, "ticker_pnl_heatmap.png")


def plot_metrics_table(metrics: pd.DataFrame) -> None:
    table = metrics[
        [
            "final_equity_rub",
            "pnl_rub",
            "pnl_pct",
            "n_trades",
            "win_rate_pct",
            "profit_factor",
            "max_drawdown_pct",
            "game_over",
        ]
    ].copy()
    table.index = [DISPLAY_NAMES[index] for index in table.index]
    table.columns = [
        "Final equity, RUB",
        "PnL, RUB",
        "PnL, %",
        "Trades",
        "Win rate, %",
        "Profit factor",
        "Max DD, %",
        "GAME OVER",
    ]
    formatted = table.copy().astype(object)
    for column in ("Final equity, RUB", "PnL, RUB"):
        formatted[column] = table[column].map(lambda value: f"{value:,.2f}")
    for column in ("PnL, %", "Win rate, %", "Profit factor", "Max DD, %"):
        formatted[column] = table[column].map(
            lambda value: "—" if pd.isna(value) else f"{float(value):.2f}"
        )
    formatted["Trades"] = table["Trades"].map(lambda value: f"{int(value)}")
    formatted["GAME OVER"] = table["GAME OVER"].map(lambda value: "yes" if value else "no")

    figure, axis = plt.subplots(figsize=(14, 2.3))
    axis.axis("off")
    rendered = axis.table(
        cellText=formatted.values,
        rowLabels=formatted.index,
        colLabels=formatted.columns,
        cellLoc="center",
        loc="center",
    )
    rendered.auto_set_font_size(False)
    rendered.set_fontsize(9)
    rendered.scale(1, 1.5)
    axis.set_title("Portfolio backtest metrics (50,000 RUB start)", pad=18)
    _save_figure(figure, "metrics_comparison.png")


def _format_tickers(
    summary: pd.DataFrame, strategy: str, positive: bool, limit: int = 5
) -> str:
    selected = summary[
        (summary["strategy"] == strategy)
        & (summary["n_trades"] >= 3)
        & ((summary["pnl_rub"] > 0) if positive else (summary["pnl_rub"] < 0))
    ].sort_values("pnl_rub", ascending=not positive)
    if selected.empty:
        return "нет тикеров минимум с 3 сделками"
    return ", ".join(
        f"{row.ticker} ({row.pnl_rub:+,.0f} RUB, {int(row.n_trades)} сделок)"
        for row in selected.head(limit).itertuples()
    )


def _format_active_tickers(
    summary: pd.DataFrame, strategy: str, limit: int = 5
) -> str:
    selected = summary[summary["strategy"] == strategy].sort_values(
        ["n_trades", "pnl_rub"], ascending=[False, False]
    )
    return ", ".join(
        f"{row.ticker} ({int(row.n_trades)} сделок)"
        for row in selected.head(limit).itertuples()
    )


def build_report(
    results: dict[str, dict[str, Any]],
    hashes: dict[str, str],
    metrics: pd.DataFrame,
    tickers: pd.DataFrame,
    monthly: pd.DataFrame,
) -> str:
    winner = metrics["pnl_rub"].idxmax()
    loser = next(strategy for strategy in STRATEGIES if strategy != winner)
    paper_candidate = {
        strategy: (
            metrics.loc[strategy, "pnl_rub"] > 0
            and metrics.loc[strategy, "profit_factor"] is not None
            and float(metrics.loc[strategy, "profit_factor"]) > 1.0
            and metrics.loc[strategy, "n_trades"] >= 30
            and not metrics.loc[strategy, "game_over"]
        )
        for strategy in STRATEGIES
    }
    metric_lines = []
    for strategy in STRATEGIES:
        row = metrics.loc[strategy]
        metric_lines.append(
            f"| {DISPLAY_NAMES[strategy]} | {row.final_equity_rub:,.2f} | "
            f"{row.pnl_rub:+,.2f} | {row.pnl_pct:+.2f}% | {int(row.n_trades)} | "
            f"{float(row.win_rate_pct):.1f}% | {float(row.profit_factor):.2f} | "
            f"{row.max_drawdown_pct:.2f}% | {'да' if row.game_over else 'нет'} |"
        )

    month_lines = []
    for strategy in STRATEGIES:
        subset = monthly[monthly["strategy"] == strategy]
        profitable = subset.loc[subset["pnl_rub"] > 0, "month"].tolist()
        losing = subset.loc[subset["pnl_rub"] < 0, "month"].tolist()
        month_lines.append(
            f"- **{DISPLAY_NAMES[strategy]}:** прибыльные месяцы — "
            f"{', '.join(profitable) or 'нет'}; убыточные — {', '.join(losing) or 'нет'}."
        )

    game_over_lines = []
    for strategy in STRATEGIES:
        result = results[strategy]
        if result.get("game_over"):
            game_over_lines.append(
                f"- **{DISPLAY_NAMES[strategy]}:** GAME OVER в "
                f"`{result.get('game_over_ts')}`; к этому моменту капитал был исчерпан "
                "после фиксации убытка."
            )
        else:
            game_over_lines.append(
                f"- **{DISPLAY_NAMES[strategy]}:** GAME OVER не наступил."
            )

    readiness = []
    for strategy in STRATEGIES:
        if paper_candidate[strategy]:
            verdict = (
                "допустим только ограниченный forward paper trading; реальный капитал "
                f"не рекомендован при историческом max DD "
                f"{metrics.loc[strategy, 'max_drawdown_pct']:.2f}%"
            )
        else:
            verdict = "запуск не рекомендован до положительного walk-forward результата"
        readiness.append(f"- **{DISPLAY_NAMES[strategy]}:** {verdict}.")

    return f"""# Issue #44: levels_reversal vs ATR reversal

## Резюме

На фиксированном портфеле 50,000 RUB лучший итог показала **{DISPLAY_NAMES[winner]}**:
её PnL выше на {metrics.loc[winner, 'pnl_rub'] - metrics.loc[loser, 'pnl_rub']:,.2f} RUB.
Это сравнение является историческим бэктестом и не доказывает будущую доходность.

## Методика

- Период: `{results[winner]['date_from']}` — `{results[winner]['date_to']}`.
- Общий капитал: 50,000 RUB; слот: 10,000 RUB; максимум 5 позиций.
- Комиссия включена в `net_return_pct` симулятора.
- Equity по дням реконструирована по закрытым сделкам (realized PnL), без mark-to-market
  открытых позиций.
- Одинаковый порядок тикеров и правила конкуренции за слот применены к обеим стратегиям.

## Сравнительные метрики

| Стратегия | Итоговый equity, RUB | PnL, RUB | PnL, % | Сделки | Win rate | Profit factor | Max DD | GAME OVER |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
{chr(10).join(metric_lines)}

![Equity curves](plots/equity_curves.png)

## Анализ сделок и тикеров

![Trade PnL distribution](plots/trade_pnl_distribution.png)

![Ticker PnL heatmap](plots/ticker_pnl_heatmap.png)

### Лучшие и худшие тикеры

- **Levels reversal, больше всего сделок:** {_format_active_tickers(tickers, 'levels_reversal')}.
- **ATR reversal, больше всего сделок:** {_format_active_tickers(tickers, 'atr_reversal')}.
- **Levels reversal, прибыльные:** {_format_tickers(tickers, 'levels_reversal', True)}.
- **Levels reversal, убыточные:** {_format_tickers(tickers, 'levels_reversal', False)}.
- **ATR reversal, прибыльные:** {_format_tickers(tickers, 'atr_reversal', True)}.
- **ATR reversal, убыточные:** {_format_tickers(tickers, 'atr_reversal', False)}.

## Анализ по месяцам

{chr(10).join(month_lines)}

## GAME OVER

{chr(10).join(game_over_lines)}

## Рекомендации

{chr(10).join(readiness)}

1. Для paper trading использовать только стратегию с положительным PnL, PF > 1,
   минимум 30 сделками и без GAME OVER; ограничить размер позиции и задать stop-критерий
   по drawdown.
2. Для ATR reversal прогнать walk-forward сетку:
   `atr_completion=0.75..0.95`, `volume_spike_mult=1.5..2.5`,
   `stop_atr_mult=0.75..1.25`, `take_atr_mult=0.85..1.50`.
3. Тикерные фильтры строить только на in-sample части и подтверждать на следующем
   временном окне; не исключать тикеры по этому full-sample результату напрямую.
4. Следующая итерация должна добавить mark-to-market equity и сравнение с buy-and-hold,
   чтобы drawdown учитывал открытые позиции.

## Воспроизводимость

- `levels_reversal` SHA-256: `{hashes['levels_reversal']}`
- `atr_reversal` SHA-256: `{hashes['atr_reversal']}`
- Код расчётов: `analysis.py`; интерактивный walkthrough: `analysis.ipynb`.

![Metrics comparison](plots/metrics_comparison.png)
"""


def run_analysis(
    input_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    results, hashes = load_results(input_paths)
    trades = {
        strategy: trades_frame(results[strategy], strategy) for strategy in STRATEGIES
    }
    all_trades = pd.concat(trades.values(), ignore_index=True)
    metrics = metrics_frame(results)
    tickers = ticker_summary(all_trades)
    monthly = monthly_summary(all_trades)
    equity = {
        strategy: daily_equity(results[strategy], trades[strategy])
        for strategy in STRATEGIES
    }

    for strategy in STRATEGIES:
        expected = float(metrics.loc[strategy, "final_equity_rub"])
        actual = float(equity[strategy].iloc[-1])
        if not np.isclose(actual, expected, atol=0.02):
            raise ValueError(
                f"{strategy}: reconstructed equity {actual:.2f} != metrics {expected:.2f}"
            )

    plot_equity(equity)
    plot_trade_distribution(all_trades)
    plot_ticker_heatmap(tickers)
    plot_metrics_table(metrics)
    report = build_report(results, hashes, metrics, tickers, monthly)
    (ANALYSIS_DIR / "report.md").write_text(report, encoding="utf-8")

    summary = {
        "metrics": metrics.reset_index().to_dict(orient="records"),
        "input_sha256": hashes,
        "plot_files": sorted(path.name for path in PLOTS_DIR.glob("*.png")),
    }
    (ANALYSIS_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "results": results,
        "trades": trades,
        "all_trades": all_trades,
        "metrics": metrics,
        "tickers": tickers,
        "monthly": monthly,
        "equity": equity,
        "hashes": hashes,
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue #44 strategy comparison")
    parser.add_argument(
        "--levels",
        type=Path,
        default=DEFAULT_INPUTS["levels_reversal"],
        help="levels_reversal portfolio JSON",
    )
    parser.add_argument(
        "--atr",
        type=Path,
        default=DEFAULT_INPUTS["atr_reversal"],
        help="atr_reversal portfolio JSON",
    )
    args = parser.parse_args()
    analysis = run_analysis(
        {"levels_reversal": args.levels, "atr_reversal": args.atr}
    )
    print(analysis["metrics"].to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
