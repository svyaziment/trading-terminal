from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from app.db.db_manager import DBManager
from app.analytics.signal_generator import SignalGenerator

router = APIRouter(prefix="/api/signals", tags=["signals"])

class GenerateSignalsRequest(BaseModel):
    ticker: str
    timeframe: str = "1h"
    limit: int = 100

class SignalResponse(BaseModel):
    ticker: str
    timeframe: str
    timestamp: datetime
    signal_type: str
    strength: float
    pattern_name: str
    description: str
    meta: dict

@router.post("/generate", response_model=List[SignalResponse])
async def generate_signals(req: GenerateSignalsRequest):
    """Генерирует сигналы для заданного тикера и таймфрейма."""
    db = DBManager()
    generator = SignalGenerator(db)
    signals = generator.generate_for_ticker(req.ticker, req.timeframe, req.limit)
    return [SignalResponse(**sig.__dict__) for sig in signals]

@router.get("/latest/{ticker}")
async def get_latest_signals(ticker: str, timeframe: str = "1h", limit: int = 10):
    """Возвращает последние сигналы из БД (без перегенерации)."""
    db = DBManager()
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT ticker, timeframe, timestamp, signal_type, strength, pattern_name, description, meta
            FROM trading.signals
            WHERE ticker = %s AND timeframe = %s
            ORDER BY timestamp DESC
            LIMIT %s
        """, (ticker, timeframe, limit))
        rows = cur.fetchall()
    return [
        {
            "ticker": r[0], "timeframe": r[1], "timestamp": r[2],
            "signal_type": r[3], "strength": r[4], "pattern_name": r[5],
            "description": r[6], "meta": r[7]
        }
        for r in rows
    ]
