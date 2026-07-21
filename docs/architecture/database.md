# Database Architecture

## Overview

The project uses PostgreSQL.

There are two logical database areas:

1. Market Data / Analytics schema: `trading`
2. Terminal / Operations schema: `terminal`

## Schema: trading

This schema already exists and contains market data and analytics:

- instruments
- candles
- candles_30min_raw
- candles_aggregated
- indicators
- signals
- top_stocks_by_volume

The backend should initially use this schema in read-only mode.

## Schema: terminal

This schema will contain terminal-specific operational data:

- users
- accounts
- broker_connections
- orders
- order_executions
- positions
- portfolio_snapshots
- risk_limits
- risk_checks
- trading_controls
- audit_logs
- strategy_signals

## Conventions

1. Prices use numeric types, not float.
2. Volumes use bigint.
3. Timestamps should preferably use timestamp with time zone.
4. FIGI is an important business identifier.
5. Internal foreign keys should use surrogate IDs.
6. Timeframes should be standardized.
7. Date-suffixed tables should be avoided.
8. Reports should use one table with report_date.

## Access Pattern

API backend:
- async access via SQLAlchemy / asyncpg
- read-only access to trading schema
- read/write access to terminal schema

Analytics/ETL workers:
- may use sync psycopg2 + pandas
- may perform bulk inserts
- may calculate indicators and signals
