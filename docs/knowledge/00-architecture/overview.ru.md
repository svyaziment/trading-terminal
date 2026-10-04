# Обзор проекта

> **Source:** project-context.ru.md sections 1
> **Last refreshed:** 2026-10-04, task-346

## 1. Обзор проекта

Торговый терминал по акциям MOEX. Песочница (без реальной торговли). Стек: FastAPI backend (Python 3.12), React frontend (Vite + Tailwind + lightweight-charts), PostgreSQL (external, через host.docker.internal из Docker). Рыночные данные: T-Bank Invest API (gRPC, sandbox) + MOEX ISS API (REST, 1min свечи).

Три опоры:
1. **Backtest / Strategy Lab** - параметризуемый движок стратегий (AND-паттерны, multi-window confirmation, комиссия/слиппедж/RR, depth presets, bootstrap) + walk-forward validation, доступен через API и UI-конструктор.
2. **Paper trading** - активная стратегия из Strategy Lab (таблица `strategies`, `in_paper_test=true AND locked=true`) торгует виртуально через единый `StrategyEvaluator` (общий мозг с бэктестом). Текущая: `test_20260830_new_level` (levels_sr_support + signal_4h_buy, RR 1:3, confirm 10min, 28 Lab-тикеров). Предыдущая locked-строка `test_20260731` разблокирована и оставлена как reference. Один arm: market вход, window mode (7-19 MSK), RR из конфига.

**Strategy Plugin System (Эпик #39):** стратегии подключаемы через `StrategyPlugin` ABC в `strategies/`. Зарегистрированные плагины: `levels_reversal` (обёртка над `StrategyEvaluator`), `atr_reversal` (ATR reversal Звездина). `portfolio_simulator.py` предоставляет backtest общего капитала (50k RUB, 10k слоты, макс 5 позиций, приоритет слотов по объёму, GAME OVER при cash<=0).
3. **Frontend dashboards** - Signals, Strategy Lab (backtest constructor), Paper Trading (A/B monitoring с фильтрами факторов + PnL chart).
