# Database Schema: trading

Scan time: 2026-07-25T12:11:33.896809
DB_SCHEMA_STATUS=success

## Tables

| Table | Rows | Columns |
|---|---|---|
| backtest_equity | 442286 | 5 |
| backtest_metrics | 6081 | 14 |
| backtest_runs | 105 | 12 |
| backtest_trades | 442286 | 21 |
| candles | 3326 | 10 |
| candles_1min_raw | 3062661 | 9 |
| candles_30min_raw | 30834 | 9 |
| candles_aggregated | 133468 | 10 |
| indicators | 69908 | 33 |
| instruments | 4272 | 15 |
| signals | 52910 | 19 |
| signals_backup_task_031e | 6648 | 19 |
| top_stocks_by_volume | 30 | 12 |

## Column details

### backtest_equity

| Column | Type | Nullable |
|---|---|---|
| id | integer | NO |
| run_id | integer | NO |
| ts | timestamp without time zone | NO |
| equity_rub | numeric | NO |
| drawdown_pct | numeric | YES |

### backtest_metrics

| Column | Type | Nullable |
|---|---|---|
| id | integer | NO |
| run_id | integer | NO |
| group_key | text | NO |
| n_trades | integer | NO |
| win_rate | numeric | YES |
| profit_factor | numeric | YES |
| expectancy | numeric | YES |
| sharpe | numeric | YES |
| sortino | numeric | YES |
| max_drawdown | numeric | YES |
| avg_bars_held | numeric | YES |
| reliable | boolean | NO |
| benchmark_buyhold_return_pct | numeric | YES |
| benchmark_random_return_pct | numeric | YES |

### backtest_runs

| Column | Type | Nullable |
|---|---|---|
| id | integer | NO |
| strategy_name | text | NO |
| params | jsonb | NO |
| universe_snapshot | jsonb | YES |
| selection_bias | boolean | NO |
| git_hash | text | YES |
| started_at | timestamp without time zone | YES |
| finished_at | timestamp without time zone | YES |
| status | text | YES |
| total_trades | integer | YES |
| note | text | YES |
| created_at | timestamp without time zone | YES |

### backtest_trades

| Column | Type | Nullable |
|---|---|---|
| id | integer | NO |
| run_id | integer | NO |
| ticker | text | NO |
| figi | text | YES |
| timeframe | text | NO |
| signal_id | integer | YES |
| pattern_name | text | YES |
| side | text | NO |
| entry_ts | timestamp without time zone | NO |
| entry_price | numeric | NO |
| exit_ts | timestamp without time zone | NO |
| exit_price | numeric | NO |
| exit_reason | text | NO |
| bars_held | integer | NO |
| gross_return_pct | numeric | YES |
| commission_pct | numeric | YES |
| slippage_pct | numeric | YES |
| net_return_pct | numeric | YES |
| pnl_rub | numeric | YES |
| lot_size | integer | YES |
| min_price_increment | numeric | YES |

### candles

| Column | Type | Nullable |
|---|---|---|
| ticker | character varying | YES |
| figi | character varying | YES |
| timestamp | timestamp without time zone | YES |
| open | double precision | YES |
| high | double precision | YES |
| low | double precision | YES |
| close | double precision | YES |
| volume | bigint | YES |
| interval | character varying | YES |
| created_at | timestamp without time zone | YES |

### candles_1min_raw

| Column | Type | Nullable |
|---|---|---|
| ticker | text | NO |
| figi | text | YES |
| timestamp | timestamp without time zone | NO |
| open | numeric | NO |
| high | numeric | NO |
| low | numeric | NO |
| close | numeric | NO |
| volume | bigint | YES |
| created_at | timestamp without time zone | YES |

### candles_30min_raw

| Column | Type | Nullable |
|---|---|---|
| ticker | character varying | NO |
| figi | character varying | YES |
| timestamp | timestamp without time zone | NO |
| open | numeric | YES |
| high | numeric | YES |
| low | numeric | YES |
| close | numeric | YES |
| volume | bigint | YES |
| created_at | timestamp without time zone | YES |

### candles_aggregated

| Column | Type | Nullable |
|---|---|---|
| ticker | character varying | NO |
| figi | character varying | YES |
| timestamp | timestamp without time zone | NO |
| timeframe | character varying | NO |
| open | numeric | YES |
| high | numeric | YES |
| low | numeric | YES |
| close | numeric | YES |
| volume | bigint | YES |
| created_at | timestamp without time zone | YES |

### indicators

| Column | Type | Nullable |
|---|---|---|
| ticker | character varying | NO |
| figi | character varying | NO |
| timeframe | character varying | NO |
| timestamp | timestamp without time zone | NO |
| sma_5 | numeric | YES |
| sma_10 | numeric | YES |
| sma_20 | numeric | YES |
| sma_50 | numeric | YES |
| sma_100 | numeric | YES |
| sma_200 | numeric | YES |
| ema_12 | numeric | YES |
| ema_26 | numeric | YES |
| ema_50 | numeric | YES |
| rsi_14 | numeric | YES |
| rsi_21 | numeric | YES |
| macd | numeric | YES |
| macd_signal | numeric | YES |
| macd_histogram | numeric | YES |
| atr_14 | numeric | YES |
| bb_upper | numeric | YES |
| bb_middle | numeric | YES |
| bb_lower | numeric | YES |
| bb_width | numeric | YES |
| bb_position | numeric | YES |
| volume_sma_20 | numeric | YES |
| volume_ratio | numeric | YES |
| created_at | timestamp without time zone | YES |
| updated_at | timestamp without time zone | YES |
| open | numeric | YES |
| high | numeric | YES |
| low | numeric | YES |
| close | numeric | YES |
| volume | bigint | YES |

### instruments

| Column | Type | Nullable |
|---|---|---|
| figi | character varying | NO |
| ticker | character varying | NO |
| name | character varying | YES |
| instrument_type | character varying | YES |
| class_code | character varying | YES |
| currency | character varying | YES |
| min_price_increment | numeric | YES |
| lot_size | integer | YES |
| is_tradable | boolean | YES |
| isin | character varying | YES |
| exchange | character varying | YES |
| country_of_risk | character varying | YES |
| country_of_risk_name | character varying | YES |
| created_at | timestamp without time zone | YES |
| updated_at | timestamp without time zone | YES |

### signals

| Column | Type | Nullable |
|---|---|---|
| id | integer | NO |
| ticker | character varying | NO |
| timeframe | character varying | NO |
| timestamp | timestamp without time zone | NO |
| signal | character varying | NO |
| confidence | numeric | YES |
| price | numeric | YES |
| rsi | numeric | YES |
| macd | numeric | YES |
| bb_position | numeric | YES |
| volume_ratio | numeric | YES |
| atr_pct | numeric | YES |
| summary | text | YES |
| buy_signals | integer | YES |
| sell_signals | integer | YES |
| total_signals | integer | YES |
| created_at | timestamp without time zone | YES |
| pattern_name | text | YES |
| figi | character varying | YES |

### signals_backup_task_031e

| Column | Type | Nullable |
|---|---|---|
| id | integer | YES |
| ticker | character varying | YES |
| timeframe | character varying | YES |
| timestamp | timestamp without time zone | YES |
| signal | character varying | YES |
| confidence | numeric | YES |
| price | numeric | YES |
| rsi | numeric | YES |
| macd | numeric | YES |
| bb_position | numeric | YES |
| volume_ratio | numeric | YES |
| atr_pct | numeric | YES |
| summary | text | YES |
| buy_signals | integer | YES |
| sell_signals | integer | YES |
| total_signals | integer | YES |
| created_at | timestamp without time zone | YES |
| pattern_name | text | YES |
| figi | character varying | YES |

### top_stocks_by_volume

| Column | Type | Nullable |
|---|---|---|
| rank | integer | NO |
| report_date | date | NO |
| ticker | character varying | NO |
| figi | character varying | NO |
| name | character varying | YES |
| sum_volume | bigint | NO |
| candle_count | integer | NO |
| first_date | date | YES |
| last_date | date | YES |
| period_start | date | NO |
| period_end | date | NO |
| created_at | timestamp without time zone | YES |
