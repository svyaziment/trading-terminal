"""Reproducible portfolio analysis for Issue #130 (levels_sr_support).

Run from the repository root:

    python analytics/issue-130-sr-support-portfolio/analysis.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

ANALYSIS_DIR = Path(__file__).resolve().parent
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from generate_inputs import (
    DATE_FROM,
    DATE_TO,
    DEFAULT_OUTPUT,
    EXPECTED_CANDIDATE_N,
    EXPECTED_SHA_C,
    INITIAL_CAPITAL,
    MAX_POSITIONS,
    SLOT_SIZE,
    SOURCE_C,
    VOLUME_ORDER_103,
    alrs_hits,
    assert_isolated,
    replay_from_129,
)


DISPLAY_NAME = "levels_sr_support"
REPO_ROOT = ANALYSIS_DIR.parents[1]
PLOTS_DIR = ANALYSIS_DIR / "plots"

ISSUE44_CONTEXT = {
    "book": "#44 test_20260731",
    "strategy_id": 36,
    "strategy_name": "test_20260731",
    "patterns": ["levels_reversal", "signal_4h_buy"],
    "level_method": ["swing", "impulse"],
    "date_to": "2026-08-15",
    "tracker_veto": False,
    "resistance_path": False,
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
ISSUE103_CONTEXT = {
    "book": "#103 test_20260821",
    "strategy_id": 118,
    "strategy_name": "test_20260821",
    "patterns": ["levels_reversal", "signal_4h_buy"],
    "level_method": ["swing", "impulse"],
    "date_to": "2026-08-21",
    "tracker_veto": False,
    "resistance_path": False,
    "final_equity_rub": 89055.31,
    "pnl_rub": 39055.31,
    "pnl_pct": 78.11,
    "n_trades": 2070,
    "win_rate_pct": 28.7,
    "profit_factor": 1.34,
    "max_drawdown_pct": 6.82,
    "event_max_drawdown_pct": 9.58,
    "game_over": False,
}
ISSUE124_BMIX = {
    "book": "#124 B-mix (optional)",
    "strategy_name": "levels_sr_breakout",
    "patterns": ["levels_sr_breakout", "signal_4h_buy"],
    "date_to": "2026-08-21",
    "tracker_veto": True,
    "resistance_path": True,
    "final_equity_rub": 98432.94,
    "pnl_rub": 48432.94,
    "pnl_pct": 96.87,
    "n_trades": 2837,
    "win_rate_pct": 30.3,
    "profit_factor": 1.32,
    "event_max_drawdown_pct": 7.93,
    "game_over": False,
    "candidate_n": 4799,
    "note": "Composite OR (support+resistance). Do not mix with C.",
}
ISSUE129_C = {
    "book": "#129 isolated C",
    "n": 4380,
    "pf": 1.45,
    "note": "Isolated ticker PF, not a 50k portfolio.",
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


def _support_params(config: dict[str, Any]) -> dict[str, Any]:
    patterns = config.get("patterns") or {}
    if isinstance(patterns, dict):
        raw = patterns.get("levels_sr_support") or {}
        return raw if isinstance(raw, dict) else {}
    return {}


def _load_result(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        result = json.load(stream)
    return _validate_result(result, origin=str(path))


def _validate_result(result: dict[str, Any], origin: str) -> dict[str, Any]:
    if result.get("status") != "success":
        raise ValueError(f"{origin}: expected status=success, got {result.get('status')!r}")
    if result.get("strategy_config_name") != DISPLAY_NAME:
        raise ValueError(
            f"{origin}: expected strategy_config_name={DISPLAY_NAME!r}, "
            f"got {result.get('strategy_config_name')!r}"
        )
    if float(result.get("initial_capital_rub", 0)) != INITIAL_CAPITAL:
        raise ValueError(f"{origin}: Issue #130 requires initial capital of 50,000 RUB")
    if float(result.get("slot_size_rub", 0)) != SLOT_SIZE:
        raise ValueError(f"{origin}: Issue #130 requires slot size of 10,000 RUB")
    if int(result.get("max_positions", 0)) != MAX_POSITIONS:
        raise ValueError(f"{origin}: Issue #130 requires max_positions=5")
    if result.get("date_from") != DATE_FROM:
        raise ValueError(f"{origin}: expected date_from={DATE_FROM}")
    if str(result.get("date_to")) < DATE_TO:
        raise ValueError(
            f"{origin}: exclusive date_to must cover 2026-08-20 "
            f"(got {result.get('date_to')!r})"
        )
    if int(result.get("candidate_trades", 0)) != EXPECTED_CANDIDATE_N:
        raise ValueError(
            f"{origin}: expected {EXPECTED_CANDIDATE_N} C candidates, "
            f"got {result.get('candidate_trades')!r}"
        )
    if result.get("config_sha256") != EXPECTED_SHA_C:
        raise ValueError(
            f"{origin}: expected config SHA {EXPECTED_SHA_C}, "
            f"got {result.get('config_sha256')!r}"
        )
    if not isinstance(result.get("trades"), list):
        raise ValueError(f"{origin}: trades must be a list")
    if not isinstance(result.get("metrics"), dict):
        raise ValueError(f"{origin}: metrics must be an object")
    config = result.get("strategy_config") or {}
    if not isinstance(config, dict):
        raise ValueError(f"{origin}: strategy_config must be an object")
    assert_isolated(config)
    levels = _support_params(config)
    if list(levels.get("level_method") or []) != ["swing", "impulse"]:
        raise ValueError(
            f"{origin}: expected level_method=['swing','impulse'], "
            f"got {levels.get('level_method')!r}"
        )
    volume = list(result.get("tickers_volume_order") or [])
    if volume[: len(VOLUME_ORDER_103)] != VOLUME_ORDER_103:
        raise ValueError(f"{origin}: volume order must match #103/#44")
    sources = set(result.get("candidate_sources") or [])
    if sources and sources != {SOURCE_C}:
        raise ValueError(f"{origin}: candidate sources must be only {SOURCE_C}, got {sources}")
    return result


def trades_frame(result: dict[str, Any], key: str = "trades") -> pd.DataFrame:
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
        "source",
    ]
    frame = pd.DataFrame(result.get(key) or [])
    if frame.empty:
        return pd.DataFrame(columns=columns)
    frame["strategy"] = DISPLAY_NAME
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
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "source" not in frame.columns:
        frame["source"] = SOURCE_C
    return frame.reindex(columns=columns)


def resistance_split(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        support = frame
        resistance = frame
    else:
        support = frame[frame["source"] == SOURCE_C]
        resistance = frame[
            frame["source"].astype(str).str.contains("resistance", na=False)
        ]
    return {
        "support_n": int(len(support)),
        "resistance_n": int(len(resistance)),
        "other_n": int(len(frame) - len(support) - len(resistance)),
    }


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
    pf = metrics.get("profit_factor")
    row = {
        "strategy": DISPLAY_NAME,
        "final_equity_rub": float(metrics["final_equity_rub"]),
        "pnl_rub": float(metrics["pnl_rub"]),
        "pnl_pct": float(metrics["pnl_pct"]),
        "n_trades": int(metrics["n_trades"]),
        "win_rate_pct": metrics.get("win_rate"),
        "profit_factor": None if pf is None or (isinstance(pf, float) and not math.isfinite(pf)) else pf,
        "event_max_drawdown_pct": float(metrics["max_drawdown_pct"]),
        "game_over": bool(result.get("game_over")),
        "skipped_entries": int(result.get("skipped_entries_no_slot", 0)),
        "candidate_trades": int(result.get("candidate_trades", 0)),
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
    axis.set_title("Daily realized equity — levels_sr_support (Issue #130)")
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
    rows = [
        ISSUE44_CONTEXT,
        ISSUE103_CONTEXT,
        {
            "book": "C levels_sr_support",
            "final_equity_rub": metrics.final_equity_rub,
            "n_trades": int(metrics.n_trades),
            "profit_factor": metrics.profit_factor,
            "max_drawdown_pct": metrics.max_drawdown_pct,
            "game_over": bool(metrics.game_over),
        },
        ISSUE124_BMIX,
    ]
    table = pd.DataFrame(
        [
            [
                row["book"],
                f"{row['final_equity_rub']:,.2f}",
                f"{int(row['n_trades'])}",
                "—" if row.get("profit_factor") is None else f"{float(row['profit_factor']):.2f}",
                f"{float(row.get('max_drawdown_pct') or row.get('event_max_drawdown_pct') or 0):.2f}",
                "yes" if row.get("game_over") else "no",
            ]
            for row in rows
        ],
        columns=["Book", "Final equity, RUB", "Trades", "Profit factor", "Max DD, %", "GAME OVER"],
    )
    figure, axis = plt.subplots(figsize=(14, 3.2))
    axis.axis("off")
    rendered = axis.table(
        cellText=table.values,
        colLabels=table.columns,
        cellLoc="center",
        loc="center",
    )
    rendered.auto_set_font_size(False)
    rendered.set_fontsize(9)
    rendered.scale(1, 1.5)
    axis.set_title("Portfolio books (50,000 RUB start). #124 B-mix is a different candidate set.", pad=18)
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


def _paper_gates(metrics: pd.Series) -> bool:
    pf = metrics.profit_factor
    return bool(
        metrics.pnl_rub > 0
        and pf is not None
        and not (isinstance(pf, float) and np.isnan(pf))
        and float(pf) > 1.0
        and metrics.n_trades >= 30
        and not metrics.game_over
    )


def _alrs_blocker(result: dict[str, Any], trades: pd.DataFrame) -> dict[str, Any]:
    check = result.get("alrs_veto_check") or {}
    candidates = result.get("candidates") or []
    candidate_hits = check.get("candidate_hits")
    if candidate_hits is None:
        candidate_hits = alrs_hits(candidates)
    portfolio_hits = check.get("portfolio_hits")
    if portfolio_hits is None:
        portfolio_hits = alrs_hits(trades.to_dict("records"))
    present = bool(candidate_hits or portfolio_hits)
    return {
        "present": present,
        "found_in_candidates": bool(candidate_hits),
        "found_in_portfolio_trades": bool(portfolio_hits),
        "timestamp": check.get("timestamp", "2026-08-20 11:50:24"),
        "price": check.get("price", 19.80),
        "candidate_hits": candidate_hits or [],
        "portfolio_hits": portfolio_hits or [],
        "blocked": not present,
    }


def product_verdict(
    metrics: pd.Series,
    alrs: dict[str, Any],
    split: dict[str, Any],
) -> dict[str, Any]:
    gates = _paper_gates(metrics)
    reasons = [
        "Портфель C построен на isolated `levels_sr_support` из #129 (n кандидатов 4380, SHA конфига стабилен), не фильтром source из композита #124.",
        f"Слоты 50k / 10k / max 5: n={int(metrics.n_trades)}, PF {float(metrics.profit_factor):.2f}, equity {metrics.final_equity_rub:,.2f} RUB, GAME OVER={'да' if metrics.game_over else 'нет'}.",
        "Бар ALRS 2026-08-20 11:50 @ 19.80 отсутствует среди candidates и портфельных входов."
        if alrs["blocked"]
        else "БЛОКЕР: бар ALRS 19.80 найден во входах.",
        f"Resistance-source n={split['resistance_n']} (ожидается 0).",
        "Сравнение с #44/#103 — другие конфиги; #124 B-mix — другой набор кандидатов (support+resistance).",
        "По умолчанию не paper без явного решения Product Owner.",
    ]
    match = (
        alrs["blocked"]
        and split["resistance_n"] == 0
        and int(metrics.candidate_trades) == EXPECTED_CANDIDATE_N
        and not metrics.game_over
    )
    return {
        "paper": False,
        "paper_gates_pass": gates,
        "match": match,
        "label": "не paper",
        "reasons": reasons,
    }


def build_report(
    result: dict[str, Any],
    digest: str,
    metrics: pd.Series,
    tickers: pd.DataFrame,
    monthly: pd.DataFrame,
    alrs: dict[str, Any],
    split: dict[str, Any],
    verdict: dict[str, Any],
) -> str:
    config = result.get("strategy_config") or {}
    levels = _support_params(config)
    rr = config.get("risk_reward") or {}
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
    if alrs["present"]:
        alrs_line = (
            f"**БЛОКЕР:** бар `{alrs['timestamp']}` @ {float(alrs['price']):.2f} найден во входах "
            f"(candidates={alrs['found_in_candidates']}, "
            f"portfolio={alrs['found_in_portfolio_trades']})."
        )
    else:
        alrs_line = (
            f"Бар `{alrs['timestamp']}` @ {float(alrs['price']):.2f} **не** найден ни среди "
            "candidate entries, ни среди портфельных сделок. Вето #97 на этом баре сработало."
        )
    failed_line = (
        ", ".join(f"{item['ticker']} ({item.get('error')})" for item in failed)
        if failed
        else "нет"
    )
    gates_line = (
        f"Гейты #44 (PnL>0, PF>1, n≥30, нет GAME OVER) "
        f"{'пройдены' if verdict['paper_gates_pass'] else 'не пройдены'}, "
        "но вердикт продукта — **не paper** без явного решения PO."
    )
    return f"""# Issue #130: портфель 50k levels_sr_support

## Резюме

Конфиг **C** (`levels_sr_support` + `signal_4h_buy`, SHA `{result['config_sha256']}`),
кандидаты из isolated-книги #129 (n={int(metrics.candidate_trades)}).
На общем капитале 50,000 RUB итоговый equity {metrics.final_equity_rub:,.2f} RUB
({metrics.pnl_rub:+,.2f} RUB, {metrics.pnl_pct:+.2f}%).
Это исторический бэктест и не доказывает будущую доходность.

**Вердикт для продукта:** {verdict['label']}. {gates_line}

## Подтверждение конфига C

- Имя: `{result['strategy_config_name']}`. Lab-черновик не lock/paper-flag.
- Paper / locked: `in_paper_test=false`, `locked=false`.
- Locked `test_20260731` (id=36), swing-only `test_20260820` (id=102) и
  `test_20260821` (id=118) не читались и не записывались.
- Движок кандидатов: `run_strategy_backtest` (пакет #129). На сделках
  `source=levels_sr_support`. Plugin-имя в Lab по-прежнему `levels_reversal`.
- Паттерны: `{', '.join(_pattern_names(config)) or '—'}`.
- Уровни: `level_method={levels.get('level_method')}`,
  `swing_window={levels.get('swing_window')}`,
  `zone_atr_mult={levels.get('zone_atr_mult')}`,
  `level_timeframe={levels.get('level_timeframe')}`,
  `impulse_body_ratio={levels.get('impulse_body_ratio')}`,
  `impulse_atr_mult={levels.get('impulse_atr_mult')}`.
- Confirm / RR: `{config.get('confirm_windows')}`,
  RR {rr.get('risk')}:{rr.get('reward')},
  commission {config.get('commission_pct')}%,
  slippage {config.get('slippage_pct')},
  n_runs `{config.get('n_runs')}`.
- Период: `{result['date_from']}` — `{last_day}` (запрос `timestamp < {result['date_to']}`).
- Вселенная: пересечение `get_big_tickers` с volume-order #103/#44 (28 имён).
- Тикеры в volume-order: `{', '.join(volume_tickers)}`.
- Не загружены: {failed_line}.
- SHA конфига C: `{result['config_sha256']}`.
- SHA-256 входного JSON: `{digest}`.

## Методика

- Кандидаты — isolated C из `analytics/issue-129-sr-support-universe/`
  (**не** exclusive-колонка #124 3811 / 1.51 и **не** `source=`-фильтр композита).
- Капитал 50,000 RUB; слот 10,000 RUB; максимум 5 позиций.
- Конкуренция: статический volume rank; нет слота → skip (`skipped_entries_no_slot`).
- Комиссия уже в `net_return_pct`.
- Equity по дням — по закрытым сделкам, без mark-to-market.
- Max DD в таблице — по equity на конец дня; event-based Max DD симулятора отдельно.
- Isolated PF #129 (1.45) с портфельным PF не смешивать.

## Сравнение книг

| Книга | Что это | Equity, RUB | n | PF | Max DD | GAME OVER |
|---|---|---:|---:|---:|---:|:---:|
| #44 `test_20260731` | портфель до вето, `date_to=2026-08-15` | {ISSUE44_CONTEXT['final_equity_rub']:,.2f} | {ISSUE44_CONTEXT['n_trades']} | {ISSUE44_CONTEXT['profit_factor']:.2f} | {ISSUE44_CONTEXT['max_drawdown_pct']:.2f}% | нет |
| #103 `test_20260821` | портфель после вето, без трекера | {ISSUE103_CONTEXT['final_equity_rub']:,.2f} | {ISSUE103_CONTEXT['n_trades']} | {ISSUE103_CONTEXT['profit_factor']:.2f} | {ISSUE103_CONTEXT['max_drawdown_pct']:.2f}% | нет |
| C `levels_sr_support` | эта задача, вето **с** трекером, без ретеста | {metrics.final_equity_rub:,.2f} | {int(metrics.n_trades)} | {pf_text} | {metrics.max_drawdown_pct:.2f}% | {'да' if metrics.game_over else 'нет'} |
| #124 B-mix | композит support+resistance, другой набор кандидатов | {ISSUE124_BMIX['final_equity_rub']:,.2f} | {ISSUE124_BMIX['n_trades']} | {ISSUE124_BMIX['profit_factor']:.2f} | {ISSUE124_BMIX['event_max_drawdown_pct']:.2f}%* | нет |

\\* у B-mix в пакете #124 опубликован event-based Max DD симулятора, не daily.

Isolated C (не портфель): n={ISSUE129_C['n']}, PF {ISSUE129_C['pf']:.2f}. Exclusive B-support 3811 / 1.51 в портфель не брался.

## Портфельные метрики C

| Стратегия | Итоговый equity, RUB | PnL, RUB | PnL, % | Сделки | Win rate | Profit factor | Max DD | GAME OVER |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| {DISPLAY_NAME} | {metrics.final_equity_rub:,.2f} | {metrics.pnl_rub:+,.2f} | {metrics.pnl_pct:+.2f}% | {int(metrics.n_trades)} | {wr_text} | {pf_text} | {metrics.max_drawdown_pct:.2f}% | {'да' if metrics.game_over else 'нет'} |

- skipped no-slot: `{int(metrics.skipped_entries)}`.
- candidate trades до replay: `{int(metrics.candidate_trades)}`.
- source: support n={split['support_n']}, resistance n={split['resistance_n']}.

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

1. {verdict['label']}: {gates_line}
2. Не подменять этот портфель isolated PF #129 и не смешивать с #124 B-mix.
3. Не lock/overwrite `test_20260731`, `test_20260820`, `test_20260821`.
4. Черновик Lab, если понадобится: `test_YYYYMMDD_sr_support` — не перезаписывать чужие строки.

## Воспроизводимость

- SHA конфига C: `{result['config_sha256']}`
- Входной JSON SHA-256: `{digest}`
- Код расчётов: `analysis.py`; интерактивный walkthrough: `analysis.ipynb`.

![Metrics summary](plots/metrics_comparison.png)
"""


def run_analysis(input_path: Path | None = None) -> dict[str, Any]:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    digest = "replay-from-129"
    if input_path is not None:
        path = Path(input_path)
        result = _load_result(path)
        digest = _sha256(path)
    elif DEFAULT_OUTPUT.exists():
        result = _load_result(DEFAULT_OUTPUT)
        digest = _sha256(DEFAULT_OUTPUT)
    else:
        result = _validate_result(replay_from_129(), origin="replay_from_129")
    trades = trades_frame(result)
    equity = daily_equity(result, trades)
    metrics = metrics_row(result, equity)
    tickers = ticker_summary(trades)
    monthly = monthly_summary(trades)
    alrs = _alrs_blocker(result, trades)
    split = resistance_split(trades)
    verdict = product_verdict(metrics, alrs, split)

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
    if split["resistance_n"] != 0:
        raise ValueError(
            f"resistance-source trades in portfolio: {split['resistance_n']}"
        )

    plot_equity(equity)
    plot_trade_distribution(trades)
    plot_ticker_heatmap(tickers)
    plot_metrics_table(metrics)
    report = build_report(
        result, digest, metrics, tickers, monthly, alrs, split, verdict
    )
    (ANALYSIS_DIR / "report.md").write_text(report, encoding="utf-8")

    summary = {
        "issue": 130,
        "strategy_config_name": result["strategy_config_name"],
        "plugin": result.get("strategy"),
        "config_sha256": result["config_sha256"],
        "date_from": result["date_from"],
        "date_to": result["date_to"],
        "period_last_day": result.get("period_last_day"),
        "tickers_volume_order": result.get("tickers_volume_order") or result.get("tickers"),
        "candidate_trades": int(metrics.candidate_trades),
        "paper": False,
        "paper_gates_pass": verdict["paper_gates_pass"],
        "alrs_veto_absent": True,
        "resistance_n": split["resistance_n"],
        "verdict": verdict,
        "metrics": [metrics.to_dict()],
        "input_sha256": digest,
        "plot_files": sorted(path.name for path in PLOTS_DIR.glob("*.png")),
        "issue44_context": ISSUE44_CONTEXT,
        "issue103_context": ISSUE103_CONTEXT,
        "issue124_bmix": ISSUE124_BMIX,
        "issue129_isolated_c": ISSUE129_C,
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
        "split": split,
        "verdict": verdict,
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue #130 portfolio analysis")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="portfolio JSON from generate_inputs.py (default: replay published #129 C)",
    )
    args = parser.parse_args()
    analysis = run_analysis(args.input)
    print(analysis["metrics"].to_string())
    print("verdict", analysis["verdict"]["label"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
