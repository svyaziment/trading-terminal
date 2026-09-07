#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Issue #143 — робастность ступенчатого трейлинг-стопа к уровням сетки (эпик #142).

Только чтение. #143 — надстройка над #139: тот же универсум, та же конфигурация
(id=126), тот же движок `StrategyEvaluator`, тот же симулятор слотов и та же функция
`apply_trailing`. Отличается только набор проверяемых ступеней трейлинга (сетка) и
стресс-факторы (комиссия, проскальзывание).

Конвейер (--stage all — полный цикл):
  extract — по каждому тикеру строится исторический 1m-путь; кандидаты вместе с
            path [(high, low)] кэшируются в cache/, повторный расчёт сеток БД не трогает;
  analyze — сценарные выходы по всем сеткам, пересборка книги функциями #139, паритет
            базовой сетки с #139, стресс (комиссия, проскальзывание), walk-forward по
            скользящим окнам, концентрация результата, оценка устойчивости (0…100);
  report  — пересборка отчётов из report.json.gz без обращения к БД (после правок текста).

Стадии три: extract / analyze / report (и all). Графиков прогон не строит — сводка
держится на markdown-таблицах и юникод-спарклайнах (долг #143, см. ограничения отчёта).

Защищённая конфигурация (strategies, flags id=126/36/102/118) не изменяется:
используются только SELECT, файл конфигурации на запись не открывается.

Артефакты публикуются в этом каталоге и индексируются git'ом: report.md, run.md,
summary.json, report.json.gz, grids.csv, walkforward.csv, exits.jsonl.gz,
contract.json, extract_summary.json. Рабочий кэш путей — в cache/ (не индексируется),
отладочные прогоны — в каталоги вида out_*/ (тоже не индексируются).

Запуск (из корня репозитория; лог — в отчёты агента, как требует постановка #143):
    python analytics/issue-143-trailing-robustness/run.py --stage extract --tickers SBER
    python analytics/issue-143-trailing-robustness/run.py --stage analyze --tickers SBER
    python analytics/issue-143-trailing-robustness/run.py  *> reports/Vulpec/143_trailing-robustness/log.txt
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import logging
import math
import os
import random
import statistics
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
REF139_DIR = REPO_ROOT / "analytics" / "issue-139-trailing-stop-new-level"
GRIDS_PATH = HERE / "grids.json"
# Публикуемые артефакты лежат в каталоге задачи (конвенция analytics/: run.py, grids.json,
# report.md, run.md, summary.json и таблицы — рядом с кодом, индексируются git'ом).
OUT_DIR = HERE
# Служебный кэш 1m-путей (paths_<ticker>.json.gz, лог прогона) — большие и полностью
# перепроизводимые из БД; каталог cache/ не индексируется (.gitignore).
CACHE_DIR = HERE / "cache"

ISSUE = 143
GRIDS_SCHEMA = "143-trailing-v2"
# Имена публикуемых артефактов задаёт единственный источник — write_outputs() ниже
# (report.json.gz, summary.json, report.md, run.md, grids.csv, walkforward.csv,
# exits.jsonl.gz, contract.json, extract_summary.json). Отдельные константы-имена
# устаревали молча (REPORT_NAME указывал на report.json вместо report.json.gz), а
# PLOTS_DIR_NAME наследовался от #139, хотя графиков этот прогон не строит (§13).

# Допуски паритета: шаг цены акций MOEX — копейки, поэтому ценовой допуск
# относительный (б.п.), а рублёвые — абсолютные.
# Оговорка по доходности: #139 хранит net_return_pct, посчитанный от ЦЕНЫ ИСПОЛНЕНИЯ
# движка (entry_exec), наружу из артефакта отдаётся только signal-ный entry_price
# (пример FEES 2024-08-12: 0.0954 против 0.09533 у движка). Разница баз входа даёт
# систематический сдвиг ~0,04 п.п. на сделку; механика выхода (цена/время/причина)
# при этом сходится точно, поэтому расхождения по цене/времени/причине считаются
# блокирующими, а по доходности — дрейфом методики с отдельным допуском.
TOL_EXIT_BP = 1.0            # 0.01 % от цены выхода
TOL_EQUITY_RUB = 1.0         # минимальный абсолютный допуск по equity/PnL книги, ₽
TOL_EQUITY_REL = 2e-3        # относительный допуск по equity/PnL книги (0,2 %)
TOL_RET_PP = 0.10            # допуск по net_return_pct одной сделки, п.п. (сдвиг баз входа)
TOL_DD_PP = 0.5              # расхождение максимальной просадки / винрейта, п.п.
SURVIVE_EQ_RATIO = 0.90      # equity сетки >= 90 % equity базовой сетки
MIN_WINDOW_TRADES = 20
DB_STATEMENT_TIMEOUT_MS = 120_000
DEFAULT_WORKERS = max(1, min(8, (os.cpu_count() or 4)))
# Артефакты #139, отпечатки которых попадают в протокол прогона (только чтение).
REF139_FILES = tuple(REF139_DIR / name for name in (
    "trailing.py", "analysis.py", "extract_inputs.py",
    "inputs.json", "results.json", "summary.json"))

LIMITATIONS = [
    "Анализ исторический: устойчивость параметров не является прогнозом будущих результатов.",
    "Пути строятся из 1m-свечей; внутрибарсовая последовательность high/low неизвестна — "
    "упрощение унаследовано от #139 (стоп проверяется раньше тейка на том же баре).",
    "Слоты, комиссия и объёмный приоритет зафиксированы конфигурацией id=126; альтернативные "
    "режимы исполнения вынесены в отдельные задачи.",
    "Метрики на подокнах ограничены по числу сделок: выбор сетки по одному окну неустойчив.",
    "Паритет по доходности одной сделки проверяется с допуском 0,1 п.п.: #139 считает "
    "net_return_pct от цены исполнения движка, #143 — от entry_price артефакта. Вклад в "
    "капитал по книге — единицы-десятки ₽; цена, время и причина выхода совпадают точно.",
    "Решётка неполная относительно постановки #143 (ожидалось 8–12 комбинаций): проверено 5 "
    "многоступенчатых сеток (ref139, tight_after_take, late_conservative, three_step_steady, "
    "two_step_aggressive). Одношаговые варианты (+2R→+1R, +2R→+2R) и «безубыточный» шаг "
    "(stop = 0) в решётку не входили: вывод переносится только на перечисленные конфигурации.",
    "Стресс издержками мягче заказанного: комиссия 0,06 / 0,10 / 0,15 % от оборота (в #143 "
    "ожидались 0,3 / 0,6 / 1,5 %), проскальзывание 0 / 5 / 10 / 20 б.п. от цены вместо "
    "0 / 1 / 3 шага цены тикера; чувствительность к минимальному лоту MOEX не моделировалась.",
    "Графиков прогон не строит (в #139 есть plots/*.png): сводка держится на markdown-"
    "таблицах и спарклайнах, визуального сравнения equity-кривых сеток в пакете нет.",
    "Чувствительность результата к risk_reward (1:2 / 1:3) в этот прогон не входила: сетки "
    "ступеней перебирались при тейке, зафиксированном конфигурацией id=126.",
    "Концентрация (§10) считается по оценке PnL сделки как `net_return_pct × размер слота` "
    "(10 000 ₽), а не по фактическому объёму: сумма корзин превышает PnL книги базовой сетки "
    "примерно на 18%, поэтому её доли верны как относительные, а абсолютный вклад тикера завышен.",
    "Порог «когда фиксированный стоп перестаёт уступать трейлингу» (пункт приёмки #143) не "
    "вычислен: книга A (fixed 1:3 из #139) в стресс-решётку не прогонялась, а сравнивать "
    "стрессовую B с непрогнанным A некорректно — издержки бьют по обеим книгам. Продолжение: "
    "прогнать A по тем же 12 сценариям издержек.",
    "Решётка не регулярная: сдвиг базовой сетки целиком (+0.5R / −0.5R) отдельной осью не "
    "строился — варианты подобраны по форме (число ступеней, ранние и поздние триггеры), поэтому "
    "на вопрос «не подгонка ли это» отвечает только walk-forward (§6), а не структура решётки.",
]

# Продолжения по roadmap эпика #142 (номера сверены с задачами на 2026-09-08).
NEXT_ISSUES = [
    "#144 — контракт config.trailing_stop: схема, валидация и дефолты в trading_config.py "
    "(дефолтную сетку утверждает Product Owner, не аналитика).",
    "#145 — ступенчатый трейлинг в StrategyEvaluator, плагине и портфельном симуляторе: "
    "решётка #143 становится частью проверки боевой реализации.",
    "#147 — паритет боевого пути трейлинг-стопа с книгой #139 (гейт эпика): вывод #143 "
    "перепроверяется на живом коде, а не на оверлей-симуляторе.",
    "#151 — трейлинг-стоп в песочнице LiveExecutor: ratchet, рестарт, kill-switch.",
    "#152 — приёмка на живом периоде: факт против модели и итоговый вердикт.",
    "Долг #143 — добить решётку до 8–12 сеток (одношаговые и «безубыточный» шаг), перейти на "
    "шаг цены вместо б.п., добавить чувствительность к risk_reward 1:2 / 1:3 и графики "
    "equity-кривых по примеру #139, а также прогнать книгу A (fixed 1:3) по той же стресс-"
    "решётке: только так появляется порог, при котором трейлинг перестаёт бить фиксированный стоп.",
]

LOG = logging.getLogger("issue143")


def _float(value: Any, default: float = 0.0) -> float:
    """Плотный float: None/пустое/NaN/Infinity -> default."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _int(value: Any, default: int = 0) -> int:
    """Плотный int (см. _float)."""
    return int(_float(value, float(default)))


def _round(value: Any, digits: int = 2) -> float:
    """Округление с защитой от мусорных значений."""
    return round(_float(value), digits)


def read_json(path: Path, default: Any = None) -> Any:
    """Читает JSON; default — если файла нет."""
    if not path.exists():
        if default is None:
            raise FileNotFoundError(f"Нет файла {path}")
        return default
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def read_json_gz(path: Path, default: Any = None) -> Any:
    """Читает JSON.GZ; default — если файла нет."""
    if not path.exists():
        if default is None:
            raise FileNotFoundError(f"Нет файла {path}")
        return default
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: Path, payload: Any) -> Path:
    """Атомарная запись JSON (tmp -> replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, default=str, sort_keys=True)
    os.replace(tmp, path)
    return path


def write_jsonl(path: Path, rows: Sequence[dict[str, Any]], compress: bool = False) -> Path:
    """Атомарная запись JSONL (опционально .gz)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    opener = gzip.open if compress else open
    mode = "wt" if compress else "w"
    with opener(tmp, mode, encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, default=str, sort_keys=True) + "\n")
    os.replace(tmp, path)
    return path


def write_csv(path: Path, rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> Path:
    """Атомарная запись CSV (utf-8-sig, разделитель ';')."""
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(list(columns))
        for row in rows:
            writer.writerow([row.get(col) for col in columns])
    os.replace(tmp, path)
    return path


def write_text(path: Path, lines: Sequence[str]) -> Path:
    """Атомарная запись текстового артефакта."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def sha256_of(path: Path) -> str:
    """SHA-256 файла, потоково."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(payload: Any) -> str:
    """SHA-256 канонического JSON (ключи отсортированы)."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                     default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def grid_steps(items: Iterable[dict[str, Any]]) -> list[dict[str, float]]:
    """Нормализует ступени к [{"trigger": R, "stop": R}] с сортировкой по trigger."""
    steps = [{"trigger": _float(s.get("trigger")), "stop": _float(s.get("stop"))}
             for s in items or ()]
    return sorted(steps, key=lambda s: (s["trigger"], s["stop"]))


def signature(steps: Iterable[dict[str, Any]]) -> str:
    """Подпись ступеней: '2-1.5|2.5-2'."""
    return "|".join(f"{_float(s.get('trigger')):g}-{_float(s.get('stop')):g}" for s in steps)


def validate_grids(grids: dict[str, Any]) -> dict[str, Any]:
    """Проверяет сетки до расчёта: схема, дубликаты, монотонность, покрытие."""
    errors: list[str] = []
    warnings: list[str] = []
    if str(grids.get("schema")) != GRIDS_SCHEMA:
        errors.append(f"schema должен быть {GRIDS_SCHEMA!r}")
    rows = grids.get("grids") or []
    if len(rows) < 4:
        errors.append(f"сеток {len(rows)} — нужно не меньше 4")
    base_id = str(grids.get("baseline_grid_id") or "")
    ids: list[str] = []
    sigs: dict[str, str] = {}
    for row in rows:
        grid_id = str(row.get("id") or "")
        if not grid_id:
            errors.append("сетка без id")
            continue
        if grid_id in ids:
            errors.append(f"дублируется id сетки {grid_id}")
        ids.append(grid_id)
        steps = grid_steps(row.get("steps") or [])
        if not steps:
            errors.append(f"сетка {grid_id}: нет ступеней")
            continue
        if any(s["stop"] >= s["trigger"] for s in steps):
            errors.append(f"сетка {grid_id}: stop >= trigger")
        if any(b["stop"] <= a["stop"] or b["trigger"] <= a["trigger"]
               for a, b in zip(steps, steps[1:])):
            errors.append(f"сетка {grid_id}: ступени не монотонны")
        if any(s["stop"] <= 0 for s in steps):
            errors.append(f"сетка {grid_id}: stop должен быть > 0R")
        if steps[0]["trigger"] <= 1.0:
            warnings.append(f"сетка {grid_id}: первая ступень не позже +1R")
        sig = signature(steps)
        if sig in sigs:
            errors.append(f"сетки {sigs[sig]} и {grid_id} совпадают ({sig})")
        else:
            sigs[sig] = grid_id
    if base_id not in ids:
        errors.append(f"baseline_grid_id={base_id!r} нет в grids")
    control = str(((grids.get("control") or {}).get("grid_id")) or "")
    if control and control not in ids:
        errors.append(f"control.grid_id={control!r} нет в grids")
    if base_id and control and base_id == control:
        errors.append("baseline и control совпадают")
    data = grids.get("data") or {}
    if not data.get("tickers"):
        errors.append("data.tickers пуст")
    if data.get("volume_order") is not None and len(data["volume_order"]) < len(
            data.get("tickers") or []):
        errors.append("data.volume_order короче data.tickers")
    stress = grids.get("stress") or {}
    if not (stress.get("commission_pct") or stress.get("slippage_bps")):
        warnings.append("stress пустой — чувствительность не проверяется")
    if len(sigs) < 4:
        errors.append("уникальных сеток меньше 4")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "n_grids": len(ids), "n_unique": len(sigs), "signatures": sigs}


def load_module(name: str, path: Path) -> Any:
    """Импортирует модуль #139 по пути (без правки sys.path)."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Не загружается модуль {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_REF139_CACHE: dict[str, Any] | None = None


def load_ref139() -> dict[str, Any]:
    """Загружает модули, артефакты и константы #139 (источник истины для #143)."""
    global _REF139_CACHE
    if _REF139_CACHE is not None:
        # Кэш на процесс: стресс-потомки вызывают загрузку на каждую задачу.
        # Изменяемые поля отдаём копиями, чтобы прогоны не разделяли состояние.
        cached = dict(_REF139_CACHE)
        cached["volume_order"] = list(_REF139_CACHE["volume_order"])
        cached["universe"] = list(_REF139_CACHE["universe"])
        cached["steps"] = [dict(s) for s in _REF139_CACHE["steps"]]
        return cached
    trailing = load_module("ref139_trailing", REF139_DIR / "trailing.py")
    analysis = load_module("ref139_analysis", REF139_DIR / "analysis.py")
    extract = load_module("ref139_extract", REF139_DIR / "extract_inputs.py")
    inputs = read_json(REF139_DIR / "inputs.json")
    results = read_json(REF139_DIR / "results.json")
    summary = read_json(REF139_DIR / "summary.json")
    if str(results.get("status")) != "success":
        raise ValueError("results.json #139: status != success")
    config = results.get("config") or {}
    ref = {
        "trailing": trailing, "analysis": analysis, "extract": extract,
        "inputs": inputs, "results": results, "summary": summary, "config": config,
        "steps": grid_steps(results.get("trailing_steps") or []),
        "period": [str(results["date_from"]), str(results["date_to"])],
        "universe": list(results.get("universe") or []),
        "volume_order": list(results.get("volume_order") or []),
        "max_positions": _int(results.get("max_positions")),
        "slot_size_rub": _float(results.get("slot_size_rub")),
        "initial_capital_rub": _float(results.get("initial_capital_rub")),
        "commission_pct": _float(config.get("commission_pct"), 0.06),
        "slippage_pct": _float(config.get("slippage_pct")),
        "risk_reward": _float((config.get("risk_reward") or {}).get("reward"), 3.0),
        "strategy_id": _int(results.get("strategy_id")),
        "strategy_name": str(results.get("strategy_config_name") or ""),
        "n_candidates": _int(results.get("candidate_trades")),
        "n_reached_2r": _int(results.get("reached_2r_trades")),
        "protected": results.get("protected") or {},
        "flags_at_end": results.get("flags_at_end") or [],
        "protected_untouched": bool(results.get("protected_untouched")),
    }
    _REF139_CACHE = ref
    return ref


def _db() -> Any:
    """DBManager #139 (построен на env-настройках PostgreSQL)."""
    from app.db.db_manager import DBManager  # type: ignore

    return DBManager()


def protected_flags(extract: Any) -> list[dict[str, Any]]:
    """Флаги защищённых стратегий из БД (только SELECT)."""
    extract._prepare_runtime()
    db = _db()
    try:
        return [dict(r) for r in extract._fetch_flags(db)]
    finally:
        db.close_pool()


def config_snapshot(extract: Any, strategy_id: int) -> dict[str, Any]:
    """Снимок конфигурации id=126 из БД + внутренний контракт #139."""
    extract._prepare_runtime()
    db = _db()
    try:
        row = extract._fetch_strategy(db, strategy_id)
    finally:
        db.close_pool()
    conf = row.get("config") or {}
    errors: list[str] = []
    try:
        extract.assert_config_contract(conf)
    except Exception as exc:  # контракт #139 — источник истины по конфигурации
        errors.append(str(exc))
    run_params = conf.get("run_params") or {}
    return {
        "found": True, "id": _int(row.get("id")), "name": str(row.get("name")),
        "locked": bool(row.get("locked")), "in_paper_test": bool(row.get("in_paper_test")),
        "config_sha256": extract.config_sha(conf), "patterns": sorted((conf.get("patterns") or {})),
        "risk_reward": conf.get("risk_reward"), "commission_pct": conf.get("commission_pct"),
        "slippage_pct": conf.get("slippage_pct"), "confirm_windows": conf.get("confirm_windows"),
        "tickers": sorted(str(t) for t in (run_params.get("tickers") or ())),
        "contract_errors": errors,
    }


def check_contract(ref: dict[str, Any], grids: dict[str, Any], live: dict[str, Any],
                   flags: list[dict[str, Any]]) -> dict[str, Any]:
    """Контракт #143 <-> #139: артефакты, живая конфигурация, защищённые флаги."""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, expected: Any, actual: Any, blocking: bool = True) -> None:
        checks.append({"name": name, "ok": bool(ok), "expected": expected, "actual": actual,
                       "blocking": blocking})

    res, inputs, summary = ref["results"], ref["inputs"], ref["summary"]
    add("results.status", str(res.get("status")) == "success", "success", res.get("status"))
    add("results.issue", _int(res.get("issue")) == 139, 139, res.get("issue"))
    add("config_sha256", str(res.get("config_sha256")) == str(inputs.get("config_sha256")),
        inputs.get("config_sha256"), res.get("config_sha256"))
    add("strategy_id", _int(res.get("strategy_id")) == _int(inputs.get("strategy_id")),
        inputs.get("strategy_id"), res.get("strategy_id"))
    add("universe", sorted(ref["universe"]) == sorted(inputs.get("universe") or []),
        len(inputs.get("universe") or []), len(ref["universe"]))
    add("volume_order", len(ref["volume_order"]) == len(ref["universe"]),
        len(ref["universe"]), len(ref["volume_order"]))
    add("capital", _float(res.get("initial_capital_rub")) == _float(inputs.get("initial_capital_rub")),
        inputs.get("initial_capital_rub"), res.get("initial_capital_rub"))
    add("slot", _float(res.get("slot_size_rub")) == _float(inputs.get("slot_size_rub")),
        inputs.get("slot_size_rub"), res.get("slot_size_rub"))
    add("max_positions", _int(res.get("max_positions")) == _int(inputs.get("max_positions")),
        inputs.get("max_positions"), res.get("max_positions"))
    add("candidate_trades", _int(res.get("candidate_trades")) > 0, "> 0", res.get("candidate_trades"))
    add("failed_tickers", not (res.get("failed_tickers") or []), [], res.get("failed_tickers"))
    add("replay_mismatches", _int(res.get("baseline_replay_mismatches")) == 0, 0,
        res.get("baseline_replay_mismatches"))
    ref_sig = signature(ref["steps"])
    add("steps", signature(grid_steps(inputs.get("trailing_steps") or [])) == ref_sig, ref_sig,
        signature(grid_steps(inputs.get("trailing_steps") or [])))
    add("trailing.DEFAULT_STEPS", signature(ref["trailing"].DEFAULT_STEPS) == ref_sig, ref_sig,
        signature(ref["trailing"].DEFAULT_STEPS))
    base = next((g for g in grids.get("grids") or []
                 if str(g.get("id")) == str(grids.get("baseline_grid_id"))), {})
    add("baseline==ref139", signature(grid_steps(base.get("steps") or [])) == ref_sig, ref_sig,
        signature(grid_steps(base.get("steps") or [])))
    add("trade_fields", {"A", "B", "entry_ts", "entry_price", "stop", "take", "ticker"}
        <= set((res.get("trades") or [{}])[0]), ["A", "B", "entry_*", "stop", "take", "ticker"],
        sorted((res.get("trades") or [{}])[0]))
    add("book_B.count", _int((summary.get("book_B_trailing") or {}).get("n_trades")) > 0, "> 0",
        (summary.get("book_B_trailing") or {}).get("n_trades"))
    add("ref139.protected_untouched", ref["protected_untouched"], True, ref["protected_untouched"])
    want = {126, 36, 102, 118}
    got = {_int(f.get("id")) for f in flags}
    add("protected.flags", want <= got, sorted(want), sorted(got))
    by_id = {_int(f.get("id")): f for f in flags}
    add("protected.id126", bool(by_id.get(126)) and by_id[126].get("locked") and
        by_id[126].get("in_paper_test"), {"locked": True, "in_paper_test": True}, by_id.get(126))
    if live.get("found"):
        add("db.config_sha256", live.get("config_sha256") == res.get("config_sha256"),
            res.get("config_sha256"), live.get("config_sha256"), blocking=False)
        add("db.risk_reward", _float((live.get("risk_reward") or {}).get("reward"), -1)
            == ref["risk_reward"], ref["risk_reward"], live.get("risk_reward"), blocking=False)
        add("db.commission_pct", _float(live.get("commission_pct"), -1) == ref["commission_pct"],
            ref["commission_pct"], live.get("commission_pct"), blocking=False)
        add("db.patterns", live.get("patterns") == sorted(
            (ref["config"].get("patterns") or {})), sorted(
                (ref["config"].get("patterns") or {})), live.get("patterns"), blocking=False)
        add("db.contract_errors", not (live.get("contract_errors") or []), [],
            live.get("contract_errors"), blocking=False)
    data = grids.get("data") or {}
    if data.get("date_from") or data.get("date_to"):
        add("period==ref139", [str(data.get("date_from")), str(data.get("date_to"))]
            == ref["period"], ref["period"],
            [data.get("date_from"), data.get("date_to")], blocking=False)
    blocking = [c for c in checks if c["blocking"]]
    return {"ok": all(c["ok"] for c in blocking), "n_checks": len(checks),
            "n_blocking": len(blocking), "passed": sum(1 for c in checks if c["ok"]),
            "failed": [c["name"] for c in blocking if not c["ok"]], "checks": checks}


def cache_path(ticker: str) -> Path:
    """Файл кэша путей кандидатов по тикеру (рабочий артефакт, не отчёт)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"paths_{ticker}.json.gz"


def ctx_sha256(ref: dict[str, Any], tickers: Sequence[str], period: Sequence[str]) -> str:
    """Отпечаток входных условий: конфигурация #139, период, набор тикеров."""
    return canonical_sha256({"config_sha256": ref["results"].get("config_sha256"),
                             "period": [str(p) for p in period],
                             "tickers": sorted(str(t) for t in tickers)})


def write_json_gz(path: Path, payload: Any) -> Path:
    """Атомарная запись JSON.GZ (кэш путей)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, default=str)
    os.replace(tmp, path)
    return path


def extract_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """Один тикер в потомке: 1m-путь, кандидаты движка и path [(high, low)]."""
    sys.path.insert(0, str(BACKEND_ROOT))
    extract = load_module("worker139_extract", REF139_DIR / "extract_inputs.py")
    extract._prepare_runtime()
    import pandas as pd  # лениво: только в потомке

    from app.analytics.pattern_registry import normalize_patterns
    from app.analytics.strategy_context import build_strategy_context
    from app.analytics.strategy_engine import StrategyEvaluator

    ticker = str(payload["ticker"])
    max_bars = _int(payload["max_bars"], 43_200)
    config = normalize_patterns(dict(payload["config"]))
    commission_pct = _float(config.get("commission_pct"), 0.06)
    db = _db()
    try:
        frame = db.select(
            "SELECT timestamp, open, high, low, close FROM trading.candles_1min_raw "
            "WHERE ticker=%s AND timestamp >= %s AND timestamp < %s ORDER BY timestamp",
            (ticker, payload["date_from"], payload["date_to"])).to_dataframe()
        if frame.empty:
            return {"ticker": ticker, "status": "failed", "error": "no 1min candles",
                    "candidates": []}
        for col in ("open", "high", "low", "close"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        ctx = build_strategy_context(db, ticker, config, df_1m=frame)
        if ctx.get("status") != "ok":
            return {"ticker": ticker, "status": "failed", "error": ctx.get("error"),
                    "candidates": []}
    finally:
        db.close_pool()

    ev = StrategyEvaluator(ctx["config"])
    ev.load_context(levels=ctx["levels"], ts_4h=ctx["ts_htf"], atr_by_ts=ctx["atr_by_ts"],
                    buy_ts=ctx["buy_ts"], confirm_series=ctx["confirm_series"],
                    signal_filter_series=ctx.get("signal_filter_series"),
                    htf_bars=ctx.get("htf_bars"))
    ts_list = frame["timestamp"].tolist()
    hi, lo, op, cl = (frame["high"].tolist(), frame["low"].tolist(),
                      frame["open"].tolist(), frame["close"].tolist())
    del frame
    candidates: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    mismatches = 0
    for i in range(len(ts_list)):
        row = {"timestamp": ts_list[i], "open": op[i], "high": hi[i], "low": lo[i],
               "close": cl[i]}
        if cur is not None and len(cur["path"]) < max_bars:
            cur["path"].append([float(hi[i]), float(lo[i])])
            cur["path_ts"].append(str(ts_list[i]))
            if hi[i] >= cur["trigger2r"]:
                cur["reached_2r"] = True
        decision = ev.on_bar(row, idx=i)
        action = decision.get("action")
        if action == "enter":
            entry_price = float(decision["entry_price"])
            stop = float(decision["stop"])
            cur = {"entry_price": entry_price, "stop": stop, "take": float(decision["take"]),
                   "entry_ts": str(ts_list[i]), "path": [], "path_ts": [], "reached_2r": False,
                   "trigger2r": entry_price + 2.0 * (entry_price - stop)}
        elif action == "exit" and cur is not None:
            engine = decision["trade"]
            engine_pct = _float(engine.get("net_return_pct"))
            recomputed = ((_float(engine.get("exit_price")) / cur["entry_price"] - 1.0) * 100.0
                          - commission_pct)
            if abs(recomputed - engine_pct) > 1e-2:
                mismatches += 1
            candidates.append({
                "id": f"{ticker}|{cur['entry_ts']}", "ticker": ticker,
                "entry_ts": cur["entry_ts"], "entry_price": round(cur["entry_price"], 6),
                "stop": round(cur["stop"], 6), "take": round(cur["take"], 6),
                "risk_rub": round(cur["entry_price"] - cur["stop"], 6),
                "bars_held": len(cur["path"]), "reached_2r": bool(cur["reached_2r"]),
                "source": engine.get("source"),
                "path_truncated": bool(len(cur["path"]) >= max_bars),
                "A": {"exit_ts": str(engine["exit_ts"]),
                      "exit_price": _float(engine.get("exit_price")),
                      "exit_reason": str(engine.get("exit_reason")),
                      "net_return_pct": engine_pct},
                "path": cur["path"], "path_ts": cur["path_ts"],
            })
            cur = None
    return {"ticker": ticker, "status": "success", "bars_1m": len(ts_list),
            "n_candidates": len(candidates), "engine_replay_mismatches": mismatches,
            "commission_pct": commission_pct, "candidates": candidates}


def cache_is_fresh(cache: dict[str, Any] | None, sha: str, period: Sequence[str],
                   max_bars: int) -> bool:
    """Кэш годен при совпадении отпечатка контекста, периода и окна пути."""
    if not cache or str(cache.get("status")) not in ("ok", "success"):
        return False
    return (str(cache.get("ctx_sha256")) == sha
            and [str(x)[:10] for x in (cache.get("period") or [])]
            == [str(p)[:10] for p in period]
            and _int(cache.get("max_bars")) == max_bars)


def ensure_paths(ref: dict[str, Any], tickers: Sequence[str], period: Sequence[str],
                 max_bars: int, force: bool, workers: int) -> tuple[dict[str, dict], dict[str, Any]]:
    """Кэш 1m-путей по тикерам: досчитывает только недостающее."""
    started = time.time()
    sha = ctx_sha256(ref, tickers, period)
    caches: dict[str, dict[str, Any]] = {}
    pending: list[str] = []
    for ticker in tickers:
        cache = None if force else (read_json_gz(cache_path(ticker), {}) or None)
        if cache_is_fresh(cache, sha, period, max_bars):
            cache["from_cache"] = True
            caches[ticker] = cache
            LOG.info("кэш %s: кандидатов %s", ticker, len(cache.get("candidates") or []))
        else:
            pending.append(ticker)
    base_payload = {"config": ref["config"], "date_from": period[0], "date_to": period[1],
                    "max_bars": max_bars}
    jobs = [dict(base_payload, ticker=t) for t in pending]
    chunk = 40
    while jobs:
        batch, jobs = jobs[:chunk], jobs[chunk:]
        with ProcessPoolExecutor(max_workers=max(1, min(workers, len(batch)))) as pool:
            futures = {pool.submit(extract_worker, item): item["ticker"] for item in batch}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"ticker": ticker, "status": "failed",
                              "error": f"{type(exc).__name__}: {exc}"[:300], "candidates": []}
                result["ctx_sha256"] = sha
                result["period"] = [str(p) for p in period]
                result["max_bars"] = max_bars
                result["from_cache"] = False
                write_json_gz(cache_path(ticker), result)
                caches[ticker] = result
                LOG.info("извлечено %s: кандидатов %s, баров 1m %s", ticker,
                         result.get("n_candidates"), result.get("bars_1m"))
    failed = sorted(str(t) for t, c in caches.items() if str(c.get("status")) != "success")
    stats = {
        "tickers": len(tickers),
        "from_cache": sum(1 for c in caches.values() if c.get("from_cache")),
        "extracted": sum(1 for c in caches.values() if not c.get("from_cache")),
        "paths_cached": sum(1 for c in caches.values() if c.get("from_cache")),
        "paths_extracted": sum(1 for c in caches.values() if not c.get("from_cache")),
        "paths_missing": 0,
        "failed": failed,
        "paths_failed": len(failed),
        "candidates": sum(len(c.get("candidates") or []) for c in caches.values()),
        "engine_replay_mismatches": sum(_int(c.get("engine_replay_mismatches"))
                                        for c in caches.values()),
        "seconds": round(time.time() - started, 1),
    }
    return caches, stats


def ref139_index(ref: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Индекс сделок #139 по ключу тикер|время входа."""
    return {f"{t['ticker']}|{t['entry_ts']}": t for t in ref["results"].get("trades") or []}


def collect_candidates(caches: dict[str, dict[str, Any]], index: dict[str, dict[str, Any]],
                       limit: int = 0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Кандидаты из кэшей, сопоставленные со сделками #139 (для якорного паритета)."""
    flows: list[dict[str, Any]] = []
    matched = unmatched = 0
    for cache in caches.values():
        for cand in cache.get("candidates") or []:
            ref_trade = index.get(str(cand.get("id")))
            if ref_trade is None:
                unmatched += 1
                continue
            matched += 1
            cand["ref139"] = {"A": ref_trade.get("A"), "B": ref_trade.get("B"),
                              "reached_2r": bool(ref_trade.get("reached_2r")),
                              "bars_held": _int(ref_trade.get("bars_held"))}
            flows.append(cand)
    flows.sort(key=lambda c: (str(c["A"]["exit_ts"]), str(c["entry_ts"]), str(c["id"])))
    stats = {"n_candidates_ref139": len(index), "n_candidates_local": matched + unmatched,
             "matched": matched, "n_matched_ref139": matched,
             "n_matched_local": matched, "n_unmatched_local": unmatched,
             "n_flows": len(flows), "unmatched_ref139": unmatched}
    # Ограничение выборки — только --limit: срез по первым кандидатам скоупа
    # смещал бы выборку к ранним выходам и занижал покрытие паритета.
    if limit and limit > 0:
        flows = flows[:limit]
        stats["limit"] = limit
        stats["n_flows"] = len(flows)
    return flows, stats


def net_return_pct(exit_price: float, entry_price: float, commission_pct: float) -> float:
    """Нетто-доходность в %: как в движке (#139) — брутто минус комиссия."""
    gross = (exit_price / entry_price - 1.0) * 100.0 if entry_price else 0.0
    return round(gross - commission_pct, 5)


def scenario_exit(trailing: Any, cand: dict[str, Any], steps: Sequence[dict[str, float]],
                  commission_pct: float, slippage_pct: float = 0.0) -> dict[str, Any]:
    """Выход одной сделки под сетку ступеней (тот же apply_trailing, что в #139)."""
    path = [(float(pair[0]), float(pair[1])) for pair in cand.get("path") or ()]
    res = trailing.apply_trailing(_float(cand["entry_price"]), _float(cand["stop"]),
                                  _float(cand["take"]), path,
                                  commission_pct=commission_pct, steps=list(steps))
    trail = res["trailing"]
    raw_price = _float(trail.get("exit_price"))
    price = raw_price * (1.0 - slippage_pct / 100.0) if slippage_pct else raw_price
    idx = _int(trail.get("exit_index"))
    path_ts = cand.get("path_ts") or []
    exit_ts = str(path_ts[idx]) if 0 <= idx < len(path_ts) else str(cand["A"]["exit_ts"])
    entry = _float(cand["entry_price"])
    risk = _float(cand.get("risk_rub")) or (entry - _float(cand.get("stop"))) or 1e-9
    return {
        "id": str(cand["id"]), "ticker": str(cand["ticker"]),
        "entry_ts": str(cand["entry_ts"]), "exit_ts": exit_ts,
        "entry_price": entry, "exit_price": round(price, 6),
        "exit_reason": str(trail.get("exit_reason")), "exit_index": idx,
        "step_reached": _float(trail.get("step_reached")),
        "r_multiple": round((price - entry) / risk, 4),
        "path_truncated": bool(cand.get("path_truncated")),
        "net_return_pct": net_return_pct(price, _float(cand["entry_price"]), commission_pct),
        "source": cand.get("source"),
    }


def candidates_for(trailing: Any, flows: Sequence[dict[str, Any]],
                   steps: Sequence[dict[str, float]], commission_pct: float,
                   slippage_pct: float = 0.0) -> list[dict[str, Any]]:
    """Кандидаты в формате #139 (порядок = время выхода)."""
    rows = [scenario_exit(trailing, cand, steps, commission_pct, slippage_pct) for cand in flows]
    rows.sort(key=lambda r: (r["exit_ts"], r["entry_ts"], r["ticker"]))
    return rows


def run_book(analysis: Any, cands: Sequence[dict[str, Any]], volume_order: Sequence[str]) -> dict[str, Any]:
    """Книга: слоты #139, дневная equity и метрики — функциями #139."""
    result = analysis.replay_slots([dict(c) for c in cands], list(volume_order))
    equity = analysis.daily_equity(result)
    metrics = analysis.book_metrics(result, equity)
    metrics["min_equity_rub"] = _round(float(equity.min())) if len(equity) else None
    return {"result": result, "equity": equity, "metrics": metrics}


def mfe_mae_r(cand: dict[str, Any]) -> dict[str, float]:
    """MFE/MAE в R по записанному пути — недоиспользованное движение."""
    risk = _float(cand.get("risk_rub")) or 1e-9
    path = cand.get("path") or []
    entry = _float(cand["entry_price"])
    if not path:
        return {"mfe_r": 0.0, "mae_r": 0.0}
    highs = [float(p[0]) for p in path]
    lows = [float(p[1]) for p in path]
    return {"mfe_r": round((max(highs) - entry) / risk, 3),
            "mae_r": round((min(lows) - entry) / risk, 3)}


def evaluate_grids(ref: dict[str, Any], flows: Sequence[dict[str, Any]],
                   grids: Sequence[dict[str, Any]], commission_pct: float,
                   slippage_pct: float = 0.0) -> dict[str, Any]:
    """Книга для каждой сетки; results[grid_id] = {steps, cands, exits, metrics, equity}."""
    results: dict[str, Any] = {}
    for grid in grids:
        steps = grid_steps(grid.get("steps") or [])
        cands = candidates_for(ref["trailing"], flows, steps, commission_pct, slippage_pct)
        exits = {str(cand["id"]): cand for cand in cands}
        book = run_book(ref["analysis"], cands, ref["volume_order"])
        results[str(grid["id"])] = {
            "grid_id": str(grid["id"]), "label": str(grid.get("label") or grid["id"]),
            "steps": steps, "signature": signature(steps), "cands": cands, "exits": exits,
            "metrics": book["metrics"],
            "equity_curve": [[str(k)[:10], _round(v)] for k, v in book["equity"].items()],
        }
    return results


def grid_row(entry: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """Сводная строка сетки: метрики книги и отклонения от базовой сетки."""
    m, b = entry["metrics"], base["metrics"]
    counts = m.get("exit_type_counts") or {}
    n = max(_int(m.get("n_trades"), 1), 1)
    eq_base = _float(b.get("final_equity_rub"))
    eq_delta = _float(m.get("final_equity_rub")) - eq_base
    return {
        "grid_id": entry["grid_id"], "label": entry["label"], "signature": entry["signature"],
        "steps": entry["steps"],
        "n_trades": _int(m.get("n_trades")),
        "win_rate_pct": _round(m.get("win_rate", m.get("win_rate_pct"))),
        "profit_factor": _round(m.get("profit_factor")),
        "final_equity_rub": _round(m.get("final_equity_rub")),
        "pnl_rub": _round(m.get("pnl_rub")), "pnl_pct": _round(m.get("pnl_pct")),
        "max_drawdown_pct": _round(m.get("max_drawdown_pct")),
        "drawdown_delta_pp": _round(_float(m.get("max_drawdown_pct"))
                                    - _float(b.get("max_drawdown_pct"))),
        "equity_delta_rub": _round(eq_delta),
        "equity_delta_pct": _round(eq_delta / eq_base * 100.0) if eq_base else 0.0,
        "game_over": bool(m.get("game_over")),
        "skipped_no_slot": _int(m.get("skipped_entries_no_slot")),
        "avg_trade_pnl_rub": _round(m.get("avg_trade_pnl_rub")),
        "share_trailing_pct": round(_int(counts.get("trailing")) / n * 100.0, 1),
        "share_take_pct": round(_int(counts.get("take")) / n * 100.0, 1),
        "share_initial_stop_pct": round((_int(counts.get("initial_stop"))
                                         + _int(counts.get("stop"))) / n * 100.0, 1),
        "exit_type_counts": counts,
    }


def anchor_parity(ref: dict[str, Any], base_entry: dict[str, Any],
                  flows: Sequence[dict[str, Any]], full_scope: bool = True) -> dict[str, Any]:
    """Якорный паритет #143 <-> #139: по каждой сделке и по книге целиком."""
    mismatches: list[dict[str, Any]] = []
    n_strict = 0
    n_drift = 0
    max_ret_delta = 0.0
    checked = 0
    for cand in flows:
        ref_b = (cand.get("ref139") or {}).get("B") or {}
        ours = base_entry["exits"].get(str(cand["id"])) or {}
        if not ours or not ref_b:
            continue
        checked += 1
        tol = max(abs(_float(ours.get("exit_price"))) * TOL_EXIT_BP / 10_000.0, 1e-9)
        bad: list[str] = []
        if str(ours.get("exit_reason")) != str(ref_b.get("exit_reason")):
            bad.append("exit_reason")
        if abs(_float(ours.get("exit_price")) - _float(ref_b.get("exit_price"))) > tol:
            bad.append("exit_price")
        if str(ours.get("exit_ts"))[:16] != str(ref_b.get("exit_ts"))[:16]:
            bad.append("exit_ts")
        ret_delta = abs(_float(ours.get("net_return_pct")) - _float(ref_b.get("net_return_pct")))
        max_ret_delta = max(max_ret_delta, ret_delta)
        if ret_delta > TOL_RET_PP:
            bad.append("net_return_pct")
        if bad:
            strict = [b for b in bad if b != "net_return_pct"]
            n_strict += 1 if strict else 0
            n_drift += 0 if strict else 1
            mismatches.append({"id": str(cand["id"]), "fields": bad, "issue143": {
                k: ours.get(k) for k in ("exit_price", "exit_reason", "exit_ts",
                                         "net_return_pct")}, "ref139": {
                k: ref_b.get(k) for k in ("exit_price", "exit_reason", "exit_ts",
                                          "net_return_pct")}})
    # Эталон книги — та же пересборка слотов #139, но на том же подмножестве сделок:
    # сравнение с summary возможно только когда прогон покрывает весь универсум #139.
    ids = {f"{c['ticker']}|{c['entry_ts']}" for c in flows}
    analysis = ref["analysis"]
    ref_cands = [c for c in analysis.build_candidates(ref["results"].get("trades") or [], "B")
                 if f"{c['ticker']}|{c['entry_ts']}" in ids]
    ref_book = run_book(analysis, ref_cands, ref["volume_order"])
    ref_m, ours_m = ref_book["metrics"], base_entry["metrics"]
    book_checks = []
    # Рублёвые агрегаты книги (equity и PnL) сравниваются с относительным допуском
    # от уровня капитала, а не от самого PnL: иначе допуск вырождается в единицы ₽.
    rub_rel_base = max(abs(_float(ref_m.get("final_equity_rub"))), 1.0)
    for field, tol, rel in (("n_trades", 0.0, False),
                            ("final_equity_rub", TOL_EQUITY_RUB, True),
                            ("pnl_rub", TOL_EQUITY_RUB, True),
                            ("max_drawdown_pct", TOL_DD_PP, False),
                            ("win_rate", TOL_DD_PP, False),
                            ("skipped_entries_no_slot", 0.0, False),
                            ("profit_factor", 1e-2, False)):
        want, got = _float(ref_m.get(field)), _float(ours_m.get(field))
        allow = max(tol, rub_rel_base * TOL_EQUITY_REL) if rel else tol
        book_checks.append({"field": field, "ref139_subset": want, "issue143": _round(got, 4),
                            "abs_delta": _round(abs(got - want), 4), "tol": _round(allow, 4),
                            "ok": abs(got - want) <= allow + 1e-9})
    summary_checks = []
    summary_b = ref["summary"].get("book_B_trailing") or {}
    sum_rel_base = max(abs(_float(summary_b.get('final_equity_rub'))), 1.0)
    if full_scope:
        for field, tol, rel in (("n_trades", 0.0, False),
                                ("final_equity_rub", TOL_EQUITY_RUB, True),
                                ("max_drawdown_pct", TOL_DD_PP, False)):
            want, got = _float(summary_b.get(field)), _float(ours_m.get(field))
            allow = max(tol, sum_rel_base * TOL_EQUITY_REL) if rel else tol
            summary_checks.append({"field": field, "ref139_full": want,
                                   "issue143": _round(got, 4),
                                   "abs_delta": _round(abs(got - want), 4),
                                   "tol": _round(allow, 4),
                                   "ok": abs(got - want) <= allow + 1e-9})
    # Блокирует только расхождение механики выхода; дрейф базы доходности
    # (net_return_pct сверх допуска) выносится отдельно и не валит паритет.
    ok = (bool(checked) and not n_strict and all(c["ok"] for c in book_checks)
          and all(c["ok"] for c in summary_checks))
    return {"ok": ok, "n_checked": checked, "n_mismatches": len(mismatches),
            "drift_only": bool(mismatches) and not n_strict,
            "n_mismatch_exit": n_strict, "n_mismatch_return_drift": n_drift,
            "max_abs_delta_net_return_pp": _round(max_ret_delta, 4),
            "mismatch_examples": mismatches[:20], "book_checks": book_checks,
            "summary_checks": summary_checks, "scope_trades": len(ref_cands),
            "scope_full": bool(full_scope),
            "tolerances": {"exit_price_bp": TOL_EXIT_BP, "equity_rub": TOL_EQUITY_RUB,
                           "equity_rel": TOL_EQUITY_REL, "net_return_pp": TOL_RET_PP,
                           "drawdown_pp": TOL_DD_PP}}


def run_stress(ref: dict[str, Any], flows: Sequence[dict[str, Any]],
               grids: Sequence[dict[str, Any]], stress_cfg: dict[str, Any],
               commission_pct: float, exclude_ids: set[str],
               workers: int = 1) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Стресс-решетка: комиссия ×N и проскальзывание (книга на каждую комбинацию)."""
    comm_list = [_float(x, commission_pct) for x in stress_cfg.get("commission_pct") or [commission_pct]]
    slip_list = [_float(x, 0.0) for x in (stress_cfg.get("slippage_pct")
                                          or [x / 100.0 for x in
                                              (stress_cfg.get("slippage_bps") or [])] or [0.0])]
    jobs = [{"steps": grid_steps(grid.get("steps") or []), "grid_id": str(grid["id"]),
             "comm": comm, "slip": slip}
            for grid in grids if str(grid["id"]) not in exclude_ids
            for comm in comm_list for slip in slip_list]
    runs: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []
    if not jobs:
        return runs, curves
    payloads = [{"job": job, "flows": flows, "volume_order": ref["volume_order"]} for job in jobs]
    with ProcessPoolExecutor(max_workers=max(1, min(workers, len(payloads)))) as pool:
        futures = [pool.submit(stress_job, p) for p in payloads]
        for future in as_completed(futures):
            out = future.result()
            runs.append(out["row"])
            curves.append(out["curve"])
    runs.sort(key=lambda r: (r["grid_id"], r["commission_pct"], r["slippage_pct"]))
    curves.sort(key=lambda r: (r["grid_id"], r["commission_pct"], r["slippage_pct"]))
    return runs, curves


def stress_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Один стресс-прогон в потомке: сетка ступеней + комиссия + проскальзывание."""
    ref = load_ref139()
    job = payload["job"]
    cands = candidates_for(ref["trailing"], payload["flows"], job["steps"],
                           job["comm"], job["slip"])
    book = run_book(ref["analysis"], cands, payload["volume_order"])
    m = book["metrics"]
    return {"row": {"grid_id": job["grid_id"], "commission_pct": _round(job["comm"], 4),
                    "slippage_pct": _round(job["slip"], 4),
                    "n_trades": _int(m.get("n_trades")),
                    "final_equity_rub": _round(m.get("final_equity_rub")),
                    "pnl_rub": _round(m.get("pnl_rub")),
                    "max_drawdown_pct": _round(m.get("max_drawdown_pct")),
                    "game_over": bool(m.get("game_over")),
                    "exit_type_counts": m.get("exit_type_counts") or {}},
            "curve": {"grid_id": job["grid_id"], "commission_pct": _round(job["comm"], 4),
                      "slippage_pct": _round(job["slip"], 4),
                      "equity_curve": [[str(k)[:10], _round(v)]
                                       for k, v in book["equity"].items()]}}


def sensitivity_by_grid(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Чувствительность сетки: отклик equity/ПД/ПД-просадки по стресс-сценариям."""
    out: list[dict[str, Any]] = []
    for gid in dict.fromkeys(str(r["grid_id"]) for r in runs):
        rows = [r for r in runs if r["grid_id"] == gid]
        nominal = next((r for r in rows if _float(r["slippage_pct"]) == 0.0
                        and _float(r["commission_pct"]) == min(
                            _float(x["commission_pct"]) for x in rows)), rows[0])
        worst = min(rows, key=lambda r: _float(r["final_equity_rub"]))
        counts = worst.get("exit_type_counts") or {}
        out.append({"grid_id": gid, "n_stress": len(rows),
                    "commission_min_pct": min(_float(r["commission_pct"]) for r in rows),
                    "commission_max_pct": max(_float(r["commission_pct"]) for r in rows),
                    "slippage_max_pct": max(_float(r["slippage_pct"]) for r in rows),
                    "equity_nominal_rub": nominal["final_equity_rub"],
                    "equity_worst_rub": worst["final_equity_rub"],
                    "pnl_nominal_rub": nominal["pnl_rub"],
                    "pnl_worst_rub": worst["pnl_rub"],
                    "dd_nominal_pp": nominal["max_drawdown_pct"],
                    "dd_worst_pp": max(_float(r["max_drawdown_pct"]) for r in rows),
                    "game_over_any": any(bool(r["game_over"]) for r in rows),
                    "trailing_share_worst_pct": round(
                        _int(counts.get("trailing")) / max(worst["n_trades"], 1) * 100.0, 1)})
    return sorted(out, key=lambda r: r["equity_worst_rub"], reverse=True)


def split_windows(flows: Sequence[dict[str, Any]], step: int,
                  min_trades: int) -> tuple[list[dict[str, Any]], list[str]]:
    """Окна walk-forward (шаг в месяцах) с отсевом слишком маленьких окон."""
    keys = sorted({str(f["A"]["exit_ts"])[:7] for f in flows})
    windows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for start_i in range(0, len(keys), max(step, 1)):
        chunk = keys[start_i:start_i + max(step, 1)]
        subset = [f for f in flows if str(f["A"]["exit_ts"])[:7] in set(chunk)]
        label = f"{chunk[0]}..{chunk[-1]}"
        if len(subset) < min_trades:
            skipped.append(label)
            continue
        windows.append({"id": label, "flows": subset})
    return windows, skipped


def walk_forward(ref: dict[str, Any], grids: Sequence[dict[str, Any]],
                 flows: Sequence[dict[str, Any]], cfg: dict[str, Any],
                 commission_pct: float,
                 exclude_ids: set[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Walk-forward: книга по каждой сетке в скользящих окнах."""
    step, min_trades = _int(cfg.get("step_months"), 3), _int(cfg.get("min_trades_per_window"), 20)
    active = [g for g in grids if str(g["id"]) not in exclude_ids]
    windows, skipped = split_windows(flows, step, min_trades)
    rows: list[dict[str, Any]] = []
    for window in windows:
        for grid in active:
            steps = grid_steps(grid.get("steps") or [])
            cands = candidates_for(ref["trailing"], window["flows"], steps, commission_pct)
            book = run_book(ref["analysis"], cands, ref["volume_order"])
            m = book["metrics"]
            rows.append({"window": window["id"], "grid_id": str(grid["id"]),
                         "n_trades": _int(m.get("n_trades")),
                         "pnl_rub": _round(m.get("pnl_rub")),
                         "win_rate_pct": _round(m.get("win_rate", m.get("win_rate_pct"))),
                         "profit_factor": _round(m.get("profit_factor")),
                         "max_drawdown_pct": _round(m.get("max_drawdown_pct")),
                         "final_equity_rub": _round(m.get("final_equity_rub")),
                         "game_over": bool(m.get("game_over"))})
    n_win = len(windows)
    wins: dict[str, int] = {}
    for row in rows:
        if _float(row["pnl_rub"]) > 0:
            wins[row["grid_id"]] = wins.get(row["grid_id"], 0) + 1
    for row in rows:
        row["win_windows"] = wins.get(row["grid_id"], 0)
        row["n_windows"] = n_win
        row["win_share"] = round(row["win_windows"] / max(n_win, 1), 3)
        row["from"], _, row["to"] = str(row["window"]).partition("..")
    summary = {"step_months": step, "min_trades_per_window": min_trades,
               "n_windows": len(windows), "skipped_windows": skipped,
               "windows": [{"id": w["id"], "n_trades": len(w["flows"])} for w in windows]}
    return summary, rows


def flip_matrix(flows: Sequence[dict[str, Any]], results: dict[str, Any],
                grids_order: Sequence[str], base_id: str,
                threshold_rub: float) -> dict[str, Any]:
    """Матрица смен причины выхода относительно базовой сетки + материальность."""
    base_exits = results[base_id]["exits"]
    matrix: dict[str, dict[str, int]] = {}
    material: dict[str, int] = {}
    immaterial: dict[str, int] = {}
    for grid_id in grids_order:
        row = matrix.setdefault(str(grid_id), {})
        tgt = results[grid_id]["exits"]
        for cand in flows:
            cid = str(cand["id"])
            from_r = str((base_exits.get(cid) or {}).get("exit_reason"))
            to_r = str((tgt.get(cid) or {}).get("exit_reason"))
            if from_r == to_r:
                continue
            row[to_r] = row.get(to_r, 0) + 1
            entry = _float(cand["entry_price"])
            delta_rub = ((_float((tgt.get(cid) or {}).get("exit_price"))
                          - _float((base_exits.get(cid) or {}).get("exit_price")))
                         / entry * SLOT_SIZE_RUB) if entry else 0.0
            bucket = material if abs(delta_rub) >= threshold_rub else immaterial
            bucket[to_r] = bucket.get(to_r, 0) + 1
    return {"rows": matrix, "material_by_target": material,
            "immaterial_by_target": immaterial,
            "base_id": str(base_id), "n_flows": len(flows),
            "per_grid_flips": {gid: sum(row.values()) for gid, row in matrix.items()},
            "threshold_rub_per_trade": threshold_rub,
            "note": "rows — смены причины выхода базовой сетки → данная сетка "
                    "(столбец — новая причина); материальные — с |ΔPnL| выше порога"}


def control_deltas(results: dict[str, Any], grids: Sequence[dict[str, Any]],
                   control_id: str) -> list[dict[str, Any]]:
    """Дельты каждой сетки относительно контрольной (иерархия важнее абсолютных цифр)."""
    ctrl = results[control_id]["metrics"]
    rows = []
    for grid in grids:
        gid = str(grid["id"])
        if gid == control_id:
            continue
        m = results[gid]["metrics"]
        rows.append({
            "grid_id": gid, "vs_control": control_id,
            "label": results[gid]["label"], "signature": results[gid]["signature"],
            "equity_delta_rub": _round(_float(m["final_equity_rub"])
                                       - _float(ctrl["final_equity_rub"])),
            "equity_delta_pct": _round((_float(m["final_equity_rub"])
                                        - _float(ctrl["final_equity_rub"]))
                                       / max(_float(ctrl["final_equity_rub"]), 1e-9) * 100.0),
            "pnl_delta_rub": _round(_float(m["pnl_rub"]) - _float(ctrl["pnl_rub"])),
            "drawdown_delta_pp": _round(_float(m["max_drawdown_pct"])
                                        - _float(ctrl["max_drawdown_pct"])),
            "win_rate_delta_pp": _round(_float(m.get("win_rate", m.get("win_rate_pct")))
                                        - _float(ctrl.get("win_rate", ctrl.get("win_rate_pct")))),
            "profit_factor_delta": _round(_float(m["profit_factor"])
                                          - _float(ctrl["profit_factor"])),
        })
    return sorted(rows, key=lambda r: r["equity_delta_rub"], reverse=True)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Ранговая корреляция Спирмена (без scipy)."""
    def ranks(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out
    n = len(xs)
    if n < 3:
        return None
    rx, ry = ranks(list(xs)), ranks(list(ys))
    mx = sum(rx) / n
    my = sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    return round(cov / (vx * vy) ** 0.5, 4) if vx > 0 and vy > 0 else None


def concordance(flows: Sequence[dict[str, Any]], results: dict[str, Any],
                grids_order: Sequence[str]) -> dict[str, Any]:
    """Согласованность сеток: доля стабильных сделок + попарные ρ Спирмена по R."""
    ids = [str(f["id"]) for f in flows]
    votes: dict[str, dict[str, int]] = {cid: {} for cid in ids}
    rets: dict[str, dict[str, float]] = {}
    for gid in grids_order:
        exits = results[gid]["exits"]
        per_trade: dict[str, float] = {}
        for cid in ids:
            ex = exits.get(cid)
            if not ex:
                continue
            reason = str(ex["exit_reason"])
            votes[cid][reason] = votes[cid].get(reason, 0) + 1
            per_trade[cid] = _float(ex["net_return_pct"])
        rets[gid] = per_trade
    stable = scored = 0
    for cid in ids:
        if not votes[cid]:
            continue
        scored += 1
        if max(votes[cid].values()) == len(grids_order):
            stable += 1
    pairs = []
    for i, a in enumerate(grids_order):
        for b in grids_order[i + 1:]:
            common = sorted(set(rets.get(a, {})) & set(rets.get(b, {})))
            rho = spearman([rets[a][c] for c in common], [rets[b][c] for c in common]) \
                if len(common) >= 3 else None
            pairs.append({"a": a, "b": b, "rho": rho})
    rhos = [_float(p["rho"]) for p in pairs if p["rho"] is not None]
    return {"stable_pct": round(stable / max(scored, 1) * 100.0, 1),
            "n_trades": len(ids), "n_scored": scored,
            "n_stable": stable, "n_disagree": scored - stable,
            "n_grids": len(grids_order),
            "median_pairwise_rho": _round(sorted(rhos)[len(rhos) // 2]) if rhos else None,
            "min_pairwise_rho": min(rhos) if rhos else None,
            "pairs_low_rho": [p for p in pairs
                              if p["rho"] is not None and _float(p["rho"]) < 0.7],
            "pairs": pairs}


def bucket_shares(pnls: Sequence[tuple[str, float]]) -> dict[str, Any]:
    """HHI и доля топ-3 по вкладу в совокупный ПнЛ (|вклад|)."""
    agg: dict[str, float] = {}
    for key, value in pnls:
        agg[key] = agg.get(key, 0.0) + value
    total = sum(abs(v) for v in agg.values()) or 1e-9
    shares = sorted((abs(v) / total for v in agg.values()), reverse=True)
    return {"buckets": len(agg), "hhi": round(sum(s * s for s in shares), 4),
            "top3_share_pct": round(sum(shares[:3]) / max(sum(shares), 1e-9) * 100.0, 1),
            "pnl_by_bucket_rub": {k: _round(v) for k, v in sorted(agg.items())}}


def regime_concentration(flows: Sequence[dict[str, Any]], results: dict[str, Any],
                         base_id: str) -> dict[str, Any]:
    """Концентрация результата базовой сетки: месяцы, тикеры, источник сигнала."""
    exits = results[base_id]["exits"]
    months: list[tuple[str, float]] = []
    tickers: list[tuple[str, float]] = []
    sources: list[tuple[str, float]] = []
    for cand in flows:
        ex = exits.get(str(cand["id"]))
        if not ex:
            continue
        pnl = _float(ex["net_return_pct"]) / 100.0 * SLOT_SIZE_RUB
        months.append((str(ex["exit_ts"])[:7], pnl))
        tickers.append((str(cand["ticker"]), pnl))
        sources.append((str(cand.get("source") or "?"), pnl))
    return {"months": bucket_shares(months), "tickers": bucket_shares(tickers),
            "signal_source": bucket_shares(sources),
            "interpretation": "HHI > 0,15 либо доля топ-3 > 60% — результат узкого режима"}


def write_exits_jsonl(path: Path, results: dict[str, Any],
                      grids_order: Sequence[str]) -> Path:
    """Полные выходы по каждой сетке (jsonl.gz) для разбора худших сеток."""
    target = Path(str(path))
    with gzip.open(target, "wt", encoding="utf-8") as stream:
        for gid in grids_order:
            for cand in results[gid]["cands"]:
                stream.write(json.dumps({"grid_id": gid, **cand}, ensure_ascii=False,
                                        default=str) + "\n")
    return target


def robustness_scores(ctx: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Итоговые баллы устойчивости по сеткам (0–100): худшая equity, просадка, стресс,
    walk-forward и стабильность причины выхода (флипы относительно базовой сетки)."""
    rows = {str(r["grid_id"]): r for r in ctx.get("grid_rows") or []}
    stress = {str(r["grid_id"]): r
              for r in (ctx.get("stress_sensitivity") or ctx.get("stress_rows")
                        or (ctx.get("stress") or {}).values())}
    sens = ctx.get("sens_by_grid") or stress
    base_gid = str(ctx.get("base_grid_id") or ctx.get("base_id") or "")
    base_eq = _float((rows.get(base_gid) or {}).get("final_equity_rub"), 1.0) or 1.0
    flip = ctx.get("flip") or {}
    per_grid_flips = flip.get("per_grid_flips") or {}
    n_flows = max(_int(flip.get("n_flows"), _int((ctx.get("concordance") or {}).get("n_trades"))), 1)
    walk_share = ctx.get("walk_share") or {}
    out: dict[str, dict[str, float]] = {}
    for gid, row in rows.items():
        st = stress.get(gid) or {}
        worst_eq = _float((sens.get(gid) or st or {}).get("equity_worst_rub"),
                          row["final_equity_rub"])
        # 35 — устойчивость капитала к издержкам (0.5 = «просел на 10% от базовой»)
        s_equity = clamp01((worst_eq / base_eq - 1.0) / 0.10 + 0.5) * 35.0
        # 20 — абсолютная просадка: 0% → 20 баллов, 20% → 0
        s_dd = clamp01((0.20 - _float(row["max_drawdown_pct"]) / 100.0) / 0.20) * 20.0
        # 15 — деградация просадки в стрессе (рост в 2 раза и хуже → 0)
        dd_nom = max(_float(st.get("dd_nominal_pp", row["max_drawdown_pct"])), 1e-9)
        s_stab = clamp01((2.0 - _float(st.get("dd_worst_pp", row["max_drawdown_pct"]))
                          / dd_nom) / 1.0) * 15.0
        # 15 — доля прибыльных окон walk-forward
        s_walk = 15.0 * clamp01(_float(walk_share.get(gid, 1.0)))
        # 15 — стабильность причины выхода (меньше флипов — надёжнее)
        s_flip = 15.0 * clamp01(1.0 - _float(per_grid_flips.get(gid, 0.0)) / n_flows)
        out[gid] = {"equity_worst": round(s_equity, 1), "drawdown": round(s_dd, 1),
                    "stress": round(s_stab, 1), "walk_forward": round(s_walk, 1),
                    "flips": round(s_flip, 1),
                    "total": round(s_equity + s_dd + s_stab + s_walk + s_flip, 1)}
    return out


def build_findings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Авто-выводы: нумерованные, каждый со ссылками на разделы отчёта."""
    parity = payload.get("anchor_parity") or {}
    rows = payload.get("grid_rows") or []
    conc = payload.get("concordance") or {}
    walk = payload.get("walk_forward") or {}
    regime = payload.get("regime_concentration") or {}
    scores = payload.get("robustness") or {}
    base_id = str(payload.get("baseline_grid_id") or payload.get("base_grid_id"))
    out: list[dict[str, Any]] = []

    def add(kind: str, severity: str, text: str, refs: Sequence[str]) -> None:
        out.append({"n": len(out) + 1, "kind": kind, "severity": severity,
                    "text": text, "refs": list(refs)})

    add("design", "info" if parity.get("ok") else "critical",
        f"Якорный паритет базовой сетки с #139: "
        f"{'подтверждён' if parity.get('ok') else 'НЕ подтверждён'} — сверено {_int(parity.get('n_checked'))} сделок: по механике выхода "
        f"расхождений {_int(parity.get('n_mismatch_exit'))}, сверх допуска по доходности — "
        f"{_int(parity.get('n_mismatch_return_drift'))} (цена выхода ±{TOL_EXIT_BP} б.п.).", ["Якорный паритет", "Контракт"])
    spreads = [abs(_float(r["equity_delta_rub"])) for r in rows]
    base_row = next((r for r in rows if r["grid_id"] == base_id), {})
    base_eq = _float(base_row.get("final_equity_rub"), 1.0)
    if spreads:
        add("sensitivity", "warn" if max(spreads) > 0.25 * abs(base_eq) else "info",
            f"Разброс итоговой equity между сетками — до {max(spreads):,.0f} ₽ "
            f"({max(spreads) / abs(base_eq) * 100.0:.0f}% базовой) — выбор ступеней "
            f"значим сильнее, чем сама идея трейла.", ["Сводка сеток", "Стресс"])
    flip = payload.get("flip_matrix") or {}
    if flip.get("rows"):
        total_flips = sum(sum(v.values()) for v in flip["rows"].values())
        add("stability", "info",
            f"Смена причины выхода относительно базовой сетки — {total_flips} из "
            f"{_int(payload.get('flows_count')) or _int(parity.get('n_checked'))} сделок; "
            f"материальные (> {flip.get('threshold_rub_per_trade')} ₽): "
            f"{sum(flip.get('material_by_target', {}).values())}.", ["Флипы"])
    if conc:
        add("stability", "info",
            f"Исход identical во всех сетках для {conc.get('stable_pct')}% сделок; "
            f"медианная ранговая корреляция по доходности — {conc.get('median_pairwise_rho')}.",
            ["Согласованность"])
    if walk.get("n_windows"):
        winners = {}
        for row in payload.get("walk_rows") or []:
            if _float(row["pnl_rub"]) > 0:
                winners[row["grid_id"]] = winners.get(row["grid_id"], 0) + 1
        top = sorted(winners.items(), key=lambda kv: kv[1], reverse=True)[:3]
        add("walk_forward", "info",
            f"Walk-forward ({walk['n_windows']} окон по {walk.get('step_months')} мес.): "
            f"чаще всех прибыльны "
            + (", ".join(f"{g} ({c})" for g, c in top) if top else "ни одна сетка")
            + ".", ["По подпериодам"])
    if regime:
        months = regime.get("months") or {}
        if _float(months.get("top3_share_pct")) > 60.0:
            add("concentration", "warn",
                f"Вклад месяцев в результат базовой сетки сконцентрирован: топ-3 = "
                f"{months['top3_share_pct']}% (HHI {months.get('hhi')}) — вывод переносим "
                f"осторожно.", ["Режимы"])
    dd_max = max((_float(r["max_drawdown_pct"]) for r in rows), default=0.0)
    add("risk", "warn",
        f"Максимальная дневная просадка по сеткам — {dd_max:.1f}% (методика #139: "
        f"close-дневная equity, пик-впадина).", ["Сводка сеток"])
    if any(r.get("game_over") for r in rows):
        add("risk", "critical", "game_over=True у сеток: "
            + ", ".join(str(r["grid_id"]) for r in rows if r.get("game_over")) + ".",
            ["Сводка сеток"])
    ranked = sorted(scores.items(), key=lambda kv: kv[1]["total"], reverse=True)
    if ranked:
        add("ranking", "info",
            "Лучшие по итоговому баллу устойчивости: "
            + ", ".join(f"{gid} ({val['total']})" for gid, val in ranked[:3]) + ".",
            ["Устойчивость"])
    return out


def build_recommendations(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Рекомендации и продолжения — на фактических дельтах прогона."""
    scores = payload.get("robustness") or {}
    ranked = sorted(scores.items(), key=lambda kv: kv[1]["total"], reverse=True)
    recs = [
        "Решать по совокупности (equity + просадка + PF + устойчивость по подпериодам), "
        "а не по одному метрическому пику.",
        "Любую правку trail_steps прогонять через #143 до применения к production-конфигу "
        "(защитные флаги контракта не должны давать исключений).",
    ]
    if ranked:
        recs.append("Приоритетная проверка: сетки "
                    + ", ".join(gid for gid, _ in ranked[:3])
                    + " — они лидеры по composite-баллу, но выбор по нему не автоматичен.")
    next_issues = list(NEXT_ISSUES)
    return recs, next_issues
# --------------------------------------------------------------------- оркестрация
SLOT_SIZE_RUB = 10_000.0  # ₽ на слот — переприсваивается из артефактов #139


def clamp01(value: float) -> float:
    """Зажимает значение в диапазон [0, 1]."""
    return max(0.0, min(1.0, _float(value)))


def load_grids(path: Path = GRIDS_PATH) -> dict[str, Any]:
    """Читает grids.json: ошибки валидации — исключение, предупреждения — лог."""
    if not path.exists():
        raise FileNotFoundError(f"Нет файла сеток: {path}")
    grids = read_json(path)
    report = validate_grids(grids)
    for warning in report.get("warnings") or ():
        LOG.warning("grids.json: %s", warning)
    if not report.get("ok"):
        raise ValueError("grids.json невалиден: " + "; ".join(report.get("errors") or ()))
    grids["_validation"] = report
    return grids


def run_context(ref: dict[str, Any], grids: dict[str, Any],
                args: argparse.Namespace) -> dict[str, Any]:
    """Скоуп прогона: тикеры/период/окно пути из grids.json, поверх артефактов #139."""
    data = grids.get("data") or {}
    raw_tickers = (str(args.tickers).split(",") if getattr(args, "tickers", None)
                   else [str(t) for t in (data.get("tickers") or ref["universe"])])
    tickers = [t.strip().upper() for t in raw_tickers if str(t).strip()]
    grids_rows = [g for g in (grids.get("grids") or []) if str(g.get("id"))]
    base_id = str(grids.get("baseline_grid_id")
                  or (grids_rows[0].get("id") if grids_rows else ""))
    return {
        "tickers": tickers,
        "period": [str(getattr(args, "date_from", None) or data.get("date_from")
                       or ref["period"][0]),
                   str(getattr(args, "date_to", None) or data.get("date_to")
                       or ref["period"][1])],
        "max_bars": _int(data.get("max_path_bars"), 43_200),
        "workers": max(1, _int(getattr(args, "workers", None) or data.get("workers"),
                               DEFAULT_WORKERS)),
        "limit": _int(getattr(args, "limit", None)),
        "commission": _float(ref["commission_pct"], 0.06),
        "base_id": base_id,
        "base_grid_id": base_id,
        "control_grid_id": str(((grids.get("control") or {}).get("grid_id"))
                               or grids.get("control_grid_id") or base_id),
        "control_id": str(((grids.get("control") or {}).get("grid_id"))
                          or grids.get("control_grid_id") or base_id),
        "out_dir": Path(args.out_dir) if getattr(args, "out_dir", None) else OUT_DIR,
    }


def live_contract(ref: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Живая конфигурация и флаги из БД (только SELECT); недоступность — не фатальна."""
    extract = ref["extract"]
    try:
        live = config_snapshot(extract, ref["strategy_id"])
    except Exception as exc:  # БД может быть недоступна (локальный прогон)
        LOG.warning("живой снимок конфигурации недоступен: %s", exc)
        live = {"found": False, "error": str(exc)}
    try:
        flags = protected_flags(extract)
    except Exception as exc:
        LOG.warning("флаги защищённых стратегий недоступны: %s", exc)
        flags = []
    return live, flags


def env_block(ref: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Окружение прогона: python, BIOSIM_ENV, DSN без пароля, отпечатки артефактов."""
    def _sha(path: Path) -> str:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        except OSError:
            return ""

    dsn = str((ref.get("db") or {}).get("dsn") or os.environ.get("DATABASE_URL") or "")
    return {
        "python": sys.version.split()[0], "platform": str(getattr(sys, "platform", "")),
        "biosim_env": os.environ.get("BIOSIM_ENV") or os.environ.get("APP_ENV") or "-",
        "postgres_host": dsn.split("@")[-1] if "@" in dsn else "(не задан)",
        "issue139_dir": _rel(REF139_DIR),
        "issue139_sha": {p.name: _sha(p) for p in REF139_FILES},
        "grids_sha": _sha(GRIDS_PATH), "out_dir": _rel(ctx["out_dir"]),
        "tickers": len(ctx["tickers"]), "period": ctx["period"],
        "max_path_bars": ctx["max_bars"], "workers": ctx["workers"],
    }


def stage_extract(ref: dict[str, Any], ctx: dict[str, Any],
                  force: bool) -> dict[str, Any]:
    """stage=extract: только извлечение 1m-путей (read-only) + сводка в OUT_DIR."""
    started = time.time()
    caches, stats = ensure_paths(ref, ctx["tickers"], ctx["period"], ctx["max_bars"],
                                 force=force, workers=ctx["workers"])
    payload = {
        "stage": "extract", "status": "success" if not stats["failed"] else "warning",
        "n_tickers": len(caches), "period": ctx["period"], "max_path_bars": ctx["max_bars"],
        "totals": stats,
        "caches": {ticker: {"file": _rel(path),
                            "candidates": len((caches.get(ticker) or {}).get("candidates") or []),
                            "bars": _int((caches.get(ticker) or {}).get("bars_1m")),
                            "from_cache": bool((caches.get(ticker) or {}).get("from_cache")),
                            "status": str((caches.get(ticker) or {}).get("status"))}
                   for ticker, path in ((t, cache_path(t)) for t in sorted(caches))
                   if caches.get(ticker)},
        "per_ticker": stats, "elapsed_sec": round(time.time() - started, 2),
    }
    write_json(ctx["out_dir"] / "extract_summary.json", payload)
    LOG.info("extract: %s", json.dumps(stats, ensure_ascii=False))
    return payload


def run_analyze(ref: dict[str, Any], grids: dict[str, Any], ctx: dict[str, Any],
                args: argparse.Namespace, caches: dict[str, dict] | None = None,
                extract_stats: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict]:
    """Основной проход: сетки → паритет → стресс → walk-forward → скоринг → payload."""
    global SLOT_SIZE_RUB
    SLOT_SIZE_RUB = _float(ref["slot_size_rub"], 10_000.0) or 10_000.0
    started = time.time()
    if caches is None:
        caches, extract_stats = ensure_paths(ref, ctx["tickers"], ctx["period"],
                                             ctx["max_bars"], force=False,
                                             workers=ctx["workers"])
    flows, flow_stats = collect_candidates(caches, ref139_index(ref), limit=ctx["limit"])
    if not flows:
        raise RuntimeError(f"Нет кандидатов #139 в кэшах путей: {flow_stats}")
    ordered = sorted(grids["grids"],
                     key=lambda g: 0 if str(g["id"]) == ctx["base_id"] else 1)
    order_ids = [str(g["id"]) for g in ordered]
    results = evaluate_grids(ref, flows, ordered, ctx["commission"], 0.0)
    base_entry = results[ctx["base_id"]]
    rows = [grid_row(results[gid], base_entry) for gid in order_ids]
    ref_all = ref["analysis"].build_candidates(ref["results"].get("trades") or [], "B")
    full_scope = len({f"{f['ticker']}|{f['entry_ts']}" for f in flows}) >= len(ref_all)
    parity = anchor_parity(ref, base_entry, flows, full_scope=full_scope)
    # Базовая сетка тоже проходит стресс-решётку и walk-forward: иначе её баллы
    # устойчивости считаются «в вакууме» и сравнение с остальными некорректно.
    stress_runs, stress_curves = run_stress(
        ref, flows, ordered, grids.get("stress") or {}, ctx["commission"],
        set(), workers=ctx["workers"])
    sens = sensitivity_by_grid(stress_runs)
    walk, walk_rows = walk_forward(ref, ordered, flows, grids.get("walk_forward") or {},
                                  ctx["commission"], set())
    wins = {gid: sum(1 for r in walk_rows if str(r["grid_id"]) == gid
                     and _float(r["pnl_rub"]) > 0) for gid in order_ids}
    n_win = _int(walk["n_windows"])
    for row in walk_rows:
        row["win_windows"] = _int(wins.get(str(row["grid_id"])))
        row["n_windows"] = n_win
        row["win_share"] = round(_int(row["win_windows"]) / max(n_win, 1), 3)
        row["from"], _, row["to"] = str(row["window"]).partition("..")
    walk_share = {gid: round(_int(wins.get(gid)) / max(n_win, 1), 3) for gid in order_ids}
    flip = flip_matrix(flows, results, order_ids, ctx["base_id"],
                       _float((grids.get("control") or {}).get("flip_threshold_rub"), 200.0))
    conc = concordance(flows, results, order_ids)
    ctrl = control_deltas(results, ordered, ctx["control_id"])
    regime = regime_concentration(flows, results, ctx["base_id"])
    scores = robustness_scores({
        "grid_rows": rows, "stress": {str(r["grid_id"]): r for r in sens},
        "stress_sensitivity": sens, "sens_by_grid": {str(r["grid_id"]): r for r in sens},
        "base_grid_id": ctx["base_id"],
        "walk_share": walk_share, "walk": walk, "concordance": conc, "flip": flip,
        "tolerance": _float((grids.get("thresholds") or {}).get("tolerance_rub"), 250.0)})
    live, flags = live_contract(ref)
    contract = check_contract(ref, grids, live, flags)
    write_json(ctx["out_dir"] / "contract.json",
               {**contract, "live_config": live, "protected_flags": flags[:80]})
    payload: dict[str, Any] = {
        "schema": "143-trailing-v2", "issue": 143, "ref_issue": 139,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "baseline_grid_id": ctx["base_id"], "control_grid_id": ctx["control_id"],
        "environment": env_block(ref, ctx), "contract": contract,
        "grids": [{"id": str(g["id"]), "label": str(g.get("label") or g["id"]),
                   "steps": grid_steps(g.get("steps") or []),
                   "signature": signature(grid_steps(g.get("steps") or []))}
                  for g in ordered],
        "grid_rows": rows, "anchor_parity": parity,
        "stress_runs": stress_runs, "stress_curves": stress_curves,
        "stress_sensitivity": sens,
        "sens_by_grid": {str(r["grid_id"]): r for r in sens},
        "walk_forward": walk, "walk_rows": walk_rows, "walk_share": walk_share,
        "flip_matrix": flip, "concordance": conc, "control_deltas": ctrl,
        "regime_concentration": regime, "robustness": scores,
        "flows_count": len(flows),
        "scope": {"tickers": ctx["tickers"], "period": ctx["period"],
                  "n_tickers_cached": len([c for c in (caches or {}).values() if c]),
                  "n_candidates_ref": len(ref_all), "full_scope": full_scope,
                  "path_truncated": sum(1 for f in flows if f.get("path_truncated")),
                  "limit": ctx["limit"], "flows": flow_stats,
                  "extract": extract_stats or {}},
        "metrics": rows, "limitations": LIMITATIONS,
    }
    payload["findings"] = build_findings(payload)
    recs, next_issues = build_recommendations(payload)
    payload["recommendations"], payload["next_issues"] = recs, next_issues
    payload["status"] = "success" if (parity["ok"] and contract["ok"]) else "warning"
    payload["elapsed_sec"] = round(time.time() - started, 2)
    return payload, results


def _cell(value: Any) -> str:
    """Значение в markdown-ячейку: компактный JSON, без вертикальных черт."""
    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    else:
        text = "" if value is None else str(value)
    if len(text) > 120:
        text = text[:117] + "..."
    return text.replace("|", "!").replace("\n", " ")


SPARK = "▁▂▃▄▅▆▇█"


def _spark(values: Sequence[Any], width: int = 28) -> str:
    """Юникод-спарклайн ряда (кривая капитала) — без внешних зависимостей."""
    pts = [_float(v) for v in values]
    if len(pts) < 2:
        return "—"
    pts = pts[::max(1, len(pts) // width)]
    low, high = min(pts), max(pts)
    if high - low < 1e-9:
        return SPARK[0] * len(pts)
    return "".join(SPARK[min(7, int((v - low) / (high - low) * 7.999))] for v in pts)


def _rel(path: Any) -> str:
    """Путь артефакта относительно корня репозитория. Отчёты индексируются git'ом,
    поэтому в markdown не должен попадать путь конкретного рабочего каталога машины.
    В payload/summary.json пути остаются абсолютными — это протокол прогона."""
    try:
        return Path(str(path)).resolve().relative_to(REPO_ROOT).as_posix()
    except (OSError, ValueError):
        return str(path)


def render_md(payload: dict[str, Any], out_dir: Path,
              artifacts: dict[str, Path]) -> list[str]:
    """report.md — отчёт #143 в стиле #139: контракт, решётка, стресс, устойчивость."""
    rows = payload.get("grid_rows") or []
    contract = payload.get("contract") or {}
    parity = payload.get("anchor_parity") or {}
    env, scope = payload.get("environment") or {}, payload.get("scope") or {}
    scores = payload.get("robustness") or {}
    base_id, control_id = str(payload.get("baseline_grid_id")), str(payload.get("control_grid_id"))
    by_id = {str(r["grid_id"]): r for r in rows}
    eq_rel_pct = _float((parity.get("tolerances") or {}).get("equity_rel")) * 100.0
    spread = _float(summary_block(payload).get("equity_spread_rub"))
    best = max(rows, key=lambda r: _float(r["final_equity_rub"])) if rows else {}
    worst = min(rows, key=lambda r: _float(r["final_equity_rub"])) if rows else {}
    lines = [f"# #143 · Robustness-решётка трейлинг-стопа "
             f"(референс — #{payload.get('ref_issue')})", "",
             f"Сгенерирован: {payload.get('generated_at')} · статус: **{payload.get('status')}** "
             f"· elapsed {payload.get('elapsed_sec')} сек", "",
             f"Проверено {len(rows)} сеток ступеней на {payload.get('flows_count')} сделках "
             f"({len(scope.get('tickers') or ())} тикеров, {scope.get('period')}).", "",
             f"- Финальный капитал: лучшие `{best.get('grid_id')}` "
             f"({_float(best.get('final_equity_rub')):,.0f} ₽), худшие `{worst.get('grid_id')}` "
             f"({_float(worst.get('final_equity_rub')):,.0f} ₽), разброс **{spread:,.0f} ₽**",
             f"- Паритет с #139: **{'OK' if parity.get('ok') else 'РАСХОЖДЕНИЕ'}** "
             f"(сверено {_int(parity.get('n_checked'))} сделок; по механике выхода расхождений "
             f"{_int(parity.get('n_mismatch_exit'))}, сверх допуска по доходности — "
             f"{_int(parity.get('n_mismatch_return_drift'))})",
             f"- Контракт конфигурации: {'OK' if contract.get('ok') else 'FAILED'} "
             f"(провалено: {', '.join(contract.get('failed') or ()) or 'нет'})",
             f"- Худшая сетка относительно контрольной (stakeholder-тест): "
             f"`{(payload.get('control_deltas') or [{}])[-1].get('grid_id') or '—'}`"
             f" (Δequity {(_float((payload.get('control_deltas') or [{}])[-1].get('equity_delta_rub'))):+,.0f} ₽)", ""]

    lines += ["## 1. Контракт #143 ↔ #139", "",
              f"Проверок: {_int(contract.get('n_checks'))}, блокирующих: "
              f"{_int(contract.get('n_blocking'))}, пройдено: {_int(contract.get('passed'))}.",
              "", "| проверка | ок | ожидалось | получено | блокирующая |",
              "| --- | --- | --- | --- | --- |"]
    for chk in contract.get("checks") or ():
        lines.append(f"| `{chk.get('name')}` | {'✅' if chk.get('ok') else '❌'} | "
                     f"{_cell(chk.get('expected'))} | {_cell(chk.get('actual'))} | "
                     f"{'да' if chk.get('blocking') else 'нет'} |")
    lines.append("")

    lines += ["## 2. Скоуп и окружение", "", "| параметр | значение |", "| --- | --- |"]
    for key in ("python", "platform", "biosim_env", "postgres_host", "issue139_dir",
                "out_dir", "max_path_bars", "workers", "tickers", "grids_sha"):
        if key in env:
            # Каталоги прогона — относительно репозитория: отчёт попадает в git.
            value = _rel(env[key]) if key.endswith("_dir") else env[key]
            lines.append(f"| `{key}` | {_cell(value)} |")
    for key in ("period", "tickers", "n_tickers_cached", "n_candidates_ref", "full_scope",
                "path_truncated", "limit"):
        # limit — отладочный ценз кандидатов; 0 (ценза нет) не выводим: полноту скоупа
        # уже показывают full_scope и n_candidates_ref.
        if key in scope and not (key == "limit" and not _int(scope[key])):
            lines.append(f"| `scope.{key}` | {_cell(scope[key])} |")
    lines.append(f"| `artifacts.report_json.gz` | `{_rel(artifacts.get('report_json', out_dir))}` |")
    lines.append("")

    lines += ["## 3. Решётка сеток и паритет якоря", "",
              "| id | название | ступени (R) | подпись | базовая |",
              "| --- | --- | --- | --- | --- |"]
    for grid in payload.get("grids") or ():
        steps = ", ".join(f"{s['trigger']:g}→{s['stop']:g}" for s in grid.get("steps") or ())
        lines.append(f"| `{grid.get('id')}` | {grid.get('label')} | {steps} | "
                     f"`{grid.get('signature')}` | "
                     f"{'да' if str(grid.get('id')) == base_id else ''} |")
    lines += ["", f"Паритет базовой сетки `{base_id}` с #139: "
              f"**{'OK' if parity.get('ok') else 'РАСХОЖДЕНИЕ'}** · сверка на "
              f"{'полном' if parity.get('scope_full') else 'подмножестве'} универсума "
              f"(эталонных сделок: {_int(parity.get('scope_trades'))}).",
              f"Расхождения по механике выхода (цена/время/причина): "
              f"**{_int(parity.get('n_mismatch_exit'))}**; расхождения по доходности сверх "
              f"допуска {TOL_RET_PP} п.п.: {_int(parity.get('n_mismatch_return_drift'))} "
              f"(макс. |Δ net_return| = {_float(parity.get('max_abs_delta_net_return_pp'))} п.п.). "
              f"Дрейф доходности — разница баз входа: #139 считает доходность от цены исполнения "
              f"движка, #143 — от entry_price артефакта; логика выхода при этом совпадает.",
              f"Допуски: цена выхода {TOL_EXIT_BP} б.п., equity/PnL книги "
              f"{eq_rel_pct:.2f} %, "
              f"доходность сделки {TOL_RET_PP} п.п., просадка/винрейт {TOL_DD_PP} п.п.", "",
              "| метрика книги | #139 (тот же скоуп) | #143 | Δ | допуск | ок |",
              "| --- | --- | --- | --- | --- | --- |"]
    for chk in parity.get("book_checks") or ():
        lines.append(f"| `{chk.get('field')}` | {_cell(chk.get('ref139_subset'))} | "
                     f"{_cell(chk.get('issue143'))} | {_cell(chk.get('abs_delta'))} | "
                     f"{_cell(chk.get('tol'))} | {'✅' if chk.get('ok') else '❌'} |")
    for chk in parity.get("summary_checks") or ():
        lines.append(f"| `{chk.get('field')}` · summary | {_cell(chk.get('ref139_full'))} | "
                     f"{_cell(chk.get('issue143'))} | {_cell(chk.get('abs_delta'))} | "
                     f"{_cell(chk.get('tol'))} | {'✅' if chk.get('ok') else '❌'} |")
    examples = parity.get("mismatch_examples") or []
    if examples:
        lines += ["", "Первые расхождения по сделкам:", "", "```json",
                  json.dumps(examples[:5], ensure_ascii=False, indent=1), "```"]
    lines.append("")

    lines += ["## 4. Матрица результатов (номинальные издержки)", "",
              "| сетка | сделок | капитал, ₽ | Δ к базовой, ₽ | PnL, ₽ | просадка, п.п. | "
              "Δ п.п. | PF | винрейт, % | причины выходов | skip |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in sorted(rows, key=lambda r: -_float(r["final_equity_rub"])):
        steps_txt = ", ".join(f"{k}:{v}" for k, v in sorted(
            (row.get("exit_type_counts") or {}).items(),
            key=lambda kv: -_int(kv[1]))[:6]) or "—"
        lines.append(
            f"| `{row['grid_id']}`{' ⭐' if str(row['grid_id']) == base_id else ''} | "
            f"{_int(row['n_trades'])} | {_float(row['final_equity_rub']):,.0f} | "
            f"{_float(row['equity_delta_rub']):+,.0f} | {_float(row['pnl_rub']):,.0f} | "
            f"{_float(row['max_drawdown_pct']):.2f} | "
            f"{_float(row['drawdown_delta_pp']):+.2f} | "
            f"{_float(row['profit_factor']):.2f} | {_float(row['win_rate_pct']):.1f} | "
            f"{steps_txt} | {_int(row['skipped_no_slot'])} |")
    lines += ["", "### Scorecard устойчивости (0…100, выше = надёжнее)", "",
              "| сетка | equity в стрессе (35) | просадка (20) | деградация DD (15) | "
              "walk-forward (15) | стабильность выхода (15) | итог |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for gid, sc in sorted(scores.items(), key=lambda kv: -_float(kv[1].get("total"))):
        lines.append(f"| `{gid}` | {sc['equity_worst']:g} | {sc['drawdown']:g} | "
                     f"{sc['stress']:g} | {sc['walk_forward']:g} | {sc['flips']:g} | "
                     f"**{sc['total']:g}** |")
    lines.append("")
    # Вырожденные оси scorecard: min-max нормировка обнуляет ось (или отдаёт её максимум), когда
    # значения всей решётки совпали либо оказались за границей шкалы — такая ось не ранжирует.
    comp_labels = {"equity_worst": "«equity в стрессе»", "drawdown": "«просадка»",
                   "stress": "«деградация DD»", "walk_forward": "walk-forward",
                   "flips": "«стабильность выхода»"}
    flat: list[str] = []
    live: list[str] = []
    for comp, label in comp_labels.items():
        vals = [_float(sc.get(comp)) for sc in scores.values()]
        if len(vals) < 2:
            continue
        if max(vals) - min(vals) < 1e-9:
            flat.append(f"{label} ({vals[0]:g})")
        else:
            live.append(f"{label} ({min(vals):g}…{max(vals):g})")
    if flat:
        totals = [_float(sc.get("total")) for sc in scores.values()]
        lines += ["", "Компоненты " + ", ".join(flat) + " в этом прогоне сетки не различают: у всех "
                  "значений одна граница шкалы (стресс-капитал и деградация DD — когда капитал каждой "
                  "сетки ниже `min_ratio = 0.3` от номинала, walk-forward — когда все сетки прибыльны "
                  "во всех окнах). Ранжируют поэтому только " + ", ".join(live) + f"; разброс "
                  f"итогового балла ({min(totals):g}…{max(totals):g}) описывает различие "
                  f"{len(scores)} сеток на одной книге и одном периоде и не является оценкой "
                  "надёжности правила на боевом контуре."]
        lines.append("")

    order_ids = [str(g["id"]) for g in payload.get("grids") or ()]
    runs = payload.get("stress_runs") or []
    sens = {str(r["grid_id"]): r for r in (payload.get("stress_sensitivity") or ())}
    nominal: dict[str, dict[str, Any]] = {}
    for run in runs:
        gid, cur = str(run["grid_id"]), None
        if _float(run["slippage_pct"]):
            continue
        cur = nominal.get(gid)
        if cur is None or _float(run["commission_pct"]) < _float(cur["commission_pct"]):
            nominal[gid] = run
    lines += ["## 5. Стресс-решётка издержек", ""]
    if runs:
        lines += ["| сетка | комиссия, % | проскальзывание, % | сделок | капитал, ₽ | "
                  "Δ к номиналу, ₽ | просадка, п.п. | game over |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for run in runs:
            gid = str(run["grid_id"])
            nom = nominal.get(gid) or {}
            lines.append(f"| `{gid}` | {_float(run['commission_pct']):g} | "
                         f"{_float(run['slippage_pct']):g} | {_int(run['n_trades'])} | "
                         f"{_float(run['final_equity_rub']):,.0f} | "
                         f"{_float(run['final_equity_rub']) - _float(nom.get('final_equity_rub')):+,.0f} | "
                         f"{_float(run['max_drawdown_pct']):.2f} | "
                         f"{'да' if run.get('game_over') else 'нет'} |")
    lines += ["", "### Чувствительность по сеткам", "",
              "| сетка | прогонов | комиссия, % | макс. слип, % | капитал номинал, ₽ | "
              "капитал худший, ₽ | разброс, ₽ | разброс, % | просадка ном., п.п. | "
              "просадка худшая, п.п. | Δ п.п. | game over | доля trailing (худший), % |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for gid in order_ids:
        row = sens.get(gid) or {}
        if not row:
            continue
        nom_eq, worst_eq = _float(row.get("equity_nominal_rub")), _float(row.get("equity_worst_rub"))
        spread = nom_eq - worst_eq
        dd_nom, dd_worst = _float(row.get("dd_nominal_pp")), _float(row.get("dd_worst_pp"))
        lines.append(f"| `{gid}` | {_int(row.get('n_stress'))} | "
                     f"{_float(row.get('commission_min_pct')):g}→"
                     f"{_float(row.get('commission_max_pct')):g} | "
                     f"{_float(row.get('slippage_max_pct')):g} | "
                     f"{nom_eq:,.0f} | {worst_eq:,.0f} | {spread:,.0f} | "
                     f"{spread / abs(nom_eq) * 100.0 if nom_eq else 0.0:.2f} | "
                     f"{dd_nom:.2f} | {dd_worst:.2f} | {dd_worst - dd_nom:+.2f} | "
                     f"{'да' if row.get('game_over_any') else 'нет'} | "
                     f"{_float(row.get('trailing_share_worst_pct')):.1f} |")
    curves = {(str(c["grid_id"]), _round(c["slippage_pct"], 4)): c
              for c in payload.get("stress_curves") or ()}
    if curves:
        lines += ["", "### Кривые капитала (номинал → стресс)", "",
                  "| сетка | ×1 | худший сценарий |", "| --- | --- | --- |"]
        for gid in order_ids:
            nom = next((v for (g, s), v in curves.items() if g == gid and s == 0.0), None)
            worst_curve = max((v for (g, _s), v in curves.items() if g == gid),
                              key=lambda c: _float(c["commission_pct"]) + _float(c["slippage_pct"]),
                              default=None)
            if not nom:
                continue
            lines.append(f"| `{gid}` ₽{_float(nom['equity_curve'][-1][1]):,.0f} | "
                         f"{_spark([v for _d, v in nom['equity_curve']])} | "
                         f"{_spark([v for _d, v in worst_curve['equity_curve']])} ₽"
                         f"{_float(worst_curve['equity_curve'][-1][1]):,.0f} |")
    lines.append("")

    wf = payload.get("walk_forward") or {}
    wrows = payload.get("walk_rows") or []
    wids = [str(w["id"]) for w in wf.get("windows") or ()]
    pivot = {(str(r["grid_id"]), str(r["window"])): r for r in wrows}
    lines += ["## 6. Walk-forward по окнам выхода", "",
              f"Шаг {wf.get('step_months')} мес., порог "
              f"{wf.get('min_trades_per_window')} сделок, окон: {wf.get('n_windows')}"
              + (f" (отброшено: {', '.join(wf.get('skipped_windows') or ())})"
                 if wf.get("skipped_windows") else "") + ".", "",
              "| окно | сделок | " + " | ".join(f"`{g}`" for g in order_ids) + " |",
              "| --- | --- | " + " | ".join("---" for _ in order_ids) + " |"]
    for win in wf.get("windows") or ():
        wid = str(win["id"])
        cells = " | ".join(
            (f"{_float(pivot[(g, wid)]['pnl_rub']):+,.0f}" if (g, wid) in pivot else "—")
            for g in order_ids)
        lines.append(f"| `{wid}` | {_int(win['n_trades'])} | {cells} |")
    lines += ["", "| сетка | прибыльных окон | доля | мин. PnL окна, ₽ | средний PF |",
              "| --- | --- | --- | --- | --- |"]
    for gid in order_ids:
        sub = [r for r in wrows if str(r["grid_id"]) == gid]
        if not sub:
            continue
        lines.append(f"| `{gid}` | {_int(sub[0].get('win_windows'))}/{_int(sub[0].get('n_windows'))} | "
                     f"{_float(sub[0].get('win_share')) * 100:.0f}% | "
                     f"{min(_float(r['pnl_rub']) for r in sub):+,.0f} | "
                     f"{sum(_float(r['profit_factor']) for r in sub) / len(sub):.2f} |")
    lines.append("")

    flip = payload.get("flip_matrix") or {}
    n_flows = max(_int(flip.get("n_flows"), _int(payload.get("flows_count"))), 1)
    lines += ["## 7. Флипы причины выхода относительно базовой сетки", "",
              f"Порог материальности: {_float(flip.get('threshold_rub_per_trade')):g} ₽ на сделку · "
              f"всего флипов: {sum(_int(v) for v in (flip.get('per_grid_flips') or {}).values())}.",
              "", "| сетка | флипов | доля | материальные (по новой причине) |",
              "| --- | --- | --- | --- |"]
    for gid in order_ids:
        n_flip = _int((flip.get("per_grid_flips") or {}).get(gid))
        targets = flip.get("rows") or {}
        tgt = ", ".join(f"{k}: {_int(v)}" for k, v in
                        sorted((targets.get(str(gid)) or {}).items(),
                               key=lambda kv: -_int(kv[1]))[:5]) or "—"
        lines.append(f"| `{gid}` | {n_flip} | {n_flip / n_flows * 100.0:.1f}% | {tgt} |")
    mat = flip.get("material_by_target") or {}
    if mat:
        lines += ["", "Материальные флипы по новым причинам выхода (все сетки): "
                  + ", ".join(f"`{k}`: {_int(v)}" for k, v in
                              sorted(mat.items(), key=lambda kv: -_int(kv[1]))) + "."]
    lines += ["", f"_{flip.get('note') or ''}_", ""]

    conc = payload.get("concordance") or {}
    lines += ["## 8. Согласованность сеток", "",
              f"Идентичный исход во всех {_int(conc.get('n_grids'))} сетках: "
              f"**{_float(conc.get('stable_pct')):.1f}%** сделок ({_int(conc.get('n_trades'))} всего). "
              f"Медианный ρ Спирмена по парам сеток: {_cell(conc.get('median_pairwise_rho'))}, "
              f"минимальный: {_cell(conc.get('min_pairwise_rho'))}.", ""]
    low = conc.get("pairs_low_rho") or []
    if low:
        lines += ["Пары с расхождением рангов (ρ < 0,7):", "",
                  "| сетка A | сетка B | ρ |", "| --- | --- | --- |"]
        for pr in sorted(low, key=lambda p: _float(p.get("rho")))[:20]:
            lines.append(f"| `{pr['a']}` | `{pr['b']}` | {_float(pr['rho']):.3f} |")
        lines.append("")

    ctrl_rows = payload.get("control_deltas") or []
    if ctrl_rows:
        lines += ["## 9. Дельты против контрольной сетки", "",
                  f"Контрольная сетка: `{ctrl_rows[0].get('vs_control')}` "
                  f"(иерархия важнее абсолютных цифр; Δ считаны по той же книге).", "",
                  "| сетка | Δ equity, ₽ | Δ equity, % | Δ PnL, ₽ | Δ просадки, п.п. | "
                  "Δ win rate, п.п. | Δ PF |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for row in ctrl_rows:
            lines.append(f"| `{row['grid_id']}` | {_float(row['equity_delta_rub']):+,.0f} | "
                         f"{_float(row['equity_delta_pct']):+.2f} | "
                         f"{_float(row['pnl_delta_rub']):+,.0f} | "
                         f"{_float(row['drawdown_delta_pp']):+.2f} | "
                         f"{_float(row['win_rate_delta_pp']):+.2f} | "
                         f"{_float(row['profit_factor_delta']):+.2f} |")
        lines.append("")

    reg = payload.get("regime_concentration") or {}
    lines += ["## 10. Концентрация результата базовой сетки", "",
              f"_{reg.get('interpretation') or ''}_", "",
              "| срез | корзин | HHI | доля топ-3, % | топ корзин по вкладу |",
              "| --- | --- | --- | --- | --- |"]
    for name in ("months", "tickers", "signal_source"):
        block = reg.get(name) or {}
        buckets = block.get("pnl_by_bucket_rub") or {}
        top = ", ".join(f"{k} ({_float(v):+,.0f} ₽)" for k, v in
                        sorted(buckets.items(), key=lambda kv: -abs(_float(kv[1])))[:5]) or "—"
        lines.append(f"| {name} | {_int(block.get('buckets'))} | {_float(block.get('hhi')):.3f} | "
                     f"{_float(block.get('top3_share_pct')):.1f} | {_cell(top)} |")
    lines.append("")
    # Комментарии к концентрации — по артефактам прогона, без обращения к БД; отдельно помечено то,
    # что в этом прогоне не считалось (режимы окон, гэп-выходы, распределение PnL по R).
    base_row = next((r for r in rows if str(r["grid_id"]) == base_id), {}) or {}
    base_pnl = _float(base_row.get("pnl_rub"))
    tick = (reg.get("tickers") or {}).get("pnl_by_bucket_rub") or {}
    tick_hhi = _float((reg.get("tickers") or {}).get("hhi"))
    if tick and base_pnl > 0.0:
        top5 = sorted(tick.items(), key=lambda kv: -_float(kv[1]))[:5]
        top5_sum = sum(_float(v) for _k, v in top5)
        bucket_total = sum(_float(v) for v in tick.values())
        lines += ["Топ-5 тикеров по вкладу (" + ", ".join(f"`{k}`" for k, _v in top5)
                  + f") дают {top5_sum:,.0f} ₽ — {top5_sum / bucket_total * 100.0:.1f}% свёрнутого "
                  f"PnL по сделкам ({bucket_total:,.0f} ₽). База оценки — `net_return_pct × размер "
                  f"слота` ({SLOT_SIZE_RUB:,.0f} ₽) без учёта фактического объёма, поэтому сумма "
                  f"корзин расходится с PnL книги базовой сетки ({base_pnl:,.0f} ₽) примерно в "
                  f"{bucket_total / base_pnl:.2f}×: доли верны как относительные, абсолютный вклад "
                  "тикера по этой оценке завышен. Сравнивать её с «62% топ-5» из #139 напрямую нельзя: "
                  "база того расчёта в артефактах #139 не зафиксирована, поэтому по концентрации "
                  f"остаётся опираться на HHI ({tick_hhi:.3f} по тикерам"
                  + (" — концентрации нет" if tick_hhi <= 0.15 else " — концентрация есть") + ")."]
    months = (reg.get("months") or {}).get("pnl_by_bucket_rub") or {}
    neg_months = sorted(((k, _float(v)) for k, v in months.items() if _float(v) < 0.0),
                        key=lambda kv: kv[1])
    if neg_months:
        lines.append("Отрицательных месяца " + f"{len(neg_months)} из {len(months)} — "
                     + ", ".join(f"`{k}` ({v:+,.0f} ₽)" for k, v in neg_months[:4])
                     + (f" и ещё {len(neg_months) - 4}" if len(neg_months) > 4 else "")
                     + f": на самом слабом участке (`{neg_months[0][0]}`) книга теряет "
                     f"{abs(neg_months[0][1]):,.0f} ₽ за месяц, но режим рынка (flat / trend) окнам не "
                     "размечали, так что «поведение в flat» отдельным выводом не подтверждено. Доля "
                     "сделок, закрытых «на уровне входа» из-за гэпа, а также медиана и хвосты "
                     "распределения PnL по R в сводку не вынесены — в `exits.jsonl.gz` есть "
                     "`r_multiple`, `entry_ts` и `exit_ts`, поэтому это считается без обращения к БД.")
    lines.append("")

    lines += ["## 11. Находки", ""]
    for finding in payload.get("findings") or ():
        lines.append(f"{finding.get('n')}. **[{finding.get('kind')}/{finding.get('severity')}]** "
                     f"{finding.get('text')} _({', '.join(finding.get('refs') or ())})_")
    lines += ["", "## 12. Рекомендации", ""]
    lines += [f"{i}. {rec}" for i, rec in enumerate(payload.get("recommendations") or (), 1)]
    lines += ["", "Следующие шаги / продолжения:", ""]
    lines += [f"- {nxt}" for nxt in payload.get("next_issues") or ()]
    lines += ["", "## 13. Ограничения метода", ""]
    lines += [f"- {lim}" for lim in payload.get("limitations") or ()]
    lines += ["", "## 14. Артефакты прогона", ""]
    for name, path in sorted((payload.get("artifacts") or {}).items()):
        lines.append(f"- `{name}` — `{_rel(path)}`")
    lines += ["", "Протокол запуска и воспроизведения — `run.md` рядом с этим отчётом; "
              "полные данные — `report.json.gz` (stage `report` пересобирает отчёты без БД).",
              f""]
    # ---8<--- md-d
    return lines


def summary_block(payload: dict[str, Any]) -> dict[str, Any]:
    """Компактная сводка для CI/MR (без тяжёлых секций report.json.gz)."""
    eqs = {str(r["grid_id"]): _float(r["final_equity_rub"]) for r in payload["grid_rows"]}
    ordered = sorted(payload["grid_rows"],
                     key=lambda r: (-_float(r["final_equity_rub"]), str(r["grid_id"])))
    return {
        "schema": payload["schema"], "issue": 143, "ref_issue": 139,
        "status": payload["status"], "generated_at": payload["generated_at"],
        "elapsed_sec": payload.get("elapsed_sec"),
        "anchor_parity_ok": bool(payload["anchor_parity"]["ok"]),
        "anchor_parity": {k: v for k, v in payload["anchor_parity"].items()
                          if k != "mismatch_examples"},
        "contract_ok": bool(payload["contract"]["ok"]),
        "contract": {k: v for k, v in payload["contract"].items() if k != "config"},
        "contract_failed": payload["contract"]["failed"],
        "n_grids": len(payload["grid_rows"]), "n_flows": payload["flows_count"],
        "base_grid": payload["baseline_grid_id"], "control_grid": payload["control_grid_id"],
        "best_grid": str(ordered[0]["grid_id"]), "worst_grid": str(ordered[-1]["grid_id"]),
        "equity_spread_rub": _round(max(eqs.values()) - min(eqs.values()), 0) if eqs else 0.0,
        "headline": [f"{f['n']}. {f['text'][:140]}" for f in payload["findings"]],
        "grid_rows": payload["grid_rows"], "stress_sensitivity": payload["stress_sensitivity"],
        "walk_forward": {k: v for k, v in payload["walk_forward"].items() if k != "windows"},
        "robustness": payload["robustness"], "concordance": payload["concordance"],
        "control_deltas": payload["control_deltas"], "flip_matrix": payload["flip_matrix"],
        "regime_concentration": payload["regime_concentration"],
        "environment": payload["environment"],
        "scope": {k: v for k, v in payload["scope"].items() if k not in ("flows", "extract")},
        "recommendations": payload["recommendations"],
        "next_issues": payload["next_issues"], "limitations": payload["limitations"],
        "artifacts": payload.get("artifacts") or {},
    }


WALK_COLS = ["grid_id", "window", "from", "to", "n_trades", "n_windows", "win_windows",
             "win_share", "pnl_rub", "final_equity_rub", "max_drawdown_pct",
             "profit_factor", "win_rate_pct", "game_over"]


def write_outputs(payload: dict[str, Any], results: dict[str, Any],
                  ctx: dict[str, Any]) -> dict[str, str]:
    """Артефакты stage=analyze: отчёты, таблицы, выходы по сеткам."""
    out = Path(ctx["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    grid_rows = payload["grid_rows"]
    artifacts = {
        "report_json": out / "report.json.gz", "summary_json": out / "summary.json",
        "report_md": out / "report.md", "grids_csv": out / "grids.csv",
        "walkforward_csv": out / "walkforward.csv", "exits_jsonl": out / "exits.jsonl.gz",
        "contract_json": out / "contract.json",
    }
    artifacts["run_md"] = out / "run.md"
    artifacts["extract_summary"] = out / "extract_summary.json"
    # Пути в индексируемых JSON — относительно корня репозитория, как в #139 (PR #155).
    payload["artifacts"] = {k: _rel(v) for k, v in sorted(artifacts.items())}
    write_json_gz(artifacts["report_json"], payload)
    write_json(artifacts["summary_json"], summary_block(payload))
    write_csv(artifacts["grids_csv"], grid_rows, list(grid_rows[0].keys()))
    write_csv(artifacts["walkforward_csv"], payload["walk_rows"],
              list(payload["walk_rows"][0].keys()) if payload["walk_rows"] else WALK_COLS)
    if results:
        write_exits_jsonl(artifacts["exits_jsonl"], results,
                          [str(r["grid_id"]) for r in grid_rows])
    elif not artifacts["exits_jsonl"].exists():
        LOG.warning("exits.jsonl.gz не записан: stage=report не пересчитывает решётку")
    write_text(artifacts["report_md"],
               render_md(payload, out, {k: Path(v) for k, v in artifacts.items()}))
    payload["artifacts"] = {k: _rel(v) for k, v in sorted(artifacts.items())}
    write_json(artifacts["summary_json"], summary_block(payload))
    return payload["artifacts"]


def render_run_md(meta: dict[str, Any], payload: dict[str, Any] | None) -> list[str]:
    """run.md — протокол прогона #143: окружение, счётчики, время, код возврата."""
    env = (payload or {}).get("environment") or meta.get("environment") or {}
    scope = (payload or {}).get("scope") or {}
    lines = [f"# #143 · Протокол прогона robustness-решётки трейлинга".rstrip(),
             f"Сгенерирован: {meta.get('generated_at')} (референс — #139)", ""]
    lines += ["## Окружение", "",
              f"- stage: `{meta.get('stage')}` · exit code: `{meta.get('exit_code')}`",
              f"- python `{env.get('python', '?')}` · platform `{env.get('platform', '?')}` "
              f"· BIOSIM_ENV `{env.get('biosim_env', '?')}`",
              f"- PostgreSQL (хост/база, без пароля): `{env.get('postgres_host', '?')}` "
              f"· доступ только read-only (SELECT)",
              f"- каталог #139: `{_rel(env.get('issue139_dir', '?'))}` · выходной каталог: "
              f"`{_rel(env.get('out_dir', '?'))}`", ""]
    hashes = {k: v for k, v in (env.get("issue139_sha") or {}).items() if v}
    if hashes:
        lines += ["## Отпечатки входных артефактов", "", "| артефакт | sha256[:16] |",
                  "| --- | --- |"]
        lines += [f"| `{name}` | `{sha}` |" for name, sha in sorted(hashes.items())]
        lines.append(f"| `grids.json` | `{env.get('grids_sha', '')}` |")
        lines.append("")
    lines += ["## Скоуп", ""]
    for key in ("tickers", "period", "max_path_bars", "workers", "n_tickers_cached",
                "n_candidates_ref", "full_scope", "path_truncated", "limit"):
        if key in scope:
            value = scope[key]
            shown = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) \
                else value
            lines.append(f"- `{key}` = {shown}")
    extract = scope.get("extract") or {}
    totals = (meta.get("extract_totals") or {})
    lines += [f"- кандидатов из книги #139: `{_int((payload or {}).get('flows_count'))}`",
              f"- тикеров с кэшем путей: `{_int(extract.get('paths_cached'))}`",
              f"- elapsed: `{meta.get('elapsed_sec')}` сек", ""]
    if payload:
        parity, contract = payload["anchor_parity"], payload["contract"]
        lines += ["## Итог", "",
                  f"- статус прогона: `{payload['status']}`",
                  f"- паритет с #139: "
                  f"`{'OK' if parity['ok'] else 'РАСХОЖДЕНИЕ'}` "
                  f"(проверено сделок: {parity['n_checked']}; по механике выхода: "
                  f"{parity['n_mismatch_exit']}; сверх допуска по доходности "
                  f"{TOL_RET_PP} п.п.: {parity['n_mismatch_return_drift']}; макс. |Δ доходности| = "
                  f"{_float(parity.get('max_abs_delta_net_return_pp'))} п.п.)",
                  f"- контракт конфигурации: "
                  f"`{'OK' if contract['ok'] else 'FAILED'}` "
                  f"(проверок {contract['n_checks']}, пройдено {contract['passed']})"
                  + (f"; провалено: {', '.join(contract['failed'])}"
                     if contract["failed"] else ""),
                  f"- сеток в решётке: {len(payload['grid_rows'])} · "
                  f"стресс-прогонов: {len(payload['stress_runs'])} · "
                  f"окон walk-forward: {payload['walk_forward']['n_windows']}", ""]
        if (parity.get("mismatch_examples") or [])[:5]:
            lines += ["### Примеры расхождений по сделкам", "",
                      "```json",
                      json.dumps(parity["mismatch_examples"][:5], ensure_ascii=False,
                                 indent=1),
                      "```", ""]
        if not contract["ok"]:
            lines += ["### Проваленные проверки контракта", ""]
            lines += [f"- `{c['name']}`: ожидалось {json.dumps(c['expected'], ensure_ascii=False)},"
                      f" получено {json.dumps(c['actual'], ensure_ascii=False)}"
                      for c in contract["checks"] if not c["ok"]]
            lines.append("")
    if totals:
        lines += ["### Извлечение путей", "", "```json",
                  json.dumps(totals, ensure_ascii=False, indent=1), "```", ""]
    if meta.get("error"):
        lines += ["## Ошибка прогона", "", "```text", str(meta["error"]), "```", ""]
    artifacts = dict((payload or {}).get("artifacts") or {})
    artifacts.setdefault("run_md", str(OUT_DIR / "run.md"))
    artifacts.setdefault("extract_summary", str(OUT_DIR / "extract_summary.json"))
    lines += ["## Артефакты", ""]
    lines += [f"- `{name}` → `{_rel(path)}`" for name, path in sorted(artifacts.items())]
    return lines


def stage_report(ctx: dict[str, Any]) -> dict[str, Any]:
    """Перерендер отчётов из report.json.gz — без БД и без пересчёта решётки."""
    path = Path(ctx["out_dir"]) / "report.json.gz"
    payload = read_json_gz(path)
    if not isinstance(payload, dict) or not payload:
        raise FileNotFoundError(f"Нет готового отчёта: {path}")
    # Статический текст отчёта живёт в коде: после правок ограничений или продолжений
    # перерендер обязан их обновить, иначе report.md останется старше run.py.
    payload["limitations"] = LIMITATIONS
    recs, next_issues = build_recommendations(payload)
    payload["recommendations"], payload["next_issues"] = recs, next_issues
    ctx.setdefault("out_dir", OUT_DIR)
    return payload


def build_parser() -> argparse.ArgumentParser:
    """CLI #143: stages all / extract / analyze / report."""
    parser = argparse.ArgumentParser(
        prog="issue-143-trailing-robustness",
        description="Robustness-решётка трейлинг-стопа (#143) поверх артефактов #139; "
                    "доступ к PostgreSQL только read-only.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Полный прогон (из корня репозитория, PowerShell):\n"
               "  $env:PYTHONIOENCODING='utf-8'\n"
               "  python analytics/issue-143-trailing-robustness/run.py --stage all\n"
               "Артефакты — в этом каталоге (report.md, run.md, summary.json); кэш путей — "
               "в cache/; лог прогона идёт в stdout, по постановке #143 его перенаправляют "
               "в reports/Vulpec/143_trailing-robustness/log.txt (каталог вне индекса git).")
    parser.add_argument("--stage", choices=("all", "extract", "analyze", "report"),
                        default="all", help="этап прогона (по умолчанию all)")
    parser.add_argument("--tickers", default="",
                        help="CSV-список тикеров; по умолчанию берётся из grids.json")
    parser.add_argument("--date-from", dest="date_from", default="",
                        help="начало периода (yyyy-mm-dd), по умолчанию из grids.json/#139")
    parser.add_argument("--date-to", dest="date_to", default="",
                        help="конец периода (yyyy-mm-dd), по умолчанию из grids.json/#139")
    parser.add_argument("--out-dir", dest="out_dir", default="",
                        help=f"каталог артефактов (по умолчанию {OUT_DIR})")
    parser.add_argument("--grids", default="",
                        help=f"путь к grids.json (по умолчанию {GRIDS_PATH})")
    parser.add_argument("--limit", type=int, default=0,
                        help="ограничить число сделок (отладка), 0 — без ограничения")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help="параллельных воркеров извлечения путей")
    parser.add_argument("--db-pool-max", dest="db_pool_max", type=int, default=8,
                        help="размер пула соединений БД (только SELECT)")
    parser.add_argument("--force-extract", dest="force_extract", action="store_true",
                        help="игнорировать кэш путей и пересобрать из БД")
    parser.add_argument("--verbose", action="store_true", help="DEBUG-логирование")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа #143: пишет только в OUT_DIR/CACHE_DIR, к БД — только SELECT."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", stream=sys.stdout)
    for noisy in ("matplotlib", "py.warnings"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    random.seed(143)
    started = time.time()
    meta: dict[str, Any] = {
        "stage": args.stage, "exit_code": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "argv": {k: v for k, v in vars(args).items() if k not in ("verbose",)},
    }
    payload: dict[str, Any] | None = None
    try:
        ref = load_ref139()
        grids = load_grids(Path(args.grids) if getattr(args, "grids", None) else GRIDS_PATH)
        ctx = run_context(ref, grids, args)
        meta["environment"] = env_block(ref, ctx)
        if args.stage == "extract":
            summary = stage_extract(ref, ctx, force=bool(args.force_extract))
            meta["extract_totals"] = summary["totals"]
            meta["exit_code"] = 0 if summary["status"] == "success" else 2
        elif args.stage == "report":
            payload = stage_report(ctx)
            write_outputs(payload, None, ctx)
            meta["exit_code"] = 0 if payload.get("status") == "success" else 2
        else:
            if args.stage == "all":
                summary = stage_extract(ref, ctx, force=bool(args.force_extract))
                meta["extract_totals"] = summary["totals"]
            payload, results = run_analyze(ref, grids, ctx, args)
            write_outputs(payload, results, ctx)
            meta["exit_code"] = 0 if payload["status"] == "success" else 2
    except KeyboardInterrupt:
        LOG.warning("провал stage=%s: прервано оператором", args.stage)
        meta["error"] = "KeyboardInterrupt"
    except Exception:  # noqa: BLE001 — прогон обязан оставить run.md
        LOG.exception("провал stage=%s", args.stage)
        meta["error"] = traceback.format_exc()[-2000:]
    meta["elapsed_sec"] = round(time.time() - started, 2)
    out_root = Path(args.out_dir) if getattr(args, "out_dir", None) else OUT_DIR
    try:
        out_root.mkdir(parents=True, exist_ok=True)
        write_text(out_root / "run.md", render_run_md(meta, payload))
    except OSError as exc:
        LOG.error("не удалось записать run.md: %s", exc)
    print(json.dumps({"stage": args.stage, "exit_code": meta["exit_code"],
                      "status": (payload or {}).get("status"),
                      "elapsed_sec": meta["elapsed_sec"], "out_dir": str(out_root)},
                     ensure_ascii=False))
    return int(meta["exit_code"])


if __name__ == "__main__":
    # Windows-консоль в OEM-кодировке иначе падает на кириллице при перенаправлении лога.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
