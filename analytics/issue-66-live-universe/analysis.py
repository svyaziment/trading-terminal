"""Reproducible live-universe ranking for Issue #66.

Run from the repository root:

    python analytics/issue-66-live-universe/analysis.py
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
PLOTS_DIR = ANALYSIS_DIR / "plots"
TOP_N = 5
MIN_TRADES_FOR_PF_FILTER = 5
MIN_PROFIT_FACTOR = 1.0
MAX_LOT_COST_RUB = 50_000.0
MAX_PER_SECTOR = 2
SECTORS = {
    "GAZP": "oil",
    "LKOH": "oil",
    "SIBN": "oil",
    "TATN": "oil",
    "ROSN": "oil",
    "NVTK": "gas",
    "GMKN": "metals",
    "RUAL": "metals",
    "ALRS": "metals",
    "PLZL": "metals",
    "MTLR": "metals",
    "CHMF": "metals",
    "NLMK": "metals",
    "SBER": "banks",
    "CBOM": "banks",
    "VTBR": "banks",
    "MTSS": "telco",
    "FEES": "power",
    "IRAO": "power",
    "PIKK": "realty",
}
SCORE_WEIGHTS = {
    "universe_pf": 0.25,
    "strategy_pf": 0.18,
    "strategy_exp": 0.08,
    "strategy_n": 0.06,
    "inv_maxdd": 0.10,
    "inv_spread": 0.15,
    "log_turnover": 0.10,
    "log_depth": 0.05,
    "atr_fit": 0.03,
}


def load_inputs(path: Path | None = None) -> dict[str, Any]:
    source = Path(path) if path else DEFAULT_INPUTS
    with source.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if not payload.get("universe"):
        raise ValueError(f"{source}: universe is empty")
    return payload


def _metric(metrics: dict[str, Any] | None, key: str) -> float | None:
    if not isinstance(metrics, dict) or key not in metrics or metrics[key] is None:
        return None
    try:
        value = float(metrics[key])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _percentile_rank(series: pd.Series) -> pd.Series:
    if series.dropna().empty:
        return pd.Series(np.nan, index=series.index)
    return series.rank(method="average", pct=True)


def build_frame(payload: dict[str, Any]) -> pd.DataFrame:
    universe = pd.DataFrame(payload["universe"]).set_index("ticker")
    instruments = pd.DataFrame(payload.get("instruments") or []).set_index("ticker")
    market = pd.DataFrame(payload.get("market") or []).set_index("ticker")
    spreads = pd.DataFrame(payload.get("spreads") or []).set_index("ticker")
    locked_rows = []
    for row in payload.get("locked_backtest") or []:
        metrics = row.get("metrics") or {}
        locked_rows.append(
            {
                "ticker": row["ticker"],
                "strategy_n": _metric(metrics, "n"),
                "strategy_pf": _metric(metrics, "pf"),
                "strategy_wr": _metric(metrics, "wr"),
                "strategy_exp_pct": _metric(metrics, "exp_pct"),
                "strategy_maxdd_pct": _metric(metrics, "maxdd_pct"),
            }
        )
    locked = (
        pd.DataFrame(locked_rows).set_index("ticker")
        if locked_rows
        else pd.DataFrame(
            columns=[
                "strategy_n",
                "strategy_pf",
                "strategy_wr",
                "strategy_exp_pct",
                "strategy_maxdd_pct",
            ]
        )
    )
    frame = universe[["rank", "pf", "source"]].rename(
        columns={"rank": "universe_rank", "pf": "universe_pf"}
    )
    frame["universe_pf"] = pd.to_numeric(frame["universe_pf"], errors="coerce")
    frame = frame.join(instruments[["lot_size"]], how="left")
    frame = frame.join(
        market[
            [
                "last_close",
                "atr_14",
                "atr_pct",
                "avg_volume_60d",
                "avg_turnover_60d",
            ]
        ],
        how="left",
    )
    frame = frame.join(
        spreads[
            [
                "median_abs_spread_pct",
                "avg_depth",
                "n_quotes",
            ]
        ],
        how="left",
    )
    frame = frame.join(locked, how="left")
    frame["lot_cost_rub"] = frame["lot_size"] * frame["last_close"]
    frame["sector"] = [SECTORS.get(ticker, "other") for ticker in frame.index]
    paper = payload.get("paper_positions") or {}
    frame["paper_trades"] = int(paper.get("closed") or 0)
    frame["paper_pnl_rub"] = None
    frame["paper_win_rate"] = None
    issue44 = payload.get("issue44_levels_reversal_ticker_pnl_rub") or {}
    frame["issue44_pnl_rub"] = frame.index.map(lambda ticker: issue44.get(ticker))
    return frame.sort_values("universe_rank")


def apply_filters(frame: pd.DataFrame) -> pd.DataFrame:
    reasons: dict[str, str] = {}
    keep = []
    for ticker, row in frame.iterrows():
        if pd.notna(row["lot_cost_rub"]) and float(row["lot_cost_rub"]) > MAX_LOT_COST_RUB:
            reasons[ticker] = "lot_cost"
            continue
        if pd.isna(row["median_abs_spread_pct"]) or pd.isna(row["avg_depth"]):
            reasons[ticker] = "no_orderbook"
            continue
        n_trades = row["strategy_n"]
        pf = row["strategy_pf"]
        if (
            pd.notna(n_trades)
            and pd.notna(pf)
            and float(n_trades) >= MIN_TRADES_FOR_PF_FILTER
            and float(pf) < MIN_PROFIT_FACTOR
        ):
            reasons[ticker] = "locked_strategy_pf"
            continue
        keep.append(ticker)
    filtered = frame.loc[keep].copy()
    filtered.attrs["exclude_reasons"] = reasons
    return filtered


def _atr_fit(atr_pct: float) -> float:
    # 2-5% daily ATR is the comfortable band for 1% risk / 50k capital.
    if not math.isfinite(atr_pct):
        return 0.0
    if 2.0 <= atr_pct <= 5.0:
        return 1.0
    distance = min(abs(atr_pct - 2.0), abs(atr_pct - 5.0))
    return max(0.0, 1.0 - distance / 5.0)


def score_frame(frame: pd.DataFrame) -> pd.DataFrame:
    scored = frame.copy()
    scored["inv_spread"] = -scored["median_abs_spread_pct"]
    scored["inv_maxdd"] = -scored["strategy_maxdd_pct"]
    scored["log_turnover"] = np.log10(scored["avg_turnover_60d"].clip(lower=1.0))
    scored["log_depth"] = np.log10(scored["avg_depth"].clip(lower=1.0))
    scored["atr_fit"] = scored["atr_pct"].map(
        lambda value: _atr_fit(float(value)) if pd.notna(value) else np.nan
    )
    rank_map = {
        "universe_pf": "universe_pf",
        "strategy_pf": "strategy_pf",
        "strategy_exp": "strategy_exp_pct",
        "strategy_n": "strategy_n",
        "inv_maxdd": "inv_maxdd",
        "inv_spread": "inv_spread",
        "log_turnover": "log_turnover",
        "log_depth": "log_depth",
        "atr_fit": "atr_fit",
    }
    ranks = {
        name: _percentile_rank(scored[column]) for name, column in rank_map.items()
    }
    scores = []
    for ticker in scored.index:
        used = 0.0
        total = 0.0
        for name, weight in SCORE_WEIGHTS.items():
            value = ranks[name].loc[ticker]
            if pd.isna(value):
                continue
            used += weight
            total += weight * float(value)
        scores.append(total / used if used else 0.0)
    scored["score"] = scores
    scored["score_rank"] = scored["score"].rank(ascending=False, method="min")
    return scored.sort_values(["score", "universe_pf"], ascending=False)


def select_top(scored: pd.DataFrame, top_n: int = TOP_N) -> list[str]:
    selected: list[str] = []
    sector_counts: dict[str, int] = {}
    for ticker, row in scored.iterrows():
        sector = str(row["sector"])
        if sector_counts.get(sector, 0) >= MAX_PER_SECTOR:
            continue
        selected.append(str(ticker))
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(selected) == top_n:
            break
    return selected


def _fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    if pd.isna(value):
        return "—"
    return f"{float(value):.{digits}f}{suffix}"


def _save_figure(figure, name: str) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = PLOTS_DIR / name
    figure.tight_layout()
    figure.savefig(path, dpi=140)
    plt.close(figure)


def plot_scores(scored: pd.DataFrame, selected: list[str]) -> None:
    figure, axis = plt.subplots(figsize=(10, 5.5))
    colors = ["#1f7a4c" if ticker in selected else "#8aa0b4" for ticker in scored.index]
    axis.barh(scored.index[::-1], scored["score"].iloc[::-1], color=colors[::-1])
    axis.set_xlabel("Composite score (0-1)")
    axis.set_title("Live-universe composite score")
    _save_figure(figure, "composite_score.png")


def plot_pf_spread(frame: pd.DataFrame, selected: list[str]) -> None:
    figure, axis = plt.subplots(figsize=(8.5, 5.5))
    pf = frame["universe_pf"]
    spread = frame["median_abs_spread_pct"]
    size = np.clip(np.log10(frame["avg_turnover_60d"].clip(lower=1.0)) * 18, 30, 180)
    colors = ["#1f7a4c" if ticker in selected else "#5b6b7a" for ticker in frame.index]
    axis.scatter(spread, pf, s=size, c=colors, alpha=0.85, edgecolors="white")
    for ticker in frame.index:
        axis.annotate(ticker, (spread.loc[ticker], pf.loc[ticker]), fontsize=8)
    axis.set_xlabel("Median |spread|, %")
    axis.set_ylabel("Universe profit factor")
    axis.set_title("PF vs spread (bubble = 60d turnover)")
    _save_figure(figure, "pf_vs_spread.png")


def plot_atr_turnover(frame: pd.DataFrame, selected: list[str]) -> None:
    figure, axis = plt.subplots(figsize=(8.5, 5.5))
    colors = ["#1f7a4c" if ticker in selected else "#5b6b7a" for ticker in frame.index]
    axis.scatter(
        frame["avg_turnover_60d"] / 1e6,
        frame["atr_pct"],
        c=colors,
        s=70,
        alpha=0.85,
        edgecolors="white",
    )
    for ticker in frame.index:
        axis.annotate(
            ticker,
            (frame.loc[ticker, "avg_turnover_60d"] / 1e6, frame.loc[ticker, "atr_pct"]),
            fontsize=8,
        )
    axis.set_xlabel("Average 60d turnover, mln RUB")
    axis.set_ylabel("Daily ATR, % of close")
    axis.set_title("Volatility vs liquidity")
    axis.set_xscale("log")
    _save_figure(figure, "atr_vs_turnover.png")


def plot_paper_gap(payload: dict[str, Any]) -> None:
    equity = payload.get("paper_equity") or {}
    positions = payload.get("paper_positions") or {}
    figure, axis = plt.subplots(figsize=(8.5, 3.2))
    axis.axis("off")
    text = (
        f"paper_positions: {int(positions.get('rows') or 0)} rows, "
        f"{int(positions.get('closed') or 0)} closed\n"
        f"paper_equity: {int(equity.get('rows') or 0)} rows, "
        f"equity {equity.get('min_equity_rub')}–{equity.get('max_equity_rub')} RUB, "
        f"max DD {equity.get('max_drawdown_pct')}%\n"
        f"window: {equity.get('min_ts')} → {equity.get('max_ts')}\n"
        "Paper sample is empty, so ranking uses backtest + liquidity + ATR."
    )
    axis.text(0.02, 0.5, text, va="center", fontsize=11, family="monospace")
    axis.set_title("Paper trading coverage")
    _save_figure(figure, "paper_coverage.png")


def render_report(
    payload: dict[str, Any],
    full: pd.DataFrame,
    scored: pd.DataFrame,
    selected: list[str],
) -> str:
    strategy = payload.get("active_strategy") or {}
    paper = payload.get("paper_positions") or {}
    equity = payload.get("paper_equity") or {}
    reasons = scored.attrs.get("exclude_reasons") or full.attrs.get("exclude_reasons") or {}
    excluded_lines = [
        f"- **{ticker}:** {reason}" for ticker, reason in sorted(reasons.items())
    ] or ["- нет"]
    selected_rows = []
    for index, ticker in enumerate(selected, 1):
        row = scored.loc[ticker]
        selected_rows.append(
            f"| {index} | {ticker} | {row['sector']} | {_fmt(row['score'], 3)} | "
            f"{_fmt(row['universe_pf'], 3)} | {_fmt(row['strategy_pf'], 2)} | "
            f"{_fmt(row['strategy_n'], 0)} | {_fmt(row['strategy_wr'], 1, '%')} | "
            f"{_fmt(row['strategy_maxdd_pct'], 1, '%')} | "
            f"{_fmt(row['median_abs_spread_pct'], 3, '%')} | "
            f"{_fmt(row['atr_pct'], 2, '%')} |"
        )
    table_rows = []
    for ticker, row in scored.iterrows():
        mark = "yes" if ticker in selected else ""
        table_rows.append(
            f"| {ticker} | {row['sector']} | {int(row['universe_rank'])} | "
            f"{_fmt(row['universe_pf'], 3)} | {_fmt(row['strategy_pf'], 2)} | "
            f"{_fmt(row['strategy_n'], 0)} | {_fmt(row['strategy_wr'], 1)} | "
            f"{_fmt(row['strategy_exp_pct'], 3)} | {_fmt(row['strategy_maxdd_pct'], 1)} | "
            f"{_fmt(row['median_abs_spread_pct'], 3)} | {_fmt(row['avg_turnover_60d'] / 1e6, 1)} | "
            f"{_fmt(row['atr_pct'], 2)} | {_fmt(row['score'], 3)} | {mark} |"
        )
    justifications = []
    for ticker in selected:
        row = scored.loc[ticker]
        bits = [
            f"universe PF {_fmt(row['universe_pf'], 3)} (rank {int(row['universe_rank'])})",
            f"median spread {_fmt(row['median_abs_spread_pct'], 3)}%",
            f"60d turnover {_fmt(row['avg_turnover_60d'] / 1e6, 1)} mln RUB",
            f"ATR {_fmt(row['atr_pct'], 2)}%",
        ]
        if pd.notna(row["strategy_pf"]):
            bits.insert(
                1,
                (
                    f"locked-strategy PF {_fmt(row['strategy_pf'], 2)}, "
                    f"n={_fmt(row['strategy_n'], 0)}, "
                    f"WR {_fmt(row['strategy_wr'], 1)}%, "
                    f"maxDD {_fmt(row['strategy_maxdd_pct'], 1)}%"
                ),
            )
        else:
            bits.insert(1, "нет полного прогона locked-стратегии за июль 2026")
        if pd.notna(row["issue44_pnl_rub"]):
            bits.append(f"issue #44 portfolio PnL {_fmt(row['issue44_pnl_rub'], 0)} RUB")
        justifications.append(f"- **{ticker}** ({row['sector']}): " + "; ".join(bits) + ".")

    run_params = strategy.get("run_params") or {}
    return f"""# Issue #66: топ-5 тикеров для live trading

## Резюме

Для sandbox live trading рекомендуется вселенная **{', '.join(selected)}**.
Paper trading не дал закрытых сделок, поэтому выбор опирается на бэктест locked-стратегии
`{strategy.get('name')}`, исторический PF вселенной и рыночную ликвидность/волатильность.
Это не доказательство будущей доходности.

## Данные и ограничения

- Снимок: `{payload.get('extracted_at')}`.
- Кандидаты: 15 тикеров из `trading.trading_universe`. Live-фильтр imbalance требует свежий стакан;
  котировки `online_orderbook_aggregates` есть только по этой вселенной.
- Paper trading: `{int(paper.get('rows') or 0)}` строк в `paper_positions`
  (closed={int(paper.get('closed') or 0)}, open={int(paper.get('open') or 0)}).
- Paper equity: `{int(equity.get('rows') or 0)}` строк, equity
  `{equity.get('min_equity_rub')}–{equity.get('max_equity_rub')}` RUB,
  max DD `{equity.get('max_drawdown_pct')}%`,
  окно `{equity.get('min_ts')} → {equity.get('max_ts')}`.
- Locked-стратегия: `{strategy.get('name')}` (id={strategy.get('id')}),
  patterns `{strategy.get('patterns')}`, confirm `{strategy.get('confirm_windows')}`,
  RR `{strategy.get('risk_reward')}`, commission `{strategy.get('commission_pct')}%`.
- Full-sample locked backtest: `{run_params.get('date_from')}` — `{run_params.get('date_to')}`,
  depth `{run_params.get('depth')}`. Выборка короткая (один месяц), поэтому PF/WR используются
  как фильтр и как один из факторов, а не как единственный критерий.
- Walk-forward для locked-стратегии в `backtest_results` отсутствует.

![Paper coverage](plots/paper_coverage.png)

## Методика

1. Кандидатный набор — текущий top-15 `trading_universe` (источник: levels_reversal matrix PF).
2. Жёсткие исключения: нет стакана; 1 лот дороже {MAX_LOT_COST_RUB:,.0f} RUB;
   locked-strategy PF < {MIN_PROFIT_FACTOR} при n ≥ {MIN_TRADES_FOR_PF_FILTER}.
3. Композитный score — взвешенные percentile ranks:
   universe PF 25%, locked PF 18%, expectancy 8%, n сделок 6%,
   обратный maxDD 10%, обратный спред 15%, log оборота 10%, log глубины стакана 5%,
   ATR-fit 3%. Пропуски не штрафуют: вес перераспределяется на доступные факторы.
4. Диверсификация: не больше {MAX_PER_SECTOR} тикеров из одного сектора.
5. Paper PnL/win-rate/drawdown по тикерам недоступны (пустая `paper_positions`);
   портфельный drawdown paper equity равен 0.

![Composite score](plots/composite_score.png)

## Финальный список

| # | Тикер | Сектор | Score | Universe PF | Strategy PF | n | WR | MaxDD | Spread | ATR |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(selected_rows)}

![PF vs spread](plots/pf_vs_spread.png)

![ATR vs turnover](plots/atr_vs_turnover.png)

### Обоснование

{chr(10).join(justifications)}

## Исключённые тикеры

{chr(10).join(excluded_lines)}

## Полная таблица кандидатов после фильтра

| Тикер | Сектор | Rank | Univ PF | Strat PF | n | WR | Exp % | MaxDD % | Spread % | Turnover mln | ATR % | Score | Top-5 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
{chr(10).join(table_rows)}

## Что передать Backend

Зафиксировать live-вселенную в `trading_config.py` как `LIVE_UNIVERSE` и читать её через
`get_live_trading_universe()`. Paper trading и `data_refresher` оставляют полный top-15:
сужение `trading.trading_universe` до 5 имён отключит стриминг и paper по остальным тикерам.

Опциональная аннотация в БД:

```sql
UPDATE trading.trading_universe
SET notes = concat(coalesce(notes, ''), ' | live_top5 #66'),
    updated_at = NOW()
WHERE ticker IN ({", ".join(repr(ticker) for ticker in selected)});
```

## Рекомендации после запуска

1. Накопить ≥30 закрытых paper/live сделок по выбранным тикерам и пересмотреть список.
2. Не расширять live-вселенную, пока нет свежего стакана по тикеру.
3. Повторить walk-forward locked-стратегии на окне шире июля 2026.

## Воспроизводимость

- Входы: `inputs.json` (срез БД без секретов).
- Код: `extract_inputs.py`, `analysis.py`.
"""


def run_analysis(input_path: Path | None = None) -> dict[str, Any]:
    payload = load_inputs(input_path)
    full = build_frame(payload)
    filtered = apply_filters(full)
    exclude_reasons = filtered.attrs.get("exclude_reasons", {})
    scored = score_frame(filtered)
    scored.attrs["exclude_reasons"] = exclude_reasons
    selected = select_top(scored)
    plot_paper_gap(payload)
    plot_scores(scored, selected)
    plot_pf_spread(full, selected)
    plot_atr_turnover(full, selected)
    report = render_report(payload, full, scored, selected)
    summary = {
        "extracted_at": payload.get("extracted_at"),
        "selected": selected,
        "excluded": exclude_reasons,
        "paper_positions": payload.get("paper_positions"),
        "paper_equity": payload.get("paper_equity"),
        "active_strategy": {
            "id": (payload.get("active_strategy") or {}).get("id"),
            "name": (payload.get("active_strategy") or {}).get("name"),
        },
        "scores": [
            {
                "ticker": ticker,
                "sector": row["sector"],
                "score": round(float(row["score"]), 4),
                "universe_pf": None if pd.isna(row["universe_pf"]) else float(row["universe_pf"]),
                "strategy_pf": None if pd.isna(row["strategy_pf"]) else float(row["strategy_pf"]),
                "strategy_n": None if pd.isna(row["strategy_n"]) else float(row["strategy_n"]),
                "median_abs_spread_pct": None
                if pd.isna(row["median_abs_spread_pct"])
                else float(row["median_abs_spread_pct"]),
                "atr_pct": None if pd.isna(row["atr_pct"]) else float(row["atr_pct"]),
                "selected": ticker in selected,
            }
            for ticker, row in scored.iterrows()
        ],
    }
    (ANALYSIS_DIR / "report.md").write_text(report, encoding="utf-8")
    (ANALYSIS_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"selected": selected, "scored": scored, "summary": summary, "report": report}


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank live-trading top-5 tickers")
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    args = parser.parse_args()
    result = run_analysis(args.inputs)
    print("selected", " ".join(result["selected"]))


if __name__ == "__main__":
    main()
