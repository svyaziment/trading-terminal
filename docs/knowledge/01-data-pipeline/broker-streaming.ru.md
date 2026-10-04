# Интеграция T-Bank Sandbox API

> **Source:** project-context.ru.md sections 10
> **Last refreshed:** 2026-10-04, task-346

## 10. Интеграция T-Bank Sandbox API

`backend/app/broker/tinkoff_sandbox.py` — граница исполнения ордеров для эпика #58. `TinkoffSandboxClient` подключается к отдельному endpoint `INVEST_GRPC_API_SANDBOX`, использует только `client.sandbox` и никогда не обращается к production-сервису `orders`. Клиент предоставляет:
- `execute_order` для market- и limit-ордеров (количество задаётся в лотах);
- `check_balance` для проверки свободных денег по валюте;
- `get_positions` для получения ненулевых открытых позиций портфеля;
- `cancel_order` для отмены активного sandbox-ордера.

Операционная политика централизована в `analytics/trading_config.py` (`SANDBOX_TRADING`): включение sandbox, жёсткий запрет реальной торговли, справочный начальный капитал, валюта по умолчанию, число retry/backoff и обнаружение счёта. Секреты там не хранятся: отдельные реквизиты `TINVEST_SANDBOX` / `TINVEST_SANDBOX_ACC` загружаются через `core/config_manager.py`; `TINVEST_TOKEN` / `TINVEST_ACC` используются только для рыночных данных.

При временных gRPC-ошибках (`UNAVAILABLE`, `RESOURCE_EXHAUSTED`, `DEADLINE_EXCEEDED`, `INTERNAL`) используется экспоненциальная задержка. Повтор ордера сохраняет один idempotency `order_id`, поэтому неопределённый ответ не приводит к дублирующему ордеру. Если `TINVEST_SANDBOX_ACC` пуст или некорректен (`50004`), клиент выбирает первый открытый sandbox-счёт и кеширует его id; счета и деньги автоматически не создаются.

Live-проверка 2026-08-16: оператор открыл sandbox-счёт и пополнил его на 50 000 RUB. `TinkoffSandboxClient` успешно прочитал баланс и позиции, выставил limit-ордер SBER на один лот и отменил его.
