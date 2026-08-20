"""Reproducible portfolio analysis for Issue #100 (test_20260820, id=102).

Run from the repository root:
    python analytics/issue-100-test-20260820-portfolio/analysis.py
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


STRATEGY = "levels_reversal"
DISPLAY_NAME = "test_20260820"
ANALYSIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANALYSIS_DIR.parents[1]
DEFAULT_INPUT = (
    REPO_ROOT / "reports/Arctic/100_test-20260820-portfolio/full_run.json"
)
PLOTS_DIR = ANALYSIS_DIR / "plots"
ISSUE44_CONTEXT = {
    "strategy_id": 36,
    "strategy_name": "test_20260731",
    "level_method": ["swing", "impulse"],
    "date_to": "2026-08-15",
    "resistance_veto": False,
    "final_equity_rub": 96343.49,
    "pnl_rub": 46343.49,
    "pnl_pct": 92.69,
    "n_trades": 3500,
    "win_rate_pct": 28.3,
    "profit_factor": 1.31,
    "max_drawdown_pct": 5.47,
    "event_max_drawdown_pct": 5.93,
    "game_over": False,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pattern_names(config: dict[str, Any]) -> list[str]:
    patterns = config.get("patterns") or {}
    if isinstance(patterns, dict):
        return list(patterns.keys())
    if isinstance(patterns, list):
        return [str(item) for item in patterns]
    return []


def _levels_params(config: dict[str, Any]) -> dict[str, Any]:
    patterns = config.get("patterns") or {}
    if isinstance(patterns, dict):
        raw = patterns.get("levels_reversal") or {}
        return raw if isinstance(raw, dict) else {}
    return {}


def _load_result(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        result = json.load(stream)

    if result.get("status") != "success":
        raise ValueError(f"{path}: expected status=success, got {result.get('status')!r}")
    if int(result.get("strategy_id", 0)) != 102:
        raise ValueError(f"{path}: expected strategy_id=102, got {result.get('strategy_id')!r}")
    if result.get("strategy_config_name") != "test_20260820":
        raise ValueError(
            f"{path}: expected strategy_config_name='test_20260820', "
            f"got {result.get('strategy_config_name')!r}"
        )
    if result.get("strategy") != STRATEGY:
        raise ValueError(
            f"{path}: expected strategy={STRATEGY!r}, got {result.get('strategy')!r}"
        )
    if float(result.get("initial_capital_rub", 0)) != 50_000.0:
        raise ValueError(f"{path}: Issue #100 requires initial capital of 50,000 RUB")
    if float(result.get("slot_size_rub", 0)) != 10_000.0:
        raise ValueError(f"{path}: Issue #100 requires slot size of 10,000 RUB")
    if int(result.get("max_positions", 0)) != 5:
        raise ValueError(f"{path}: Issue #100 requires max_positions=5")
    if result.get("date_from") != "2024-08-01":
        raise ValueError(f"{path}: expected date_from=2024-08-01")
    if str(result.get("date_to")) < "2026-08-21":
        raise ValueError(
            f"{path}: exclusive date_to must cover 2026-08-20 "
            f"(got {result.get('date_to')!r})"
        )
    if not isinstance(result.get("trades"), list):
        raise ValueError(f"{path}: trades must be a list")
    if not isinstance(result.get("metrics"), dict):
        raise ValueError(f"{path}: metrics must be an object")
    if not isinstance(result.get("strategy_config"), dict):
        raise ValueError(f"{path}: strategy_config must be an object")
    return result


def trades_frame(result: dict[str, Any]) -> pd.DataFrame:
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
    frame["strategy"] = STRATEGY
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


def _drawdown_stats(equity: pd.Series) -> dict[str, Any]:
    running_peak = equity.cummax()
    drawdown = (running_peak - equity) / running_peak * 100.0
    trough_ts = drawdown.idxmax()
    peak_ts = equity.loc[:trough_ts].idxmax()
    return {
        "max_drawdown_pct": round(float(drawdown.loc[trough_ts]), 2),
        "max_drawdown_peak_date": str(pd.Timestamp(peak_ts).date()),
        "max_drawdown_trough_date": str(pd.Timestamp(trough_ts).date()),
        "max_drawdown_peak_equity_rub": round(float(equity.loc[peak_ts]), 2),
        "max_drawdown_trough_equity_rub": round(float(equity.loc[trough_ts]), 2),
        "max_drawdown_rub": round(
            float(equity.loc[peak_ts] - equity.loc[trough_ts]), 2
        ),
    }


def metrics_row(result: dict[str, Any], equity: pd.Series) -> pd.Series:
    metrics = result["metrics"]
    drawdown = _drawdown_stats(equity)
    row = {
        "strategy": STRATEGY,
        "final_equity_rub": float(metrics["final_equity_rub"]),
        "pnl_rub": float(metrics["pnl_rub"]),
        "pnl_pct": float(metrics["pnl_pct"]),
        "n_trades": int(metrics["n_trades"]),
        "win_rate_pct": metrics["win_rate"],
        "profit_factor": metrics["profit_factor"],
        "event_max_drawdown_pct": float(metrics["max_drawdown_pct"]),
        "game_over": bool(result.get("game_over")),
        "skipped_entries": int(result.get("skipped_entries_no_slot", 0)),
        **drawdown,
    }
    return pd.Series(row)


def daily_equity(result: dict[str, Any], trades: pd.DataFrame) -> pd.Series:
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


def ticker_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["ticker", "n_trades", "pnl_rub", "win_rate_pct"])
    grouped = (
        trades.groupby("ticker", observed=True)
        .agg(
            n_trades=("pnl_rub", "size"),
            pnl_rub=("pnl_rub", "sum"),
            win_rate_pct=("pnl_rub", lambda values: (values > 0).mean() * 100),
        )
        .reset_index()
        .sort_values("ticker")
    )
    return grouped


def monthly_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["month", "n_trades", "pnl_rub"])
    frame = trades.assign(month=trades["exit_ts"].dt.to_period("M").astype(str))
    return (
        frame.groupby("month", observed=True)
        .agg(n_trades=("pnl_rub", "size"), pnl_rub=("pnl_rub", "sum"))
        .reset_index()
        .sort_values("month")
    )


def _save_figure(figure: plt.Figure, filename: str) -> None:
    figure.tight_layout()
    figure.savefig(PLOTS_DIR / filename, dpi=160, bbox_inches="tight")
    plt.close(figure)


def plot_equity(equity: pd.Series) -> None:
    figure, axis = plt.subplots(figsize=(12, 6))
    axis.plot(equity.index, equity.values, label=DISPLAY_NAME, linewidth=2, color="#1f77b4")
    axis.axhline(50_000, color="gray", linestyle="--", linewidth=1, label="Start: 50,000 RUB")
    axis.set_title("Daily realized equity — test_20260820")
    axis.set_xlabel("Date")
    axis.set_ylabel("Equity (RUB)")
    axis.grid(alpha=0.25)
    axis.legend()
    _save_figure(figure, "equity_curves.png")


def plot_trade_distribution(trades: pd.DataFrame) -> None:
    figure, axis = plt.subplots(figsize=(10, 6))
    values = trades["pnl_rub"]
    if not values.empty:
        axis.hist(values, bins=30, color="#1f77b4", alpha=0.75, label=DISPLAY_NAME)
    axis.axvline(0, color="black", linewidth=1)
    axis.set_title("Distribution of realized PnL per trade")
    axis.set_xlabel("Trade PnL (RUB)")
    axis.set_ylabel("Number of trades")
    axis.legend()
    _save_figure(figure, "trade_pnl_distribution.png")


def plot_ticker_heatmap(summary: pd.DataFrame) -> None:
    if summary.empty:
        figure, axis = plt.subplots(figsize=(6, 3))
        axis.axis("off")
        axis.set_title("No portfolio trades")
        _save_figure(figure, "ticker_pnl_heatmap.png")
        return
    pivot = summary.set_index("ticker")[["pnl_rub"]].sort_index()
    limit = max(float(np.abs(pivot.to_numpy()).max()), 1.0)
    figure_height = max(7.0, len(pivot) * 0.34)
    figure, axis = plt.subplots(figsize=(5.5, figure_height))
    image = axis.imshow(
        pivot.to_numpy(),
        aspect="auto",
        cmap="RdYlGn",
        vmin=-limit,
        vmax=limit,
    )
    axis.set_title("Realized PnL by ticker")
    axis.set_xlabel("Strategy")
    axis.set_ylabel("Ticker")
    axis.set_xticks([0], [DISPLAY_NAME])
    axis.set_yticks(range(len(pivot.index)), pivot.index)
    for row in range(len(pivot.index)):
        axis.text(
            0,
            row,
            f"{pivot.iloc[row, 0]:,.0f}",
            ha="center",
            va="center",
            fontsize=7,
        )
    colorbar = figure.colorbar(image, ax=axis)
    colorbar.set_label("PnL (RUB)")
    _save_figure(figure, "ticker_pnl_heatmap.png")


def plot_metrics_table(metrics: pd.Series) -> None:
    table = pd.DataFrame(
        [
            [
                f"{metrics.final_equity_rub:,.2f}",
                f"{metrics.pnl_rub:,.2f}",
                f"{float(metrics.pnl_pct):.2f}",
                f"{int(metrics.n_trades)}",
                "—" if pd.isna(metrics.win_rate_pct) else f"{float(metrics.win_rate_pct):.2f}",
                "—" if pd.isna(metrics.profit_factor) else f"{float(metrics.profit_factor):.2f}",
                f"{metrics.max_drawdown_pct:.2f}",
                "yes" if metrics.game_over else "no",
            ]
        ],
        index=[DISPLAY_NAME],
        columns=[
            "Final equity, RUB",
            "PnL, RUB",
            "PnL, %",
            "Trades",
            "Win rate, %",
            "Profit factor",
            "Max DD, %",
            "GAME OVER",
        ],
    )
    figure, axis = plt.subplots(figsize=(14, 2.1))
    axis.axis("off")
    rendered = axis.table(
        cellText=table.values,
        rowLabels=table.index,
        colLabels=table.columns,
        cellLoc="center",
        loc="center",
    )
    rendered.auto_set_font_size(False)
    rendered.set_fontsize(9)
    rendered.scale(1, 1.5)
    axis.set_title("Portfolio backtest metrics (50,000 RUB start)", pad=18)
    _save_figure(figure, "metrics_comparison.png")


def _format_tickers(summary: pd.DataFrame, positive: bool, limit: int = 5) -> str:
    selected = summary[
        (summary["n_trades"] >= 3)
        & ((summary["pnl_rub"] > 0) if positive else (summary["pnl_rub"] < 0))
    ].sort_values("pnl_rub", ascending=not positive)
    if selected.empty:
        return "нет тикеров минимум с 3 сделками"
    return ", ".join(
        f"{row.ticker} ({row.pnl_rub:+,.0f} RUB, {int(row.n_trades)} сделок)"
        for row in selected.head(limit).itertuples()
    )


def _format_active_tickers(summary: pd.DataFrame, limit: int = 5) -> str:
    if summary.empty:
        return "нет сделок"
    selected = summary.sort_values(["n_trades", "pnl_rub"], ascending=[False, False])
    return ", ".join(
        f"{row.ticker} ({int(row.n_trades)} сделок)"
        for row in selected.head(limit).itertuples()
    )


def _paper_ready(metrics: pd.Series) -> bool:
    pf = metrics.profit_factor
    return bool(
        metrics.pnl_rub > 0
        and pf is not None
        and not (isinstance(pf, float) and np.isnan(pf))
        and float(pf) > 1.0
        and metrics.n_trades >= 30
        and not metrics.game_over
    )


def _alrs_blocker(result: dict[str, Any]) -> dict[str, Any]:
    check = result.get("alrs_veto_check") or {}
    present = bool(
        check.get("found_in_candidates") or check.get("found_in_portfolio_trades")
    )
    return {
        "present": present,
        "found_in_candidates": bool(check.get("found_in_candidates")),
        "found_in_portfolio_trades": bool(check.get("found_in_portfolio_trades")),
        "timestamp": check.get("timestamp", "2026-08-20 11:50:24"),
        "price": check.get("price", 19.80),
        "candidate_hits": check.get("candidate_hits") or [],
        "portfolio_hits": check.get("portfolio_hits") or [],
    }


def build_report(
    result: dict[str, Any],
    digest: str,
    metrics: pd.Series,
    tickers: pd.DataFrame,
    monthly: pd.DataFrame,
    alrs: dict[str, Any],
) -> str:
    config = result.get("strategy_config") or {}
    levels = _levels_params(config)
    rr = config.get("risk_reward") or {}
    paper_ok = _paper_ready(metrics)
    last_day = result.get("period_last_day") or "2026-08-20"
    volume_tickers = result.get("tickers_volume_order") or result.get("tickers") or []
    failed = result.get("failed_tickers") or []
    pf_text = (
        "—"
        if metrics.profit_factor is None or pd.isna(metrics.profit_factor)
        else f"{float(metrics.profit_factor):.2f}"
    )
    wr_text = (
        "—"
        if metrics.win_rate_pct is None or pd.isna(metrics.win_rate_pct)
        else f"{float(metrics.win_rate_pct):.1f}%"
    )
    profitable = monthly.loc[monthly["pnl_rub"] > 0, "month"].tolist()
    losing = monthly.loc[monthly["pnl_rub"] < 0, "month"].tolist()
    if result.get("game_over"):
        game_over_line = (
            f"GAME OVER в `{result.get('game_over_ts')}`; к этому моменту капитал "
            "был исчерпан после фиксации убытка."
        )
    else:
        game_over_line = "GAME OVER не наступил."
    if paper_ok:
        verdict = (
            "По правилам #44 ограниченный forward paper **допустим** "
            f"(PnL {metrics.pnl_rub:+,.2f} RUB, PF {pf_text}, "
            f"n={int(metrics.n_trades)}, без GAME OVER; daily Max DD "
            f"{metrics.max_drawdown_pct:.2f}%). Параметры `test_20260820` и locked "
            "`test_20260731` не менять."
        )
    else:
        verdict = (
            "По правилам #44 это **не кандидат** в paper: нужны положительный PnL, "
            "PF>1, n≥30 и отсутствие GAME OVER. Параметры `test_20260820` и locked "
            "`test_20260731` не крутить."
        )
    if alrs["present"]:
        alrs_line = (
            f"**БЛОКЕР:** бар `{alrs['timestamp']}` @ {float(alrs['price']):.2f} найден во входах "
            f"(candidates={alrs['found_in_candidates']}, "
            f"portfolio={alrs['found_in_portfolio_trades']})."
        )
    else:
        alrs_line = (
            f"Бар `{alrs['timestamp']}` @ {float(alrs['price']):.2f} **не** найден ни среди "
            "per-ticker candidate entries, ни среди портфельных сделок. Вето #97 "
            "на этом баре сработало."
        )
    failed_line = (
        ", ".join(f"{item['ticker']} ({item.get('error')})" for item in failed)
        if failed
        else "нет"
    )
    return f"""# Issue #100: портфельный бэктест test_20260820

## Резюме

Один конфиг: **{result['strategy_config_name']}** (`trading.strategies.id={result['strategy_id']}`),
плагин `{result['strategy']}`. На общем капитале 50,000 RUB итоговый equity
{metrics.final_equity_rub:,.2f} RUB ({metrics.pnl_rub:+,.2f} RUB, {metrics.pnl_pct:+.2f}%).
Это исторический бэктест и не доказывает будущую доходность.

**Вердикт для продукта:** {verdict}

## Подтверждение конфига id=102

- Имя / id: `{result['strategy_config_name']}` / `{result['strategy_id']}`.
- Paper / locked: `in_paper_test={str(result.get('in_paper_test')).lower()}`,
  `locked={str(result.get('locked')).lower()}`. Locked `test_20260731` (id=36)
  не читался и не записывался (`locked_reference_id_untouched=36`).
- Plugin: `{result['strategy']}` (`config.strategy_name` / `resolve_strategy_name`).
- Паттерны: `{', '.join(_pattern_names(config)) or '—'}`.
- Уровни: `level_method={levels.get('level_method')}`,
  `swing_window={levels.get('swing_window')}`,
  `zone_atr_mult={levels.get('zone_atr_mult')}`,
  `level_timeframe={levels.get('level_timeframe')}`.
- Confirm / RR: `{config.get('confirm_windows')}`,
  RR {rr.get('risk')}:{rr.get('reward')},
  commission {config.get('commission_pct')}%,
  slippage {config.get('slippage_pct')},
  n_runs `{config.get('n_runs')}`.
- Период: `{result['date_from']}` — `{last_day}` (запрос `timestamp < {result['date_to']}`,
  exclusive-конвенция `MODE_PRESETS`; #44 заканчивался exclusive `2026-08-15`).
- Вселенная: `get_tickers_by_volume(..., max_tickers=None)`, порядок = рейтинг объёма.
- Тикеры в volume-order: `{', '.join(volume_tickers)}`.
- Не загружены: {failed_line}.
- SHA-256 входного JSON: `{digest}`.

## Методика

- Движок: `run_portfolio_backtest` → `LevelsReversalStrategy` → `StrategyEvaluator`
  **после** #97 (вето resistance-зоны включено).
- Капитал 50,000 RUB; слот 10,000 RUB; максимум 5 позиций.
- Конкуренция: статический volume rank; нет слота → skip (`skipped_entries_no_slot`).
- Комиссия уже в `net_return_pct`.
- Equity по дням — по закрытым сделкам, без mark-to-market.
- Max DD в таблице — по equity на конец дня; event-based Max DD симулятора отдельно.
- Lab express / full-sample по 28 тикерам и сравнение с ATR в этот отчёт не входят.

## Портфельные метрики

| Стратегия | Итоговый equity, RUB | PnL, RUB | PnL, % | Сделки | Win rate | Profit factor | Max DD | GAME OVER |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| {DISPLAY_NAME} | {metrics.final_equity_rub:,.2f} | {metrics.pnl_rub:+,.2f} | {metrics.pnl_pct:+.2f}% | {int(metrics.n_trades)} | {wr_text} | {pf_text} | {metrics.max_drawdown_pct:.2f}% | {'да' if metrics.game_over else 'нет'} |

- skipped no-slot: `{int(metrics.skipped_entries)}`.
- candidate trades до replay: `{int(result.get('candidate_trades', 0))}`.

![Equity curve](plots/equity_curves.png)

### Максимальная просадка

- Daily Max DD: {metrics.max_drawdown_pct:.2f}% с {metrics.max_drawdown_peak_date}
  по {metrics.max_drawdown_trough_date}; equity снизилась с
  {metrics.max_drawdown_peak_equity_rub:,.2f} до
  {metrics.max_drawdown_trough_equity_rub:,.2f} RUB
  (−{metrics.max_drawdown_rub:,.2f} RUB).
- Event-based Max DD симулятора: {metrics.event_max_drawdown_pct:.2f}%.

## Анализ сделок и тикеров

![Trade PnL distribution](plots/trade_pnl_distribution.png)

![Ticker PnL heatmap](plots/ticker_pnl_heatmap.png)

- **Больше всего сделок:** {_format_active_tickers(tickers)}.
- **Прибыльные:** {_format_tickers(tickers, True)}.
- **Убыточные:** {_format_tickers(tickers, False)}.

## Анализ по месяцам

- Прибыльные месяцы — {', '.join(profitable) or 'нет'}; убыточные — {', '.join(losing) or 'нет'}.

## Точечная проверка ALRS (paper #711)

{alrs_line}

## GAME OVER

{game_over_line}

## Рекомендации

1. {verdict}
2. Не подменять этот портфельный прогон Lab UI (`full_sample` express) и не считать
   его достаточным.
3. Не сужать вселенную до ALRS / live top-5 и не крутить RR / confirm / `level_method`.
4. Следующая итерация по правилам #44: mark-to-market equity и walk-forward, если
   продукт снова рассматривает paper.

## Контекст Issue #44 (другой конфиг, не сравнение)

Исторический пакет `analytics/issue-44-strategy-comparison/`: locked
`test_20260731` id=36, `level_method=['swing','impulse']`, exclusive
`date_to=2026-08-15`, движок **до** вето #97. Levels reversal тогда:
equity {ISSUE44_CONTEXT['final_equity_rub']:,.2f} RUB,
PnL {ISSUE44_CONTEXT['pnl_rub']:+,.2f} RUB ({ISSUE44_CONTEXT['pnl_pct']:+.2f}%),
{ISSUE44_CONTEXT['n_trades']} сделок, WR {ISSUE44_CONTEXT['win_rate_pct']:.1f}%,
PF {ISSUE44_CONTEXT['profit_factor']:.2f}, daily Max DD
{ISSUE44_CONTEXT['max_drawdown_pct']:.2f}%. Цифры даны только как фон; это
другой конфиг и другой `date_to`. ATR в этом отчёте не сравнивается.

## Воспроизводимость

- Входной JSON SHA-256: `{digest}`
- Код расчётов: `analysis.py`; интерактивный walkthrough: `analysis.ipynb`.

![Metrics summary](plots/metrics_comparison.png)
"""


def run_analysis(input_path: Path | None = None) -> dict[str, Any]:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = Path(input_path or DEFAULT_INPUT)
    result = _load_result(path)
    digest = _sha256(path)
    trades = trades_frame(result)
    equity = daily_equity(result, trades)
    metrics = metrics_row(result, equity)
    tickers = ticker_summary(trades)
    monthly = monthly_summary(trades)
    alrs = _alrs_blocker(result)

    expected = float(metrics.final_equity_rub)
    actual = float(equity.iloc[-1])
    if not np.isclose(actual, expected, atol=0.02):
        raise ValueError(
            f"reconstructed equity {actual:.2f} != metrics {expected:.2f}"
        )
    if alrs["present"]:
        raise ValueError(
            "ALRS blocker: paper #711 bar is present in entries "
            f"(candidates={alrs['found_in_candidates']}, "
            f"portfolio={alrs['found_in_portfolio_trades']})"
        )

    plot_equity(equity)
    plot_trade_distribution(trades)
    plot_ticker_heatmap(tickers)
    plot_metrics_table(metrics)
    report = build_report(result, digest, metrics, tickers, monthly, alrs)
    (ANALYSIS_DIR / "report.md").write_text(report, encoding="utf-8")

    summary = {
        "strategy_id": result["strategy_id"],
        "strategy_config_name": result["strategy_config_name"],
        "plugin": result["strategy"],
        "date_from": result["date_from"],
        "date_to": result["date_to"],
        "period_last_day": result.get("period_last_day"),
        "tickers_volume_order": result.get("tickers_volume_order") or result.get("tickers"),
        "paper_ready": _paper_ready(metrics),
        "alrs_veto_absent": True,
        "metrics": [metrics.to_dict()],
        "input_sha256": digest,
        "plot_files": sorted(path.name for path in PLOTS_DIR.glob("*.png")),
        "issue44_context": ISSUE44_CONTEXT,
    }
    (ANALYSIS_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return {
        "result": result,
        "trades": trades,
        "metrics": metrics,
        "tickers": tickers,
        "monthly": monthly,
        "equity": equity,
        "hash": digest,
        "alrs": alrs,
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue #100 portfolio analysis")
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="portfolio JSON from generate_inputs.py",
    )
    args = parser.parse_args()
    analysis = run_analysis(args.input)
    print(analysis["metrics"].to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
