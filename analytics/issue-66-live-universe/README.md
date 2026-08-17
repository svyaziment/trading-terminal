# Выбор топ-5 тикеров для live trading

Опубликованный результат [Issue #66](https://github.com/svyaziment/trading-terminal/issues/66)
в эпике [Live Trading Infrastructure #58](https://github.com/svyaziment/trading-terminal/issues/58).

## Цель

Выбрать финальную sandbox-вселенную из пяти тикеров для `LiveExecutor`
по paper trading, бэктесту locked-стратегии, ликвидности и ATR.

## Результат

`SBER`, `LKOH`, `RUAL`, `NVTK`, `GAZP`.

Список зафиксирован в `backend/app/analytics/trading_config.py` как `LIVE_UNIVERSE`.
Paper trading и `data_refresher` продолжают использовать полный top-15
`trading.trading_universe`.

## Состав

| Файл | Назначение |
|---|---|
| `extract_inputs.py` | Снимок БД в `inputs.json` (без секретов и без массивов trades) |
| `analysis.py` | Расчёт фильтра, score, отчёта и графиков |
| `inputs.json` | Воспроизводимый срез на 2026-08-17 |
| `summary.json` | Машиночитаемый топ-5 |
| `report.md` | Аналитический отчёт |
| `plots/*.png` | Score, PF/спред, ATR/оборот, покрытие paper |

## Запуск

Из корня репозитория, если PostgreSQL доступен:

```bash
python analytics/issue-66-live-universe/extract_inputs.py
python analytics/issue-66-live-universe/analysis.py
```

Повтор отчёта по уже сохранённому снимку не требует БД:

```bash
python analytics/issue-66-live-universe/analysis.py
```
