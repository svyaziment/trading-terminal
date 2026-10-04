# T-Bank Sandbox API Integration

> **Source:** project-context.md sections 10
> **Last refreshed:** 2026-10-04, task-346

## 10. T-Bank Sandbox API Integration

`backend/app/broker/tinkoff_sandbox.py` is the execution boundary for Epic #58. `TinkoffSandboxClient` connects to the dedicated `INVEST_GRPC_API_SANDBOX` endpoint and uses only `client.sandbox`; it never calls the production `orders` service. It provides:
- `execute_order` for market and limit orders (quantity is in lots);
- `check_balance` for free cash by currency;
- `get_positions` for non-zero open portfolio positions;
- `cancel_order` for active sandbox orders.

Operational policy is centralized in `analytics/trading_config.py` (`SANDBOX_TRADING`): sandbox enablement, hard prohibition of real trading, initial capital reference, default currency, retry count/backoff, and account discovery. Secrets are not stored there: dedicated `TINVEST_SANDBOX` / `TINVEST_SANDBOX_ACC` credentials are loaded through `core/config_manager.py`; `TINVEST_TOKEN` / `TINVEST_ACC` are reserved for market data.

Transient gRPC failures (`UNAVAILABLE`, `RESOURCE_EXHAUSTED`, `DEADLINE_EXCEEDED`, `INTERNAL`) use exponential backoff. Order retries reuse the same idempotency `order_id`, preventing duplicate execution after an uncertain response. If `TINVEST_SANDBOX_ACC` is empty or invalid (`50004`), the client falls back to the first open sandbox account and caches its id; it does not create or fund accounts automatically.

Live verification on 2026-08-16: an operator opened a sandbox account and funded it with 50,000 RUB. `TinkoffSandboxClient` successfully read the balance and positions, submitted a one-lot SBER limit order, and cancelled that order.
