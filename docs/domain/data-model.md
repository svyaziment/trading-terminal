# Domain Data Model

## Existing Market Data Entities

### Instrument

Source table:

    trading.instruments

Important fields:

- figi
- ticker
- name
- instrument_type
- class_code
- currency
- min_price_increment
- lot_size
- is_tradable
- isin
- exchange
- country_of_risk
- country_of_risk_name

### Candle

Source tables:

    trading.candles
    trading.candles_30min_raw
    trading.candles_aggregated

Recommended canonical fields:

- instrument_id
- figi
- ticker
- timeframe
- timestamp
- open
- high
- low
- close
- volume
- source
- created_at

### Indicator

Source table:

    trading.indicators

Examples:

- sma_5
- sma_20
- ema_12
- rsi_14
- macd
- atr_14
- bb_upper
- bb_middle
- bb_lower

### Signal

Source table:

    trading.signals

Fields:

- ticker
- timeframe
- timestamp
- signal
- confidence
- price
- summary
- buy_signals
- sell_signals
- total_signals

## Future Terminal Entities

### Order

Fields:

- id
- account_id
- instrument_id
- client_order_id
- broker_order_id
- direction
- order_type
- quantity_lots
- price
- status
- source
- approved_by
- created_at
- updated_at

### OrderExecution

Fields:

- id
- order_id
- broker_execution_id
- quantity_lots
- price
- commission
- executed_at

### Position

Fields:

- id
- account_id
- instrument_id
- quantity_lots
- average_price
- current_price
- unrealized_pnl
- updated_at

### RiskLimit

Fields:

- id
- account_id
- max_order_amount
- max_daily_loss
- max_open_orders
- max_orders_per_minute
- market_orders_enabled
- margin_trading_enabled
- trading_enabled

### AuditLog

Fields:

- id
- user_id
- agent_id
- action
- entity_type
- entity_id
- payload
- created_at
