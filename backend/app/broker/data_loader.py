"""
Tinkoff / T-Bank Invest API data loader.

Loads candles using t-tech-investments SDK.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import pandas as pd

try:
    from t_tech.invest import Client, CandleInterval
    from t_tech.invest.constants import INVEST_GRPC_API

    IS_SDK_AVAILABLE = True
except ImportError:
    Client = None
    CandleInterval = None
    INVEST_GRPC_API = None
    IS_SDK_AVAILABLE = False


class DataLoader:
    """
    Loads market data from Tinkoff Invest API.
    """

    INTERVAL_MAP = {
        "1min": CandleInterval.CANDLE_INTERVAL_1_MIN,
        "5min": CandleInterval.CANDLE_INTERVAL_5_MIN,
        "15min": CandleInterval.CANDLE_INTERVAL_15_MIN,
        "30min": CandleInterval.CANDLE_INTERVAL_30_MIN,
        "hour": CandleInterval.CANDLE_INTERVAL_HOUR,
        "1h": CandleInterval.CANDLE_INTERVAL_HOUR,
        "day": CandleInterval.CANDLE_INTERVAL_DAY,
        "1d": CandleInterval.CANDLE_INTERVAL_DAY,
        "week": CandleInterval.CANDLE_INTERVAL_WEEK,
        "1w": CandleInterval.CANDLE_INTERVAL_WEEK,
        "month": CandleInterval.CANDLE_INTERVAL_MONTH,
        "1M": CandleInterval.CANDLE_INTERVAL_MONTH,
    }

    if IS_SDK_AVAILABLE:
        _INTERVAL_NAMES = {
            "1min": "CANDLE_INTERVAL_1_MIN",
            "5min": "CANDLE_INTERVAL_5_MIN",
            "15min": "CANDLE_INTERVAL_15_MIN",
            "30min": "CANDLE_INTERVAL_30_MINUTE",
            "hour": "CANDLE_INTERVAL_HOUR",
            "1h": "CANDLE_INTERVAL_HOUR",
            "day": "CANDLE_INTERVAL_DAY",
            "1d": "CANDLE_INTERVAL_DAY",
            "week": "CANDLE_INTERVAL_WEEK",
            "1w": "CANDLE_INTERVAL_WEEK",
            "month": "CANDLE_INTERVAL_MONTH",
            "1M": "CANDLE_INTERVAL_MONTH",
        }

        for key, value in _INTERVAL_NAMES.items():
            if hasattr(CandleInterval, value):
                INTERVAL_MAP[key] = getattr(CandleInterval, value)

    def __init__(self) -> None:
        self.token = os.getenv("TINVEST_TOKEN", "").strip()

    def fetch_candles_by_figi(
        self,
        figi: str,
        ticker: str = "",
        days: int = 7,
        interval_str: str = "30min",
    ) -> pd.DataFrame:
        """
        Fetch candles by FIGI.

        :param figi: instrument FIGI
        :param ticker: ticker for logging
        :param days: history days
        :param interval_str: interval, for example 30min, 1h, 1d
        :return: DataFrame with candles
        """

        if not IS_SDK_AVAILABLE:
            raise RuntimeError("t-tech-investments SDK is not available")

        if not self.token:
            raise RuntimeError("TINVEST_TOKEN is empty")

        interval = self.INTERVAL_MAP.get(interval_str)

        if interval is None:
            raise ValueError(f"Unknown interval: {interval_str}")

        now = datetime.now(timezone.utc)
        from_dt = now - timedelta(days=days)

        with Client(self.token, target=INVEST_GRPC_API) as client:
            response = client.market_data.get_candles(
                figi=figi,
                from_=from_dt,
                to=now,
                interval=interval,
            )

            candles = getattr(response, "candles", []) or []

            rows = []

            for candle in candles:
                open_price = getattr(candle, "open", None)
                high_price = getattr(candle, "high", None)
                low_price = getattr(candle, "low", None)
                close_price = getattr(candle, "close", None)

                rows.append(
                    {
                        "time": getattr(candle, "time", None),
                        "open": float(open_price.units) + open_price.nano / 1e9 if open_price else None,
                        "high": float(high_price.units) + high_price.nano / 1e9 if high_price else None,
                        "low": float(low_price.units) + low_price.nano / 1e9 if low_price else None,
                        "close": float(close_price.units) + close_price.nano / 1e9 if close_price else None,
                        "volume": getattr(candle, "volume", None),
                    }
                )

            return pd.DataFrame(rows)
