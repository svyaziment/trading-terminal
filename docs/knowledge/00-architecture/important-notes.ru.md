# Важные замечания

> **Source:** project-context.ru.md sections 9
> **Last refreshed:** 2026-10-04, task-346

## 9. Важные замечания

- **Песочница**: реальной торговли нет. Используются sandbox-токены T-Bank API.
- **Секреты**: .env (`TINVEST_TOKEN` / `TINVEST_ACC` для рыночных данных, `TINVEST_SANDBOX` / необязательный `TINVEST_SANDBOX_ACC` для sandbox-исполнения, `TGM_TOKEN` / `TGM_CHAT` для Telegram, `PSTGRS_PWD`). Никогда не логировать секреты и не использовать реквизиты рыночных данных для торговли.
- **Docker**: после изменений кода пересоберите backend image (`docker compose up -d --build backend`). Backend монтирует `./reports` (для last_run.json).
- **Единый источник истины**: торговая вселенная + определения стратегий живут в `trading_config.py` / `trading.trading_universe`. Не хардкодьте списки тикеров или параметры стратегий в модулях.
- **Заблокированная стратегия**: стратегия в paper test имеет `locked=true`; API отклоняет её перезапись (409). Разблокировать только после тестового периода.
- **Логирование**: DBManager по умолчанию пишет в stdout. В скриптах, которые парсят JSON из stdout, перенаправьте логи в stderr. Фоновые процессы (start_processes.sh) используют `python -u` + `logging.basicConfig(level=INFO, stream=sys.stdout)` для немедленной записи логов в файлы.
