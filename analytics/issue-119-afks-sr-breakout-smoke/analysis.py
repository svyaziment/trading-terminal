"""Reproducible Issue #119 AFKS smoke: levels_sr_breakout vs #44/#103.

Run from the repository root after extract_inputs.py:

    python analytics/issue-119-afks-sr-breakout-smoke/analysis.py
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
import pandas as pd


ANALYSIS_DIR = Path(__file__).resolve().parent
DEFAULT_INPUTS = ANALYSIS_DIR / "inputs.json"
DEFAULT_RESULTS = ANALYSIS_DIR / "results.json"
PLOTS_DIR = ANALYSIS_DIR / "plots"

SOURCE_SUPPORT = "levels_sr_breakout_support"
SOURCE_RESISTANCE = "levels_sr_breakout_resistance"

BOOK_44_AFKS = {
    "issue": 44,
    "kind": "portfolio_replay",
    "strategy_name": "test_20260731",
    "resistance_veto": False,
    "date_to": "2026-08-15",
    "n_trades": 95,
    "pnl_rub": 2002.06,
    "note": (
        "Published portfolio book (50k / 10k slots), not an isolated ticker "
        "backtest. Period exclusive date_to=2026-08-15, engine before veto #97."
    ),
}
BOOK_103_CONTEXT = {
    "issue": 103,
    "kind": "portfolio_replay",
    "strategy_name": "test_20260821",
    "resistance_veto": True,
    "date_from": "2024-08-01",
    "date_to": "2026-08-21",
    "portfolio_n_trades": 2070,
    "portfolio_pf": 1.34,
    "published_afks_isolated": False,
    "note": (
        "Published #103 package is a 28-ticker portfolio replay. It does not "
        "publish an isolated AFKS n/PF. Isolated run A in this smoke is the "
        "#103-equivalent on one ticker."
    ),
}
BOOK_100_AFKS_LAB = {
    "issue": 100,
    "kind": "lab_full_sample",
    "strategy_name": "test_20260820",
    "level_method": ["swing"],
    "date_from": "2024-08-21",
    "n": 45,
    "pf": 1.07,
    "exp_pct": 0.053,
    "wr": 28.9,
    "maxdd_pct": 9.7,
    "note": "Swing-only Lab full-sample after veto #97; different geometry and start date.",
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


def pick_path_b_examples(frame: pd.DataFrame, limit: int = 2) -> list[dict[str, Any]]:
    rows = frame[frame["source"] == SOURCE_RESISTANCE].sort_values("entry_ts")
    examples = []
    for row in rows.head(limit).itertuples():
        examples.append(
            {
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


def plugin_parity(run_b: dict[str, Any], run_plugin: dict[str, Any] | None) -> dict[str, Any]:
    if not run_plugin:
        return {"ran": False}
    n_b = int(run_b.get("n") or 0)
    n_p = int(run_plugin.get("n") or 0)
    pf_b = finite_pf(run_b.get("metrics"))
    pf_p = finite_pf(run_plugin.get("metrics"))
    return {
        "ran": True,
        "status": run_plugin.get("status"),
        "n_match": n_b == n_p,
        "n_b": n_b,
        "n_plugin": n_p,
        "pf_b": pf_b,
        "pf_plugin": pf_p,
        "source_on_plugin_trades": any(
            trade.get("source") for trade in (run_plugin.get("trades") or [])
        ),
    }


def product_verdict(
    metrics_a: dict[str, Any],
    metrics_b: dict[str, Any],
    split: dict[str, Any],
    extra: dict[str, int],
    plugin: dict[str, Any],
) -> dict[str, Any]:
    pf_a = finite_pf(metrics_a)
    pf_b = finite_pf(metrics_b)
    n_res = int(split["resistance"]["n"])
    n_sup = int(split["support"]["n"])
    added = int(extra["added"])
    reasons: list[str] = []
    expand = False
    tune_retest = False

    if metrics_b.get("n", 0) == 0:
        reasons.append("Кандидат B дал 0 сделок — сначала проверить HTF/трекер, не крутить вселенную.")
        return {
            "expand_universe": False,
            "tune_retest": False,
            "paper": False,
            "label": "нет",
            "reasons": reasons,
        }

    if plugin.get("ran") and not plugin.get("n_match"):
        reasons.append(
            f"Plugin-путь Lab дал n={plugin.get('n_plugin')} против "
            f"run_strategy_backtest n={plugin.get('n_b')} — HTF ещё сверять."
        )

    if n_res == 0:
        tune_retest = True
        reasons.append(
            "Путь B (ретест сопротивления) не дал сделок на AFKS при дефолтах ретеста. "
            "Имеет смысл крутить retest_window_bars / retest_zone_atr, а не сразу 28 тикеров."
        )
    else:
        pf_res = finite_pf(split["resistance"]["metrics"])
        reasons.append(
            f"Путь B добавил {n_res} сделок с source={SOURCE_RESISTANCE} "
            f"(PF пути B {_fmt_pf(pf_res, bool(split['resistance']['metrics'].get('pf_infinite')))})."
        )
        if pf_res is not None and pf_res < 1.0:
            tune_retest = True
            reasons.append("PF книги B-only < 1 — сначала сетка ретеста, не paper.")
        else:
            expand = True

    extra_support = n_sup - int(extra["n_a"])
    if extra_support:
        reasons.append(
            f"Support-путь B дал {n_sup} сделок против {extra['n_a']} у A "
            f"({extra_support:+d}). Композит передаёт LevelsTracker в вето: "
            f"пробитое сопротивление больше не режет support-вход."
        )
    if added > 0:
        reasons.append(f"Смесь B дала на {added} сделок больше, чем база A ({extra['n_a']} → {extra['n_b']}).")
        if pf_b is not None and pf_a is not None and pf_b + 1e-9 >= pf_a:
            expand = True
            reasons.append(
                f"PF смеси B ({_fmt_pf(pf_b)}) не хуже базы A ({_fmt_pf(pf_a)}) — "
                "есть смысл расширить вселенную изолированным композитом."
            )
        elif pf_b is not None and pf_a is not None and pf_b < pf_a:
            reasons.append(
                f"PF смеси B ({_fmt_pf(pf_b)}) ниже базы A ({_fmt_pf(pf_a)}). "
                "Расширять вселенную только если путь B отдельно устойчив."
            )
    elif added == 0:
        reasons.append("Число сделок B совпало с A — композит на AFKS не добавил входов.")
        if n_res == 0:
            tune_retest = True
    else:
        reasons.append(
            f"Смесь B дала меньше сделок, чем A ({extra['n_a']} → {extra['n_b']}). "
            "Путь B мог перехватить бары пути A (OR, path B wins)."
        )

    reasons.append(f"Путь A внутри B: {n_sup} сделок (`{SOURCE_SUPPORT}`).")
    reasons.append("Это smoke одного тикера, не портфель 50k и не вердикт катить в paper.")

    if expand and not tune_retest:
        label = "расширять вселенную"
    elif tune_retest and expand:
        label = "крутить параметры ретеста, затем вселенную"
    elif tune_retest:
        label = "крутить параметры ретеста"
    else:
        label = "нет"

    return {
        "expand_universe": expand and not tune_retest,
        "tune_retest": tune_retest,
        "paper": False,
        "label": label,
        "reasons": reasons,
    }


def plot_metrics(rows: list[dict[str, Any]]) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    labels = [row["label"] for row in rows]
    ns = [row["n"] or 0 for row in rows]
    pfs = [0.0 if row["pf"] is None else float(row["pf"]) for row in rows]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].bar(labels, ns, color=["#4c72b0", "#55a868", "#c44e52", "#8172b3"][: len(labels)])
    axes[0].set_title("AFKS: число сделок")
    axes[0].set_ylabel("n")
    axes[1].bar(labels, pfs, color=["#4c72b0", "#55a868", "#c44e52", "#8172b3"][: len(labels)])
    axes[1].axhline(1.0, color="black", linewidth=0.8, linestyle="--")
    axes[1].set_title("AFKS: profit factor")
    axes[1].set_ylabel("PF")
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "metrics_comparison.png", dpi=120)
    plt.close(fig)


def plot_equity(frame_a: pd.DataFrame, frame_b: pd.DataFrame) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 4))
    if not frame_a.empty:
        eq_a = frame_a.sort_values("exit_ts")["net_return_pct"].cumsum()
        ax.plot(frame_a.sort_values("exit_ts")["exit_ts"], eq_a, label="A levels_reversal")
    if not frame_b.empty:
        eq_b = frame_b.sort_values("exit_ts")["net_return_pct"].cumsum()
        ax.plot(frame_b.sort_values("exit_ts")["exit_ts"], eq_b, label="B levels_sr_breakout")
    ax.set_title("AFKS: накопленный net_return_pct (изолированный тикер, не 50k)")
    ax.set_ylabel("cumsum net %")
    ax.legend()
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "equity_curves.png", dpi=120)
    plt.close(fig)


def plot_source_split(split: dict[str, Any]) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    labels = ["support A-path", "resistance B-path"]
    values = [split["support"]["n"], split["resistance"]["n"]]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(labels, values, color=["#4c72b0", "#c44e52"])
    ax.set_title("B: сделки по source")
    ax.set_ylabel("n")
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "source_split.png", dpi=120)
    plt.close(fig)


def render_report(
    inputs: dict[str, Any],
    results: dict[str, Any],
    metrics_a: dict[str, Any],
    metrics_b: dict[str, Any],
    split: dict[str, Any],
    extra: dict[str, int],
    examples: list[dict[str, Any]],
    plugin: dict[str, Any],
    verdict: dict[str, Any],
) -> str:
    cfg_a = (inputs.get("configs") or {}).get("A") or {}
    cfg_b = (inputs.get("configs") or {}).get("B") or {}
    flags = inputs.get("flags_at_start") or []
    flag_lines = [
        f"- `{row['name']}` (id={row['id']}): in_paper_test={row['in_paper_test']}, locked={row['locked']}"
        for row in flags
    ]
    example_lines = []
    if examples:
        for item in examples:
            example_lines.append(
                f"- `{item['entry_ts']}` вход {item['entry_price']}, выход {item['exit_price']} "
                f"({item['exit_reason']}, {item['net_return_pct']:+.3f}%), "
                f"source=`{item['source']}`. {item['note']}"
            )
    else:
        example_lines.append("- Сделок пути B на AFKS нет — описывать нечего.")

    plugin_line = "третий прогон (plugin) не запускался."
    if plugin.get("ran"):
        plugin_line = (
            f"status={plugin.get('status')}, n_match={plugin.get('n_match')} "
            f"(B={plugin.get('n_b')}, plugin={plugin.get('n_plugin')}), "
            f"source на plugin-сделках={plugin.get('source_on_plugin_trades')}."
        )

    reason_lines = [f"- {item}" for item in verdict["reasons"]]
    levels = ((cfg_a.get("config") or {}).get("patterns") or {}).get("levels_reversal") or {}
    return f"""# Issue #119: smoke AFKS для `levels_sr_breakout` vs #44/#103

## Резюме

Изолированный бэктест **одного тикера AFKS** за `2024-08-01` … `timestamp < 2026-08-21`.
Это **не** портфельный replay на 50k и **не** вердикт катить в paper.

| Код | patterns | n | PF | Exp % | WR | MaxDD % |
|---|---|---:|---:|---:|---:|---:|
| A | `levels_reversal` + `signal_4h_buy` | {metrics_a.get('n')} | {_fmt_pf(finite_pf(metrics_a), bool(metrics_a.get('pf_infinite')))} | {_fmt(metrics_a.get('exp_pct'), 3)} | {_fmt(metrics_a.get('wr'), 1)} | {_fmt(metrics_a.get('maxdd_pct'), 1)} |
| B | `levels_sr_breakout` + `signal_4h_buy` | {metrics_b.get('n')} | {_fmt_pf(finite_pf(metrics_b), bool(metrics_b.get('pf_infinite')))} | {_fmt(metrics_b.get('exp_pct'), 3)} | {_fmt(metrics_b.get('wr'), 1)} | {_fmt(metrics_b.get('maxdd_pct'), 1)} |
| B support | `{SOURCE_SUPPORT}` | {split['support']['n']} | {_fmt_pf(finite_pf(split['support']['metrics']), bool(split['support']['metrics'].get('pf_infinite')))} | {_fmt(split['support']['metrics'].get('exp_pct'), 3)} | {_fmt(split['support']['metrics'].get('wr'), 1)} | {_fmt(split['support']['metrics'].get('maxdd_pct'), 1)} |
| B resistance | `{SOURCE_RESISTANCE}` | {split['resistance']['n']} | {_fmt_pf(finite_pf(split['resistance']['metrics']), bool(split['resistance']['metrics'].get('pf_infinite')))} | {_fmt(split['resistance']['metrics'].get('exp_pct'), 3)} | {_fmt(split['resistance']['metrics'].get('wr'), 1)} | {_fmt(split['resistance']['metrics'].get('maxdd_pct'), 1)} |

B добавил **{extra['added']}** сделок относительно A ({extra['n_a']} → {extra['n_b']}):
путь B (resistance) + support-входы, которые A резал вето без трекера.

**Вердикт:** {verdict['label']}.

![Сравнение метрик](plots/metrics_comparison.png)

## Конфиги

Общее: `level_timeframe={levels.get('level_timeframe')}`, `level_method={levels.get('level_method')}`, confirm `{levels.get('confirm_windows')}`, RR 1:2, commission 0.06%, `signal_4h_buy` включён. `level_breakout_retest` выключен.

- A SHA-256: `{cfg_a.get('config_sha256')}`.
- B SHA-256: `{cfg_b.get('config_sha256')}`.
- Снимок: `{inputs.get('extracted_at')}`. Референс: `{ (inputs.get('reference') or {}).get('name') }` id={(inputs.get('reference') or {}).get('id')}, locked={(inputs.get('reference') or {}).get('locked')}.

Флаги стратегий на старте (после прогона те же):

{chr(10).join(flag_lines) or '- нет'}

`test_20260731`, `test_20260820`, `test_20260821` не перезаписывались. Черновик Lab не создавался.

## Книги #44 и #103 (контекст, другая методика)

| Книга | Что это | AFKS |
|---|---|---|
| #44 | Портфель 50k, **без** вето #97, exclusive `date_to=2026-08-15` | {BOOK_44_AFKS['n_trades']} сделок, {BOOK_44_AFKS['pnl_rub']:+.2f} RUB (слоты, не isolated PF) |
| #103 | Портфель 50k, **после** вето, swing+impulse, то же окно | isolated AFKS в пакете нет; портфель n={BOOK_103_CONTEXT['portfolio_n_trades']}, PF {BOOK_103_CONTEXT['portfolio_pf']} |
| #100 Lab | Isolated Lab, **только swing**, `date_from=2024-08-21` | n={BOOK_100_AFKS_LAB['n']}, PF {BOOK_100_AFKS_LAB['pf']}, WR {BOOK_100_AFKS_LAB['wr']}% |

Цифры книг нельзя вычитать из isolated A/B как «дельта PF». A — честная база после вето на одном тикере (без трекера в вето). B — тот же период и фильтры, но OR двух путей **и** вето с `LevelsTracker` (пробитое сопротивление не opposing zone). Поэтому B-support (78) ≠ A (39): это не удвоение и не баг.

![Накопленный net %](plots/equity_curves.png)

## Source кандидата

Неразмеченных сделок B: **{split['unlabeled_n']}** (должно быть 0).

![Разбивка source](plots/source_split.png)

### Выборочное описание пути B

{chr(10).join(example_lines)}

Бар ALRS 2026-08-20 11:50 @ 19.80 к AFKS не относится. На AFKS смотрим метрики и `source`.

## Plugin / Lab HTF

Третий прогон — `run_portfolio_backtest` с тем же конфигом B (тот же путь, что Lab `_run_job` после #116): {plugin_line}

Plugin-сделки сейчас **не** копируют `source` из `EntrySignal.metadata` — для разметки пути используйте `run_strategy_backtest`.

## Вердикт для продукта

{chr(10).join(reason_lines)}

Locked и эталонные стратегии не менять. Следующий шаг — только если вердикт просит расширить вселенную или сетку ретеста.

## Воспроизводимость

- Конфиги и SHA: `inputs.json`.
- Прогоны: `results.json` (`extract_inputs.py`).
- Код: `analysis.py`.
"""


def run_analysis(
    inputs_path: Path | None = None,
    results_path: Path | None = None,
) -> dict[str, Any]:
    inputs = load_json(Path(inputs_path) if inputs_path else DEFAULT_INPUTS)
    results = load_json(Path(results_path) if results_path else DEFAULT_RESULTS)
    runs = results.get("runs") or {}
    run_a = runs.get("A") or {}
    run_b = runs.get("B") or {}
    if run_a.get("status") != "success" or run_b.get("status") != "success":
        raise ValueError(
            f"expected successful A and B runs, got A={run_a.get('status')} B={run_b.get('status')}"
        )
    frame_a = trades_frame(run_a.get("trades") or [], default_source="levels_reversal")
    frame_b = trades_frame(run_b.get("trades") or [])
    metrics_a = metrics_from_trades(frame_a)
    metrics_b = metrics_from_trades(frame_b)
    split = source_split(frame_b)
    extra = extra_vs_baseline(metrics_a["n"], metrics_b["n"])
    examples = pick_path_b_examples(frame_b)
    plugin = plugin_parity(run_b, runs.get("B_plugin"))
    verdict = product_verdict(metrics_a, metrics_b, split, extra, plugin)
    plot_metrics(
        [
            {"label": "A", "n": metrics_a["n"], "pf": finite_pf(metrics_a)},
            {"label": "B mix", "n": metrics_b["n"], "pf": finite_pf(metrics_b)},
            {"label": "B support", "n": split["support"]["n"], "pf": finite_pf(split["support"]["metrics"])},
            {
                "label": "B resist",
                "n": split["resistance"]["n"],
                "pf": finite_pf(split["resistance"]["metrics"]),
            },
        ]
    )
    plot_equity(frame_a, frame_b)
    plot_source_split(split)
    report = render_report(
        inputs, results, metrics_a, metrics_b, split, extra, examples, plugin, verdict
    )
    (ANALYSIS_DIR / "report.md").write_text(report, encoding="utf-8")
    summary = {
        "issue": 119,
        "ticker": "AFKS",
        "date_from": results.get("date_from"),
        "date_to": results.get("date_to"),
        "period_last_day": results.get("period_last_day"),
        "extracted_at": inputs.get("extracted_at"),
        "updated_at": results.get("updated_at"),
        "protected_untouched": results.get("protected_untouched"),
        "reference": inputs.get("reference"),
        "config_sha": {
            "A": ((inputs.get("configs") or {}).get("A") or {}).get("config_sha256"),
            "B": ((inputs.get("configs") or {}).get("B") or {}).get("config_sha256"),
        },
        "runs": {
            "A": {"engine": "run_strategy_backtest", "metrics": metrics_a},
            "B": {
                "engine": "run_strategy_backtest",
                "metrics": metrics_b,
                "source": split,
            },
            "B_plugin": plugin,
        },
        "extra_vs_a": extra,
        "path_b_examples": examples,
        "books": {
            "issue_44_afks": BOOK_44_AFKS,
            "issue_103": BOOK_103_CONTEXT,
            "issue_100_afks_lab": BOOK_100_AFKS_LAB,
        },
        "verdict": verdict,
        "plot_files": ["metrics_comparison.png", "equity_curves.png", "source_split.png"],
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
    parser = argparse.ArgumentParser(description="Issue #119 AFKS smoke analysis")
    parser.add_argument("--inputs", default=str(DEFAULT_INPUTS))
    parser.add_argument("--results", default=str(DEFAULT_RESULTS))
    args = parser.parse_args()
    run_analysis(Path(args.inputs), Path(args.results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
