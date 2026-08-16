CREATE SCHEMA IF NOT EXISTS trading;

CREATE TABLE IF NOT EXISTS trading.live_positions (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(32) NOT NULL,
    instrument_id VARCHAR(128) NOT NULL,
    signal_ts TIMESTAMP NOT NULL,
    entry_price NUMERIC(20, 9) NOT NULL,
    lot_size INTEGER NOT NULL CHECK (lot_size > 0),
    size_lots INTEGER NOT NULL CHECK (size_lots > 0),
    stop_price NUMERIC(20, 9) NOT NULL,
    take_price NUMERIC(20, 9) NOT NULL,
    broker_order_id VARCHAR(128) NOT NULL UNIQUE,
    broker_stop_id VARCHAR(128),
    broker_take_id VARCHAR(128),
    status VARCHAR(32) NOT NULL
        CHECK (status IN (
            'pending',
            'open',
            'closed_stop',
            'closed_take',
            'cancelled'
        )),
    strategy_name VARCHAR(255) NOT NULL,
    exit_ts TIMESTAMP,
    exit_price NUMERIC(20, 9),
    exit_reason VARCHAR(64),
    pnl_rub NUMERIC(20, 2),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_live_positions_active
    ON trading.live_positions (status, ticker);
