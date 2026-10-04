# Important Notes

> **Source:** project-context.md sections 9
> **Last refreshed:** 2026-10-04, task-346

## 9. Important Notes

- **Sandbox mode**: no real trading. T-Bank API sandbox tokens.
- **Secrets**: .env (`TINVEST_TOKEN` / `TINVEST_ACC` for market data, `TINVEST_SANDBOX` / optional `TINVEST_SANDBOX_ACC` for sandbox execution, `TGM_TOKEN` / `TGM_CHAT` for Telegram, `PSTGRS_PWD`). Never log secrets or reuse market-data credentials for trading.
- **Docker**: rebuild backend image after code changes (`docker compose up -d --build backend`). Backend mounts `./reports` (for last_run.json).
- **Single source of truth**: trading universe + strategy definitions live in `trading_config.py` / `trading.trading_universe`. Do not hardcode ticker lists or strategy params in modules.
- **Locked strategy**: the strategy under paper test has `locked=true`; the API rejects overwriting it (409). Unlock only after the test period.
- **Logging**: DBManager logs to stdout by default. Reroute to stderr in scripts that parse JSON from stdout. Background processes (start_processes.sh) use `python -u` + `logging.basicConfig(level=INFO, stream=sys.stdout)` for unbuffered logging to log files.
