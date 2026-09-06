"""Issue #139 portfolio A/B replay + comparison — runs WITHOUT a database.

Reads `results.json` produced by `extract_inputs.py` (one record per trade with
BOTH the baseline exit `A` and the stepped-trailing exit `B` computed on the same
entry/path), replays each exit mode through the shared-capital slot rules of the
production simulator (50k / 10k / max 5 / volume priority / GAME OVER), and emits
`summary.json`, `report.md` (EN+RU) and `plots/`.

The ONLY difference between A and B is the exit rule; entries, initial 1R risk,
commission and universe are identical (single-difference rule from the issue).

Run from the repository root (no DB needed):

    python analytics/issue-139-trailing-stop-new-level/analysis.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ANALYSIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANALYSIS_DIR.parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
PLOTS_DIR = ANALYSIS_DIR / "plots"
RESULTS_PATH = ANALYSIS_DIR / "results.json"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

ISSUE = 139
DISPLAY_NAME = "test_20260830_new_level"
INITIAL_CAPITAL = 50_000.0
SLOT_SIZE = 10_000.0
MAX_POSITIONS = 5


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_results(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        result = json.load(stream)
    if result.get("status") != "success":
        raise ValueError(f"{path}: expected status=success, got {result.get('status')!r}")
    if result.get("strategy_config_name") != DISPLAY_NAME:
        raise ValueError(f"{path}: expected strategy_config_name={DISPLAY_NAME}")
    if int(result.get("issue") or 0) != ISSUE:
        raise ValueError(f"{path}: expected issue {ISSUE}")
    if float(result.get("initial_capital_rub")) != INITIAL_CAPITAL:
        raise ValueError(f"{path}: expected 50k initial capital")
    if float(result.get("slot_size_rub")) != SLOT_SIZE:
        raise ValueError(f"{path}: expected 10k slot")
    if int(result.get("max_positions")) != MAX_POSITIONS:
        raise ValueError(f"{path}: expected max 5 positions")
    if int(result.get("candidate_trades") or 0) <= 0:
        raise ValueError(f"{path}: no candidate trades")
    if result.get("failed_tickers"):
        raise ValueError(f"{path}: extract had failed tickers: {result['failed_tickers']}")
    return result


def build_candidates(trades: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    """Mode 'A' = baseline exit, 'B' = trailing exit. Identical entry/risk."""
    key = "A" if mode == "A" else "B"
    out = []
    for tr in trades:
        exit_block = tr[key]
        out.append({
            "ticker": tr["ticker"],
            "entry_ts": tr["entry_ts"],
            "exit_ts": exit_block["exit_ts"],
            "entry_price": tr["entry_price"],
            "exit_price": exit_block["exit_price"],
            "exit_reason": exit_block["exit_reason"],
            "net_return_pct": float(exit_block["net_return_pct"]),
            "source": tr.get("source"),
        })
    return out


def replay_slots(candidates: list[dict[str, Any]], volume_order: list[str]) -> dict[str, Any]:
    from app.analytics.portfolio_simulator import _portfolio_metrics, _replay_portfolio_trades
    volume_rank = {t: i for i, t in enumerate(volume_order)}
    trades, equity_curve, game_over, game_over_ts, skipped = _replay_portfolio_trades(
        candidates, volume_rank,
        initial_capital=INITIAL_CAPITAL, slot_size=SLOT_SIZE, max_positions=MAX_POSITIONS,
    )
    metrics = _portfolio_metrics(trades, equity_curve, INITIAL_CAPITAL)
    pf = metrics.get("profit_factor")
    if isinstance(pf, float) and not np.isfinite(pf):
        metrics["profit_factor"] = None
        metrics["pf_infinite"] = True
    return {
        "trades": trades, "equity_curve": equity_curve,
        "game_over": game_over, "game_over_ts": game_over_ts,
        "skipped_entries_no_slot": skipped, "metrics": metrics,
    }


def daily_equity(result: dict[str, Any]) -> pd.Series:
    trades = result["trades"]
    if not trades:
        return pd.Series([INITIAL_CAPITAL], index=pd.to_datetime(["2024-08-01"]))
    frame = pd.DataFrame(trades)
    frame["exit_ts"] = pd.to_datetime(frame["exit_ts"], errors="coerce")
    frame["pnl_rub"] = pd.to_numeric(frame["pnl_rub"], errors="coerce")
    frame = frame.dropna(subset=["exit_ts", "pnl_rub"]).sort_values("exit_ts")
    by_day = frame.groupby(frame["exit_ts"].dt.normalize())["pnl_rub"].sum()
    eq = INITIAL_CAPITAL + by_day.cumsum()
    start = pd.Series([INITIAL_CAPITAL], index=pd.DatetimeIndex([by_day.index[0]]))
    eq = pd.concat([start, eq]).groupby(level=0).last().sort_index()
    eq.index.name = "date"
    return eq


def max_drawdown_daily(equity: pd.Series) -> dict[str, Any]:
    if equity.empty:
        return {"max_drawdown_pct": 0.0}
    peak = equity.cummax()
    dd = (peak - equity) / peak * 100.0
    trough = dd.idxmax()
    peak_date = equity.loc[:trough].idxmax()
    return {
        "max_drawdown_pct": round(float(dd.max()), 2),
        "max_drawdown_peak_date": str(pd.Timestamp(peak_date).date()),
        "max_drawdown_trough_date": str(pd.Timestamp(trough).date()),
        "max_drawdown_peak_equity_rub": round(float(equity.loc[peak_date]), 2),
        "max_drawdown_trough_equity_rub": round(float(equity.loc[trough]), 2),
        "max_drawdown_rub": round(float(equity.loc[peak_date] - equity.loc[trough]), 2),
    }


def exit_type_counts(trades: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tr in trades:
        reason = str(tr.get("exit_reason"))
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def book_metrics(result: dict[str, Any], equity: pd.Series) -> dict[str, Any]:
    trades = result["trades"]
    m = dict(result["metrics"])
    m["avg_trade_pnl_rub"] = round(float(np.mean([t["pnl_rub"] for t in trades])), 2) if trades else None
    m.update(max_drawdown_daily(equity))
    m["event_max_drawdown_pct"] = result["metrics"].get("max_drawdown_pct")
    m["game_over"] = bool(result["game_over"])
    m["game_over_ts"] = result["game_over_ts"]
    m["skipped_entries_no_slot"] = result["skipped_entries_no_slot"]
    m["final_equity_rub"] = round(float(equity.iloc[-1]), 2)
    m["pnl_rub"] = round(float(equity.iloc[-1] - INITIAL_CAPITAL), 2)
    m["pnl_pct"] = round(float((equity.iloc[-1] / INITIAL_CAPITAL - 1) * 100), 2)
    m["exit_type_counts"] = exit_type_counts(trades)
    m["n_trades"] = int(len(trades))
    return m



def _save_figure(figure, filename: str) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(PLOTS_DIR / filename, dpi=150, bbox_inches="tight")
    plt.close(figure)


def plot_equity_ab(equity_a: pd.Series, equity_b: pd.Series) -> None:
    figure, axis = plt.subplots(figsize=(12, 6))
    axis.plot(equity_a.index, equity_a.values, label="A baseline (fixed stop/take)", linewidth=1.8, color="#1f77b4")
    axis.plot(equity_b.index, equity_b.values, label="B stepped trailing", linewidth=1.8, color="#d62728")
    axis.axhline(INITIAL_CAPITAL, color="gray", linestyle="--", linewidth=1, label="Start 50,000 RUB")
    axis.set_title("Daily realized equity — baseline vs stepped trailing (Issue #139)")
    axis.set_xlabel("Date"); axis.set_ylabel("Equity (RUB)"); axis.grid(alpha=0.25); axis.legend()
    _save_figure(figure, "equity_ab.png")


def plot_exit_distribution(counts_a: dict[str, int], counts_b: dict[str, int]) -> None:
    labels = ["stop", "initial_stop", "trailing", "take", "open"]
    a_vals = [int(counts_a.get(k, 0)) for k in labels]
    b_vals = [int(counts_b.get(k, 0)) for k in labels]
    x = np.arange(len(labels)); w = 0.38
    figure, axis = plt.subplots(figsize=(10, 5.5))
    axis.bar(x - w / 2, a_vals, w, label="A baseline", color="#1f77b4")
    axis.bar(x + w / 2, b_vals, w, label="B trailing", color="#d62728")
    axis.set_xticks(x, labels)
    for xi, (a, b) in enumerate(zip(a_vals, b_vals)):
        axis.text(xi - w / 2, a, str(a), ha="center", va="bottom", fontsize=8)
        axis.text(xi + w / 2, b, str(b), ha="center", va="bottom", fontsize=8)
    axis.set_title("Closed trades by exit type (A vs B)")
    axis.set_ylabel("Number of trades"); axis.grid(alpha=0.2, axis="y"); axis.legend()
    _save_figure(figure, "exit_type_distribution.png")


def build_verdict(ma: dict[str, Any], mb: dict[str, Any],
                  steps: list[dict[str, float]]) -> dict[str, Any]:
    ea = float(ma["final_equity_rub"]); eb = float(mb["final_equity_rub"])
    delta_rub = round(eb - ea, 2)
    delta_pct = round((eb / ea - 1) * 100, 2) if ea else None
    improves = eb > ea
    n_b = int(mb["n_trades"]) or 1
    trailing_n = int(mb["exit_type_counts"].get("trailing", 0))
    take_n = int(mb["exit_type_counts"].get("take", 0))
    stop_n = int(mb["exit_type_counts"].get("initial_stop", 0)) + int(mb["exit_type_counts"].get("stop", 0))
    share_trailing = round(trailing_n / n_b * 100, 1)
    share_take = round(take_n / n_b * 100, 1)
    share_stop = round(stop_n / n_b * 100, 1)
    # Recommendation heuristic: adopt only if it clearly raises capital without
    # turning profitable books into a GAME OVER.
    if improves and not mb["game_over"] and delta_pct is not None and delta_pct >= 2.0:
        action = "внедрять (consider adopting)"
    elif improves:
        action = "доработать (refine grid; gain is marginal)"
    else:
        action = "отказаться (reject: trailing lowers capital)"
    return {
        "steps": steps,
        "final_equity_a_rub": ea,
        "final_equity_b_rub": eb,
        "delta_rub": delta_rub,
        "delta_pct": delta_pct,
        "trailing_improves_capital": bool(improves),
        "game_over_a": bool(ma["game_over"]),
        "game_over_b": bool(mb["game_over"]),
        "b_exit_share_trailing_pct": share_trailing,
        "b_exit_share_take_pct": share_take,
        "b_exit_share_initial_stop_pct": share_stop,
        "recommendation": action,
    }



def build_report(result, digest, ma, mb, verdict) -> str:
    def row(m):
        pf = m.get("profit_factor")
        return (
            f"| {m['final_equity_rub']:,.2f} | {m['pnl_rub']:+,.2f} | {m['pnl_pct']:+.2f}% "
            f"| {m['n_trades']} | {m.get('win_rate', m.get('win_rate_pct'))}% | {pf} "
            f"| {m['max_drawdown_pct']}% | {'yes' if m['game_over'] else 'no'} "
            f"| {m.get('avg_trade_pnl_rub')} |"
        )
    ea = ma["exit_type_counts"]; eb = mb["exit_type_counts"]
    steps_txt = ", ".join(f"+{s['trigger']}R->+{s['stop']}R" for s in verdict["steps"])
    cfg = result["config"]
    support = (cfg.get("patterns") or {}).get("levels_sr_support") or {}
    return f"""# Issue #139 — stepped trailing stop on `test_20260830_new_level` (50k simulator)

A/B in the portfolio simulator: 50,000 RUB, slot 10,000, max 5 positions,
volume priority, GAME OVER at cash <= 0. **The only difference between A and B is
the exit rule.** Entry, initial 1R risk, commission and universe are identical.

This is an historical backtest; it does not prove future performance.

## Конфигурация (id=126, locked, точно из `config`)

- Паттерны: `signal_4h_buy` + `levels_sr_support`.
- Уровни: `level_method={support.get('level_method')}`, `swing_window={support.get('swing_window')}`,
  `zone_atr_mult={support.get('zone_atr_mult')}`, `level_timeframe={support.get('level_timeframe')}`,
  `impulse_atr_mult={support.get('impulse_atr_mult')}`, `impulse_body_ratio={support.get('impulse_body_ratio')}`.
- RR: 1:{(cfg.get('risk_reward') or {}).get('reward')}; commission {cfg.get('commission_pct')}%; slippage {cfg.get('slippage_pct')}.
- Период: `{result['date_from']}` … `timestamp < {result['date_to']}` (последний день `{result.get('period_last_day')}`) — полный, не экспресс.
- Вселенная: {len(result['universe'])} тикеров из `run_params.tickers`.
- Ступени трейлинга (в R от входа, конфигурируемы в `trailing.py`): **{steps_txt}**. Тейк не меняется; трейлинг только поджимает стоп.
- SHA-256 конфига id=126: `{result['config_sha256']}`.
- SHA-256 `results.json`: `{digest}`.
- Защитные строки (126 / 36 / 102 / 118) не менялись.

## Metrics A/B

| Book | Equity, RUB | PnL, RUB | PnL, % | Trades | Win rate | PF | Max DD (daily) | GAME OVER | Avg PnL/trade |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|---:|
| **A baseline** {row(ma)}
| **B trailing** {row(mb)}

- Кандидатов (входов): `{result['candidate_trades']}`; дошли до +2R: `{result.get('reached_2r_trades')}`
  (только у них трейлинг может изменить исход; у остальных B = A).
- Skipped no-slot: A `{ma['skipped_entries_no_slot']}`, B `{mb['skipped_entries_no_slot']}`.
- Event-based Max DD: A `{ma['event_max_drawdown_pct']}%`, B `{mb['event_max_drawdown_pct']}%`.

## Закрытия по типу выхода / Closes by exit type

| Тип / Type | A | B |
|---|---:|---:|
| initial stop | {ea.get('stop', 0)} | {eb.get('initial_stop', 0) + eb.get('stop', 0)} |
| trailing stop | 0 | {eb.get('trailing', 0)} |
| take | {ea.get('take', 0)} | {eb.get('take', 0)} |

Доля закрытий по трейлинг-стопу в B / trailing share in B: **{verdict['b_exit_share_trailing_pct']}%**
(тейк/take {verdict['b_exit_share_take_pct']}%, начальный стоп/initial stop {verdict['b_exit_share_initial_stop_pct']}%).

## Вердикт / Verdict

- Итоговый капитал / final equity: A {verdict['final_equity_a_rub']:,.2f} RUB -> B {verdict['final_equity_b_rub']:,.2f} RUB
  (delta **{verdict['delta_rub']:+,.2f} RUB**, {verdict['delta_pct']:+.2f}%).
- Трейлинг улучшает капитал / trailing improves capital: **{'да / YES' if verdict['trailing_improves_capital'] else 'нет / NO'}**.
- GAME OVER: A {'no' if not verdict['game_over_a'] else 'yes'}, B {'no' if not verdict['game_over_b'] else 'yes'}.
- Рекомендация / recommendation: **{verdict['recommendation']}**.

![Equity A/B](plots/equity_ab.png)

![Exit types](plots/exit_type_distribution.png)
"""



def run_analysis(results_path: Path) -> dict[str, Any]:
    result = load_results(results_path)
    digest = _sha256(results_path)
    trades = result["trades"]
    volume_order = result["volume_order"]
    steps = result.get("trailing_steps") or []

    cand_a = build_candidates(trades, "A")
    cand_b = build_candidates(trades, "B")
    rep_a = replay_slots(cand_a, volume_order)
    rep_b = replay_slots(cand_b, volume_order)
    eq_a = daily_equity(rep_a)
    eq_b = daily_equity(rep_b)
    ma = book_metrics(rep_a, eq_a)
    mb = book_metrics(rep_b, eq_b)
    verdict = build_verdict(ma, mb, steps)

    plot_equity_ab(eq_a, eq_b)
    plot_exit_distribution(ma["exit_type_counts"], mb["exit_type_counts"])
    (ANALYSIS_DIR / "report.md").write_text(
        build_report(result, digest, ma, mb, verdict), encoding="utf-8")

    summary = {
        "issue": ISSUE,
        "strategy_config_name": DISPLAY_NAME,
        "strategy_id": result.get("strategy_id"),
        "config_sha256": result["config_sha256"],
        "input_sha256": digest,
        "date_from": result["date_from"],
        "date_to": result["date_to"],
        "period_last_day": result.get("period_last_day"),
        "universe": result["universe"],
        "volume_order": volume_order,
        "initial_capital_rub": INITIAL_CAPITAL,
        "slot_size_rub": SLOT_SIZE,
        "max_positions": MAX_POSITIONS,
        "candidate_trades": int(result["candidate_trades"]),
        "reached_2r_trades": int(result.get("reached_2r_trades") or 0),
        "baseline_replay_mismatches": int(result.get("baseline_replay_mismatches") or 0),
        "protected_untouched": bool(result.get("protected_untouched", True)),
        "book_A_baseline": ma,
        "book_B_trailing": mb,
        "verdict": verdict,
        "plot_files": sorted(p.name for p in PLOTS_DIR.glob("*.png")),
    }
    (ANALYSIS_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue #139 A/B portfolio replay (no DB)")
    parser.add_argument("--results", type=Path, default=RESULTS_PATH)
    args = parser.parse_args()
    summary = run_analysis(args.results)
    v = summary["verdict"]
    print(f"A={v['final_equity_a_rub']:,.2f} RUB  B={v['final_equity_b_rub']:,.2f} RUB  "
          f"delta={v['delta_rub']:+,.2f} ({v['delta_pct']:+.2f}%)  "
          f"improves={v['trailing_improves_capital']}  rec={v['recommendation']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

