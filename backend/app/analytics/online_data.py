"""
Online data layer: streaming 1min candles + order book via T-Bank MarketDataServerSideStream.
Top-5 tickers (RUAL, GMKN, PIKK, GAZP, SIBN) by figi.
Writes: online_candles_1min (closed 1min candles), online_orderbook_aggregates (per-minute aggregates).
Handles ping keepalive and reconnect on stream error.
"""
from __future__ import annotations
import time
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

try:
    from t_tech.invest import Client
    from t_tech.invest.constants import INVEST_GRPC_API
    IS_SDK_AVAILABLE = True
except ImportError:
    Client = None
    INVEST_GRPC_API = None
    IS_SDK_AVAILABLE = False

from app.db.db_manager import DBManager
from app.core.config_manager import load_settings

logger = logging.getLogger(__name__)
from app.analytics.trading_config import get_trading_universe

TOP5_TICKERS = ['RUAL', 'GMKN', 'PIKK', 'GAZP', 'SIBN']
ORDERBOOK_DEPTH = 10
RECONNECT_DELAY = 5  # seconds


def money_to_float(money_value) -> Optional[float]:
    if money_value is None:
        return None
    return float(money_value.units) + money_value.nano / 1e9


def get_figi_map(db: DBManager, tickers: List[str]) -> Dict[str, str]:
    df = db.select("""
        SELECT ticker, figi FROM trading.instruments
        WHERE ticker = ANY(%s) AND figi IS NOT NULL
    """, (tickers,)).to_dataframe()
    return dict(zip(df['ticker'], df['figi'])) if not df.empty else {}


def save_candle(db: DBManager, ticker: str, candle):
    """Save a closed 1min candle to online_candles_1min."""
    ts = getattr(candle, 'time', None)
    if ts is None:
        return
    row = {
        'ticker': ticker,
        'timestamp': pd.to_datetime(ts, utc=True).tz_convert('Europe/Moscow').tz_localize(None),
        'open': money_to_float(getattr(candle, 'open', None)),
        'high': money_to_float(getattr(candle, 'high', None)),
        'low': money_to_float(getattr(candle, 'low', None)),
        'close': money_to_float(getattr(candle, 'close', None)),
        'volume': getattr(candle, 'volume', None),
        'source': 'streaming',
    }
    try:
        db.insert_with_schema('online_candles_1min', pd.DataFrame([row]))
    except Exception as e:
        logger.error(f"save_candle error {ticker}: {e}")


def save_orderbook_aggregate(db: DBManager, ticker: str, orderbook):
    """Compute aggregates from order book and save to online_orderbook_aggregates."""
    bids = getattr(orderbook, 'bids', []) or []
    asks = getattr(orderbook, 'asks', []) or []
    if not bids or not asks:
        return
    best_bid = money_to_float(bids[0].price) if bids else None
    best_ask = money_to_float(asks[0].price) if asks else None
    if best_bid is None or best_ask is None:
        return
    spread = best_ask - best_bid
    mid_price = (best_bid + best_ask) / 2
    spread_pct = (spread / mid_price * 100) if mid_price > 0 else 0
    bid_depth = sum(getattr(b, 'quantity', 0) for b in bids[:ORDERBOOK_DEPTH])
    ask_depth = sum(getattr(a, 'quantity', 0) for a in asks[:ORDERBOOK_DEPTH])
    volume_imbalance = (bid_depth / ask_depth) if ask_depth > 0 else 0
    row = {
        'ticker': ticker,
        'timestamp': datetime.now(timezone.utc).replace(tzinfo=None),
        'best_bid': best_bid,
        'best_ask': best_ask,
        'spread': spread,
        'spread_pct': spread_pct,
        'mid_price': mid_price,
        'bid_depth': bid_depth,
        'ask_depth': ask_depth,
        'volume_imbalance': volume_imbalance,
        'levels_count': min(len(bids), len(asks), ORDERBOOK_DEPTH),
    }
    # Per-minute upsert: round timestamp to minute, update on conflict (one row per ticker per minute)
    row['timestamp'] = pd.Timestamp.now(timezone.utc).tz_convert('Europe/Moscow').tz_localize(None).floor('min')
    try:
        db.execute("""
            INSERT INTO trading.online_orderbook_aggregates
                (ticker, timestamp, best_bid, best_ask, spread, spread_pct, mid_price,
                 bid_depth, ask_depth, volume_imbalance, levels_count, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (ticker, timestamp) DO UPDATE SET
                best_bid = EXCLUDED.best_bid,
                best_ask = EXCLUDED.best_ask,
                spread = EXCLUDED.spread,
                spread_pct = EXCLUDED.spread_pct,
                mid_price = EXCLUDED.mid_price,
                bid_depth = EXCLUDED.bid_depth,
                ask_depth = EXCLUDED.ask_depth,
                volume_imbalance = EXCLUDED.volume_imbalance,
                levels_count = EXCLUDED.levels_count
        """, (row['ticker'], row['timestamp'], row['best_bid'], row['best_ask'],
                row['spread'], row['spread_pct'], row['mid_price'],
                row['bid_depth'], row['ask_depth'], row['volume_imbalance'], row['levels_count']))
    except Exception as e:
        logger.error(f"save_orderbook error {ticker}: {e}")


def run_online_data(tickers: List[str] = None, duration_minutes: int = 60):
    """Stream 1min candles + order book for duration_minutes, with reconnect."""
    if not IS_SDK_AVAILABLE:
        raise RuntimeError("T-Bank API SDK not available")
    if tickers is None:
        tickers = get_trading_universe(DBManager())
    settings = load_settings()
    token = settings.api.token
    if not token:
        raise RuntimeError("TINVEST_TOKEN is empty")

    db = DBManager()
    figi_map = get_figi_map(db, tickers)
    figi_to_ticker = {v: k for k, v in figi_map.items()}
    logger.info(f"FIGI map: {figi_map}")

    # Import streaming types - try multiple paths
    try:
        # Try schemas submodule first
        try:
            from t_tech.invest.schemas import (
                MarketDataServerSideStreamRequest,
                SubscribeCandlesRequest,
                SubscribeOrderBookRequest,
                SubscriptionAction,
                SubscriptionInterval,
                CandleInstrument,
                OrderBookInstrument,
            )
            # OrderBookType may not exist or be optional
            try:
                from t_tech.invest.schemas import OrderBookType
                HAS_ORDERBOOK_TYPE = True
            except ImportError:
                OrderBookType = None
                HAS_ORDERBOOK_TYPE = False
        except ImportError:
            # Fallback to main module
            from t_tech.invest import (
                MarketDataServerSideStreamRequest,
                SubscribeCandlesRequest,
                SubscribeOrderBookRequest,
                SubscriptionAction,
                SubscriptionInterval,
                CandleInstrument,
                OrderBookInstrument,
            )
            try:
                from t_tech.invest import OrderBookType
                HAS_ORDERBOOK_TYPE = True
            except ImportError:
                OrderBookType = None
                HAS_ORDERBOOK_TYPE = False
        logger.info(f"Streaming types imported (OrderBookType available: {HAS_ORDERBOOK_TYPE})")
    except ImportError as e:
        logger.error(f"Failed to import streaming types: {e}")
        raise

    start_time = time.time()

    while (time.time() - start_time) < (duration_minutes * 60):
        try:
            with Client(token, target=INVEST_GRPC_API) as client:
                # Build subscription request
                candle_instruments = [
                    CandleInstrument(instrument_id=figi, interval=SubscriptionInterval.SUBSCRIPTION_INTERVAL_ONE_MINUTE)
                    for figi in figi_map.values()
                ]
                orderbook_instruments = [
                    OrderBookInstrument(instrument_id=figi, depth=ORDERBOOK_DEPTH, order_book_type=OrderBookType.ORDERBOOK_TYPE_EXCHANGE)
                    for figi in figi_map.values()
                ]
                
                # Build order book request (OrderBookType may be optional)
                if HAS_ORDERBOOK_TYPE and OrderBookType is not None:
                    orderbook_request = SubscribeOrderBookRequest(
                        subscription_action=SubscriptionAction.SUBSCRIPTION_ACTION_SUBSCRIBE,
                        instruments=orderbook_instruments,
                        
                    )
                else:
                    # Skip order_book_type if not available
                    orderbook_request = SubscribeOrderBookRequest(
                        subscription_action=SubscriptionAction.SUBSCRIPTION_ACTION_SUBSCRIBE,
                        instruments=orderbook_instruments,
                    )
                
                request = MarketDataServerSideStreamRequest(
                    subscribe_candles_request=SubscribeCandlesRequest(
                        subscription_action=SubscriptionAction.SUBSCRIPTION_ACTION_SUBSCRIBE,
                        instruments=candle_instruments,
                        waiting_close=True,
                    ),
                    subscribe_order_book_request=orderbook_request,
                )
                logger.info("Subscribing to MarketDataServerSideStream...")
                # Server-side stream: iterate over events
                # Call gRPC stub directly (SDK wrapper is broken for unary-stream: it passes
                # request_iterator as a keyword, but the stub accepts only a positional request).
                from t_tech.invest import _grpc_helpers
                from t_tech.invest.grpc import marketdata_pb2
                from t_tech.invest.schemas import MarketDataResponse
                _stub = client.market_data_stream.stub
                _pb_req = _grpc_helpers.dataclass_to_protobuf(
                    request, marketdata_pb2.MarketDataServerSideStreamRequest())
                _md = client.market_data_stream.metadata
                for _pb_resp in _stub.MarketDataServerSideStream(_pb_req, metadata=_md):
                    event = _grpc_helpers.protobuf_to_dataclass(_pb_resp, MarketDataResponse)
                    if (time.time() - start_time) >= (duration_minutes * 60):
                        break
                    # Handle candle
                    if hasattr(event, 'candle') and event.candle is not None:
                        candle = event.candle
                        figi = getattr(candle, 'figi', None)
                        ticker = figi_to_ticker.get(figi)
                        if ticker:
                            save_candle(db, ticker, candle)
                            logger.info(f"Candle {ticker}: close={money_to_float(candle.close)}")
                    # Handle order book
                    if hasattr(event, 'orderbook') and event.orderbook is not None:
                        ob = event.orderbook
                        figi = getattr(ob, 'figi', None)
                        ticker = figi_to_ticker.get(figi)
                        if ticker:
                            save_orderbook_aggregate(db, ticker, ob)
                    # Handle ping (keepalive) - just log occasionally
                    if hasattr(event, 'ping') and event.ping is not None:
                        logger.debug("ping received")
        except Exception as e:
            logger.error(f"Stream error: {e}, reconnecting in {RECONNECT_DELAY}s...")
            time.sleep(RECONNECT_DELAY)

    try: db.close_pool()
    except Exception: pass
    logger.info(f"Online data streaming finished ({duration_minutes} min)")


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    run_online_data(duration_minutes=duration)
