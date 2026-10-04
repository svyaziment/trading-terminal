# Эксплуатация live-вселенной

> **Source:** handover.ru.md sections 18, 31, 32
> **Last refreshed:** 2026-10-04, task-346

## 18. Эксплуатация live-вселенной

- Рейтинг paper остаётся `get_trading_universe()` (top-15 из `trading.trading_universe`). Таблицу не сужать.
- Streaming и data refresh используют `get_streaming_universe()` = top-15 ∪ `LIVE_UNIVERSE`.
- Sandbox-исполнение использует `LIVE_UNIVERSE` / `get_live_trading_universe()`: ROSN, IRAO, AFKS, NVTK, SBER, MTSS, PHOR, MOEX, FLOT, FEES, GAZP, PLZL (PO-список задачи #135 плюс 2026-09-02). Геттер **не** обрезает имена вне paper top-15. `LiveExecutor.initialize()` пересекает тикеры paper-стратегии с этим списком.
- Исторический рейтинг задачи #66 (SBER, LKOH, RUAL, NVTK, GAZP) живёт в `analytics/issue-66-live-universe/`. Не переписывать тот пакет под текущий PO-список.
- Имя locked paper для preflight: `EXPECTED_LOCKED_STRATEGY` = `test_20260830_new_level`.
- Тесты: `cd backend && python -m pytest -q tests/test_trading_config.py tests/test_live_universe_analysis.py tests/test_live_executor.py`.

## 31. Эксплуатация isolated-вселенной поддержки с трекером

- Пакет: `analytics/issue-129-sr-support-universe/`. Изолированные 28 тикеров Lab (`get_big_tickers`), тот же период, что #124. Не live top-5 и не `run_params.tickers`.
- C = только `levels_sr_support` + `signal_4h_buy` (SHA `3b7864c4de2cb2c7d271be8c21c7d99c29bfd8a7dd05980b3c5497b6b2aedb1b`). Движок `run_strategy_backtest`, на сделках есть `source=levels_sr_support`.
- Exclusive B-support в #124 — **подпись композита** (путь B забирает dual-бар и занимает единственный слот). Isolated C — **runnable** книга только поддержки: n=4380 PF 1.45. Не считать exclusive 3811 / 1.51 bit-for-bit совпадением с C.
- Extra 611 vs exclusive: 610 occupancy (C входит, пока композит в сделке пути B), leftover 1 (PHOR `2026-08-14 14:48`). Missing 42: cascade (extra C занимает слот, поздняя B-support не стреляет). Extra PF 0.95 — isolated extras хуже exclusive 1.51.
- AFKS: C 89 / 1.49; exclusive 78 ⊆ C; не mix 116 / 1.46. Бар ALRS `2026-08-20 11:50:24` @ 19.80 заблокирован. Resistance-source n=0.
- Задача #130 должна брать C (4380 / 1.45), не exclusive 3811 / 1.51. Не смешивать isolated PF с портфелем 50k. Портфельный пакет: handover §32.
- Не lock/paper-flag и не overwrite `test_20260731`, `test_20260820`, `test_20260821`.
- Повтор без нового бэктеста: `python analytics/issue-129-sr-support-universe/analysis.py`. Полный прогон: `python analytics/issue-129-sr-support-universe/extract_inputs.py` (резюмируется).
- Unit: `cd backend && python -m pytest -q tests/test_issue129_analysis.py`.

## 32. Эксплуатация портфеля поддержки с трекером

- Пакет: `analytics/issue-130-sr-support-portfolio/`. Replay слотов isolated C из #129, те же 28 имён / volume-order, что #103/#44. Не live top-5.
- C = только `levels_sr_support` + `signal_4h_buy` (SHA `3b7864c4de2cb2c7d271be8c21c7d99c29bfd8a7dd05980b3c5497b6b2aedb1b`). Кандидаты из published #129 `results.json` через `run_strategy_backtest` (`source=levels_sr_support`). Не фильтровать #124 B-mix по `source`.
- Слоты: 50 000 RUB / 10 000 / max 5. Период `2024-08-01` … `timestamp < 2026-08-21`. Дневная equity — по закрытиям, без mark-to-market.
- Опубликованная книга C: n=3237 PF 1.33 equity 96 204.63 daily Max DD 6.08% event Max DD 6.98% skipped 1143, без GAME OVER. Isolated C остаётся 4380 / 1.45 — эти PF не смешивать.
- Сравнение (другие книги, не замены): #44 equity 96 343.49 n=3500 PF 1.31; #103 equity 89 055.31 n=2070 PF 1.34; #124 B-mix equity 98 432.94 n=2837 PF 1.32 (кандидаты support+resistance).
- Бар ALRS `2026-08-20 11:50:24` @ 19.80 отсутствует среди candidates и портфельных входов. Resistance-source n=0.
- Вердикт: не paper (по умолчанию без явного решения PO). Не lock/paper-flag и не overwrite `test_20260731`, `test_20260820`, `test_20260821`. Черновик Lab при необходимости: `test_YYYYMMDD_sr_support`.
- Повтор без нового бэктеста: `python analytics/issue-130-sr-support-portfolio/analysis.py`. JSON слотов: `python analytics/issue-130-sr-support-portfolio/generate_inputs.py --source 129`. Notebook: `python analytics/issue-130-sr-support-portfolio/build_notebook.py --execute`.
- Unit: `cd backend && python -m pytest -q tests/test_issue130_analysis.py`.
