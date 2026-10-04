# Первая canary-сессия sandbox LiveExecutor

> **Source:** handover.ru.md sections 19
> **Last refreshed:** 2026-10-04, task-346

## 19. Первая canary-сессия sandbox LiveExecutor

Используйте окно 60-120 минут во время торговой сессии MOEX, предпочтительно с 10:00 до 16:00 МСК. Цель — проверить цепочку исполнения, а не доходность. Для canary запрещено использовать историческое значение по умолчанию `DURATION_MINUTES=1200`.

1. После слияния изменений live-вселенной и логов отказов пересоберите backend: `docker compose up -d --build backend`. Убедитесь, что `http://localhost:8000/health` возвращает `status=ok`.
2. Оставьте запущенными ровно по одному процессу `data_refresher`, `online_data`, `live_engine` и `paper_trader`. Не останавливайте paper trading ради canary. Если они не запущены, заранее поднимите обычный paper-стек с явной длительностью, покрывающей окно canary.
3. Непосредственно перед executor выполните read-only preflight:
   `docker compose exec -T backend python -m app.analytics.live_executor_preflight`.
   Проверка завершится ошибкой, если backend нездоров, `LIVE_UNIVERSE` отличается от SBER/LKOH/RUAL/NVTK/GAZP, единственная locked paper-стратегия — не `test_20260731`, нет свободных sandbox RUB, хотя бы один из пяти стаканов старше пяти минут, paper-процессы запущены не в единственном экземпляре, в `trading.trading_universe` не 15 строк или `allow_real_trading` не равен false.
4. Запустите executor на один час без перезапуска paper-процессов:
   `START_LIVE_EXECUTOR=1 PRESERVE_PAPER_PROCESSES=1 DURATION_MINUTES=60 ./start_processes.sh`.
   Режим `PRESERVE_PAPER_PROCESSES=1` аварийно завершит запуск, если не обнаружит ровно по одному экземпляру каждого из четырёх paper-процессов.
5. За первые пять минут в `reports/live-executor/executor.log` должна появиться строка `Sandbox LiveExecutor started` с числом инициализированных тикеров. Строки отказов появляются только после BUY-решения; их отсутствие может означать, что `StrategyEvaluator` не создал BUY. Также следите за вкладкой Live Trading и `trading.live_positions`.
6. По истечении указанного времени executor остановится автоматически. Для досрочной остановки отправьте SIGTERM только процессу, команда которого содержит `LiveExecutor`; не перезапускайте весь paper-стек. При `close_positions_on_shutdown=false` позиции в sandbox останутся открытыми, а ожидающие/protection-ордера будут отменены и их IDs очищены в БД — сразу проверьте оставшиеся позиции.
7. Длительность paper-стека должна покрывать canary с запасом. Если paper-процесс истечёт во время сессии, перезапустите только четыре paper-воркера; не вызывайте полный `start_processes.sh`, потому что шаг 0 убьёт `LiveExecutor`.

Полезные read-only SQL:

```sql
SELECT id, name, in_paper_test, locked
FROM trading.strategies
WHERE in_paper_test=true AND locked=true;

SELECT ticker, max(timestamp) AS latest_orderbook
FROM trading.online_orderbook_aggregates
WHERE ticker IN ('SBER','LKOH','RUAL','NVTK','GAZP')
GROUP BY ticker ORDER BY ticker;

SELECT count(*) AS universe_size
FROM trading.trading_universe;

SELECT timestamp, equity_rub
FROM trading.paper_equity
ORDER BY timestamp DESC LIMIT 2;
```

После остановки зафиксируйте в Issue #74: инициализированные тикеры из startup log; количество отказов по каждому `reason`; число исполненных BUY; последние позиции из запроса ниже; подтверждение, что `paper_equity` обновлялась в том же интервале.

```sql
SELECT ticker, status, size_lots, entry_price, broker_order_id
FROM trading.live_positions
ORDER BY id DESC LIMIT 20;
```

Не меняйте locked-стратегию, RR, порог imbalance и `trading.trading_universe`. Никогда не включайте `allow_real_trading`; запуск выполняется только в sandbox.
