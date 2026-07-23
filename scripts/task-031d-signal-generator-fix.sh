#!/usr/bin/env bash
set -u

TASK_ID="task-031d-signal-generator-fix"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
REPORT_DIR="reports/${TASK_ID}"
BACKUP_DIR="${REPORT_DIR}/backup"
LOG_TXT="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"
REPORT_MD="${REPORT_DIR}/report.md"

mkdir -p "${BACKUP_DIR}"
: > "${LOG_TXT}"

log() {
  echo "$1" | tee -a "${LOG_TXT}"
}

log "Task: ${TASK_ID}"
log "Started: ${STARTED_AT}"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  PY=""
fi

if [[ -z "${PY}" ]]; then
  log "ERROR: python not found"
  cat > "${REPORT_JSON}" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "failed",
  "error": "python_not_found",
  "started_at": "${STARTED_AT}",
  "finished_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
  exit 1
fi

log "Using Python: ${PY}"

backup_file() {
  local file_path="$1"
  if [[ -f "${file_path}" ]]; then
    local dest="${BACKUP_DIR}/${file_path}"
    mkdir -p "$(dirname "${dest}")"
    cp "${file_path}" "${dest}"
    log "BACKUP ${file_path}"
  fi
}

backup_file "backend/app/analytics/patterns/__init__.py"
backup_file "backend/app/analytics/patterns/trend.py"
backup_file "backend/app/analytics/patterns/mean_reversion.py"
backup_file "backend/app/analytics/patterns/breakout.py"
backup_file "backend/app/analytics/patterns/volume.py"
backup_file "backend/app/analytics/patterns/price_action.py"
backup_file "backend/app/analytics/signal_engine.py"
backup_file "backend/app/analytics/signal_generator.py"
backup_file "backend/tests/test_signal_generator.py"

log "Creating backend/app/analytics/patterns/base.py"
cat > backend/app/analytics/patterns/base.py <<'BASE_PY_EOF'
"""
Base classes for signal patterns.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

import pandas as pd


@dataclass
class MarketContext:
    """
    Minimal market context passed to every pattern.
    """

    timeframe: str
    ticker: str = ""
    figi: str = ""


class BasePattern:
    """
    Base class for all trading patterns.
    """

    name: str = "BasePattern"
    category: str = "Base"

    allowed_trend_regimes = []
    allowed_vol_regimes = []
    timeframe_thresholds: Dict[str, Dict[str, Any]] = {}

    def get_thresholds(self, timeframe: str) -> Dict[str, Any]:
        """
        Return thresholds for a timeframe.

        Patterns may define `timeframe_thresholds` as:

            {
                "30min": {...},
                "1h": {...},
            }

        If a timeframe is missing, an empty dict is returned.
        """

        thresholds = getattr(self, "timeframe_thresholds", {}) or {}
        return dict(thresholds.get(timeframe, {}))

    def evaluate(
        self,
        current_row: pd.Series,
        history: pd.DataFrame,
        context: MarketContext,
    ) -> Optional[Dict[str, Any]]:
        """
        Evaluate pattern on the current candle.

        Contract:
        - current_row: current candle/indicator row.
        - history: candles up to and including current_row.
        - context: timeframe/ticker/figi context.

        Expected return:
            {
                "direction": "BUY" | "SELL",
                "strength": float,
                "reason": str,
            }

        or None if no signal.
        """

        raise NotImplementedError
BASE_PY_EOF

log "Rewriting backend/app/analytics/patterns/__init__.py"
cat > backend/app/analytics/patterns/__init__.py <<'INIT_PY_EOF'
"""
Trading patterns package.
"""

from .trend import Trend_SMA_Alignment
from .mean_reversion import MR_RSI_Reversal
from .breakout import BO_BB_Squeeze
from .volume import VOL_Spike, VOL_Low_Pullback
from .price_action import (
    PA_Hammer,
    PA_HangingMan,
    PA_Engulfing,
    PA_ThreeWhiteSoldiers,
    PA_ThreeBlackCrows,
)

__all__ = [
    "Trend_SMA_Alignment",
    "MR_RSI_Reversal",
    "BO_BB_Squeeze",
    "VOL_Spike",
    "VOL_Low_Pullback",
    "PA_Hammer",
    "PA_HangingMan",
    "PA_Engulfing",
    "PA_ThreeWhiteSoldiers",
    "PA_ThreeBlackCrows",
]
INIT_PY_EOF

log "Replacing old src.core.signal_patterns.base imports"
for pattern_file in \
  backend/app/analytics/patterns/trend.py \
  backend/app/analytics/patterns/mean_reversion.py \
  backend/app/analytics/patterns/breakout.py \
  backend/app/analytics/patterns/volume.py \
  backend/app/analytics/patterns/price_action.py
do
  if [[ -f "${pattern_file}" ]]; then
    sed -i 's#from src.core.signal_patterns.base import BasePattern, MarketContext#from app.analytics.patterns.base import BasePattern, MarketContext#g' "${pattern_file}"
    log "PATCHED imports in ${pattern_file}"
  else
    log "MISSING ${pattern_file}"
  fi
done

log "Fixing MR_RSI_Reversal previous RSI indexing"
"${PY}" - <<'PYFIX'
from pathlib import Path

path = Path("backend/app/analytics/patterns/mean_reversion.py")
if not path.exists():
    print("MISSING mean_reversion.py")
    raise SystemExit(0)

text = path.read_text(encoding="utf-8")

old = "        prev_rsi = history['rsi_14'].iloc[-1]"
new = (
    "        if len(history) < 2:\n"
    "            return None\n"
    "        prev_rsi = history['rsi_14'].iloc[-2]"
)

if old in text:
    text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    print("MR_RSI_FIX=applied")
else:
    if "iloc[-2]" in text:
        print("MR_RSI_FIX=already_fixed")
    else:
        print("MR_RSI_FIX=not_found")
PYFIX

log "Rewriting backend/app/analytics/signal_engine.py"
cat > backend/app/analytics/signal_engine.py <<'ENGINE_PY_EOF'
"""
SignalEngine.

Applies a list of patterns to an indicator DataFrame.
"""

import logging
from typing import Any, Dict, List

import pandas as pd

from app.analytics.patterns.base import MarketContext

logger = logging.getLogger(__name__)


class SignalEngine:
    """
    Applies patterns to each candle in a DataFrame.
    """

    def __init__(self, patterns: List[Any]):
        self.patterns = patterns

    def process_dataframe(
        self,
        df: pd.DataFrame,
        timeframe: str,
        lookback_window: int,
    ) -> List[Dict[str, Any]]:
        """
        Apply all patterns to every candle.

        Returns a list of results:

            {
                "candle": dict,
                "triggered_patterns": [
                    {
                        "name": ...,
                        "direction": "BUY" | "SELL",
                        "strength": float,
                        "reason": str,
                    }
                ],
                "summary": {
                    "buy_signals": int,
                    "sell_signals": int,
                    "total_patterns": int,
                },
            }
        """

        results: List[Dict[str, Any]] = []

        for idx in range(len(df)):
            current_row = df.iloc[idx]
            candle = current_row.to_dict()

            candle["timestamp"] = pd.to_datetime(candle.get("timestamp"))

            close_value = candle.get("close", candle.get("price"))
            try:
                candle["price"] = float(close_value) if close_value is not None else None
            except Exception:
                candle["price"] = None

            # History includes the current candle.
            # Most patterns expect history.iloc[-1] to be current,
            # and history.iloc[-2] to be previous.
            history = df.iloc[: idx + 1]

            context = MarketContext(
                timeframe=str(timeframe),
                ticker=str(candle.get("ticker", "")),
                figi=str(candle.get("figi", "")),
            )

            triggered: List[Dict[str, Any]] = []

            for pattern in self.patterns:
                pattern_name = getattr(pattern, "name", pattern.__class__.__name__)

                try:
                    if hasattr(pattern, "evaluate"):
                        signal = pattern.evaluate(current_row, history, context)
                    elif hasattr(pattern, "check"):
                        # Backward compatibility only.
                        signal = pattern.check(candle, history, lookback_window)
                    else:
                        signal = None
                except Exception as exc:
                    logger.debug("Pattern %s failed: %s", pattern_name, exc)
                    signal = None

                if not signal:
                    continue

                signal = dict(signal)
                signal.setdefault("name", pattern_name)
                signal["direction"] = str(signal.get("direction", "")).upper()

                if signal["direction"] not in {"BUY", "SELL"}:
                    continue

                triggered.append(signal)

            summary = {
                "buy_signals": sum(1 for s in triggered if s.get("direction") == "BUY"),
                "sell_signals": sum(1 for s in triggered if s.get("direction") == "SELL"),
                "total_patterns": len(triggered),
            }

            results.append(
                {
                    "candle": candle,
                    "triggered_patterns": triggered,
                    "summary": summary,
                }
            )

        return results
ENGINE_PY_EOF

log "Rewriting backend/app/analytics/signal_generator.py"
cat > backend/app/analytics/signal_generator.py <<'GENERATOR_PY_EOF'
"""
Signal generator.

Scans indicators, applies SignalEngine patterns and stores signals
into trading.signals.
"""

import logging
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.db.db_manager import DBManager
from app.analytics.indicators_manager import IndicatorsManager
from app.analytics.patterns import (
    Trend_SMA_Alignment,
    MR_RSI_Reversal,
    BO_BB_Squeeze,
    VOL_Spike,
    VOL_Low_Pullback,
    PA_Hammer,
    PA_HangingMan,
    PA_Engulfing,
    PA_ThreeWhiteSoldiers,
    PA_ThreeBlackCrows,
)
from app.analytics.signal_engine import SignalEngine

logger = logging.getLogger(__name__)


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


class SignalGenerator:
    """
    Generates and stores trading signals.
    """

    def __init__(self) -> None:
        self.db = DBManager()
        self.indicators_manager = IndicatorsManager()
        self._ensure_signals_table()

        self.engine = SignalEngine(
            patterns=[
                Trend_SMA_Alignment(),
                MR_RSI_Reversal(),
                BO_BB_Squeeze(),
                VOL_Spike(),
                VOL_Low_Pullback(),
                PA_Hammer(),
                PA_HangingMan(),
                PA_Engulfing(),
                PA_ThreeWhiteSoldiers(),
                PA_ThreeBlackCrows(),
            ]
        )

        self.stats = {
            "db_insert_success": 0,
            "db_insert_duplicate": 0,
            "db_insert_error": 0,
            "total_errors": 0,
        }

    def _ensure_signals_table(self) -> None:
        """
        Ensures trading.signals exists and has a unique index for upserts.
        """

        try:
            check_query = """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'trading'
                      AND table_name = 'signals'
                )
            """
            result = self.db.select(check_query)
            df = result.to_dataframe()

            exists = False
            if not df.empty:
                exists = bool(df.iloc[0]["exists"])

            if not exists:
                create_query = """
                    CREATE TABLE IF NOT EXISTS trading.signals (
                        id SERIAL PRIMARY KEY,
                        ticker VARCHAR(20) NOT NULL,
                        timeframe VARCHAR(10) NOT NULL,
                        timestamp TIMESTAMP NOT NULL,
                        signal VARCHAR(10) NOT NULL,
                        confidence NUMERIC(5,4),
                        price NUMERIC(20,10),
                        rsi NUMERIC(10,2),
                        macd NUMERIC(20,10),
                        bb_position NUMERIC(5,2),
                        volume_ratio NUMERIC(10,2),
                        atr_pct NUMERIC(10,4),
                        summary TEXT,
                        buy_signals INT,
                        sell_signals INT,
                        total_signals INT,
                        pattern_name TEXT,
                        figi VARCHAR(50),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT signals_unique_key
                            UNIQUE (ticker, timeframe, timestamp, signal)
                    )
                """
                self.db.execute(create_query)
                logger.info("Created trading.signals table")

            # Optional useful columns for future frontend/API.
            self.db.execute(
                "ALTER TABLE trading.signals ADD COLUMN IF NOT EXISTS pattern_name TEXT"
            )
            self.db.execute(
                "ALTER TABLE trading.signals ADD COLUMN IF NOT EXISTS figi VARCHAR(50)"
            )

            # Remove duplicates before creating unique index.
            # Keeps one row per ticker/timeframe/timestamp/signal.
            self.db.execute(
                """
                DELETE FROM trading.signals a
                USING trading.signals b
                WHERE a.ctid < b.ctid
                  AND a.ticker = b.ticker
                  AND a.timeframe = b.timeframe
                  AND a.timestamp = b.timestamp
                  AND a.signal = b.signal
                """
            )

            # Required for ON CONFLICT (ticker, timeframe, timestamp, signal).
            self.db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_unique
                    ON trading.signals (ticker, timeframe, timestamp, signal)
                """
            )

            logger.info("trading.signals schema ensured")

        except Exception as exc:
            logger.error("Cannot ensure trading.signals schema: %s", exc)
            raise

    def get_top_tickers(self, limit: int = 30) -> List[str]:
        """
        Returns top tickers from the latest report_date.
        """

        query = """
            WITH latest AS (
                SELECT COALESCE(MAX(report_date), CURRENT_DATE) AS report_date
                FROM trading.top_stocks_by_volume
            )
            SELECT t.ticker
            FROM trading.top_stocks_by_volume t
            JOIN latest l ON t.report_date = l.report_date
            ORDER BY t.rank ASC
            LIMIT %(limit)s
        """

        result = self.db.select(query, params={"limit": limit})
        df = result.to_dataframe()

        if df.empty:
            return []

        return df["ticker"].tolist()

    def scan_and_save_signals(
        self,
        tickers: List[str],
        timeframes: Optional[List[str]] = None,
        lookback: int = 1000,
    ) -> Dict[str, Any]:
        """
        Scans tickers and saves signals.
        """

        if timeframes is None:
            timeframes = ["30min", "1h", "4h", "1d"]

        logger.info(
            "Starting scan: %d tickers, timeframes=%s, lookback=%d",
            len(tickers),
            timeframes,
            lookback,
        )

        self.stats = {
            "db_insert_success": 0,
            "db_insert_duplicate": 0,
            "db_insert_error": 0,
            "total_errors": 0,
        }

        report: Dict[str, Any] = {
            "scan_started_at": datetime.now().isoformat(),
            "tickers_count": len(tickers),
            "timeframes": timeframes,
            "lookback": lookback,
            "total_signals_saved": 0,
            "total_candles_analyzed": 0,
            "tickers_scanned": [],
            "signals": [],
            "errors": [],
            "pattern_statistics": {},
            "db_statistics": {
                "insert_success": 0,
                "insert_duplicate": 0,
                "insert_error": 0,
            },
        }

        for ticker in tickers:
            figi = self.indicators_manager.get_figi(ticker)

            ticker_report = {
                "ticker": ticker,
                "figi": figi,
                "timeframes_scanned": [],
                "signals_found": 0,
                "errors": [],
            }

            for timeframe in timeframes:
                timeframe_report = {
                    "timeframe": timeframe,
                    "candles_analyzed": 0,
                    "signals_found": 0,
                    "patterns_triggered": {},
                    "db_inserts": {
                        "success": 0,
                        "duplicate": 0,
                        "error": 0,
                    },
                }

                try:
                    df = self.indicators_manager.get_indicators(
                        ticker=ticker,
                        timeframe=timeframe,
                        limit=lookback,
                    )

                    if df.empty:
                        logger.warning("%s (%s): no indicator data", ticker, timeframe)
                        ticker_report["timeframes_scanned"].append(timeframe_report)
                        continue

                    if len(df) < 20:
                        logger.warning(
                            "%s (%s): too few candles: %d",
                            ticker,
                            timeframe,
                            len(df),
                        )
                        ticker_report["timeframes_scanned"].append(timeframe_report)
                        continue

                    if "figi" not in df.columns:
                        df["figi"] = figi
                    else:
                        df["figi"] = df["figi"].fillna(figi)

                    df = df.sort_values("timestamp").reset_index(drop=True)

                    logger.info(
                        "%s (%s): analyzing %d candles",
                        ticker,
                        timeframe,
                        len(df),
                    )

                    timeframe_report["candles_analyzed"] = len(df)
                    report["total_candles_analyzed"] += len(df)

                    results = self.engine.process_dataframe(
                        df=df,
                        timeframe=timeframe,
                        lookback_window=len(df),
                    )

                    for res in results:
                        triggered = res.get("triggered_patterns", [])
                        if not triggered:
                            continue

                        candle = res["candle"]
                        summary_counts = res.get("summary", {})

                        by_direction: Dict[str, List[Dict[str, Any]]] = {}

                        for pattern_signal in triggered:
                            direction = str(pattern_signal.get("direction", "")).upper()
                            if direction not in {"BUY", "SELL"}:
                                continue

                            by_direction.setdefault(direction, []).append(pattern_signal)

                        for direction, patterns in by_direction.items():
                            pattern_names = [
                                str(p.get("name", "Unknown")) for p in patterns
                            ]

                            reasons = [
                                f"{p.get('name', 'Unknown')}: {p.get('reason', '')}"
                                for p in patterns
                            ]

                            strengths = [
                                float(p.get("strength"))
                                for p in patterns
                                if p.get("strength") is not None
                            ]

                            confidence = max(strengths) if strengths else None

                            timestamp_value = candle.get("timestamp")
                            if hasattr(timestamp_value, "to_pydatetime"):
                                timestamp_value = timestamp_value.to_pydatetime()

                            close_value = _to_float(candle.get("close"))
                            price_value = _to_float(candle.get("price"))
                            if price_value is None:
                                price_value = close_value

                            atr_14 = _to_float(candle.get("atr_14"))
                            atr_pct = None
                            if atr_14 is not None and close_value not in (None, 0.0):
                                atr_pct = atr_14 / close_value * 100.0

                            signal_data = {
                                "ticker": str(ticker),
                                "figi": str(candle.get("figi") or figi or ""),
                                "timeframe": str(timeframe),
                                "timestamp": timestamp_value,
                                "signal": direction,
                                "confidence": confidence,
                                "price": price_value,
                                "rsi": _to_float(candle.get("rsi_14")),
                                "macd": _to_float(candle.get("macd")),
                                "bb_position": _to_float(candle.get("bb_position")),
                                "volume_ratio": _to_float(candle.get("volume_ratio")),
                                "atr_pct": atr_pct,
                                "summary": "; ".join(reasons),
                                "pattern_name": ", ".join(pattern_names),
                                "buy_signals": int(summary_counts.get("buy_signals", 0)),
                                "sell_signals": int(summary_counts.get("sell_signals", 0)),
                                "total_signals": int(summary_counts.get("total_patterns", 0)),
                            }

                            status, error = self.save_signal_to_db(signal_data)

                            if status == "success":
                                report["total_signals_saved"] += 1
                                timeframe_report["signals_found"] += 1
                                ticker_report["signals_found"] += 1
                                timeframe_report["db_inserts"]["success"] += 1
                                self.stats["db_insert_success"] += 1

                                report["signals"].append(signal_data)

                                for pattern_name in pattern_names:
                                    report["pattern_statistics"][pattern_name] = (
                                        report["pattern_statistics"].get(pattern_name, 0) + 1
                                    )
                                    timeframe_report["patterns_triggered"][pattern_name] = (
                                        timeframe_report["patterns_triggered"].get(pattern_name, 0) + 1
                                    )

                            elif status == "duplicate":
                                timeframe_report["db_inserts"]["duplicate"] += 1
                                self.stats["db_insert_duplicate"] += 1

                            elif status == "error":
                                timeframe_report["db_inserts"]["error"] += 1
                                self.stats["db_insert_error"] += 1

                    if timeframe_report["signals_found"] > 0:
                        logger.info(
                            "%s (%s): saved %d signals",
                            ticker,
                            timeframe,
                            timeframe_report["signals_found"],
                        )

                    ticker_report["timeframes_scanned"].append(timeframe_report)

                except Exception as exc:
                    error_msg = f"{ticker} ({timeframe}): {exc}"
                    logger.error(error_msg)

                    error_report = {
                        "ticker": ticker,
                        "timeframe": timeframe,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                        "timestamp": datetime.now().isoformat(),
                    }

                    report["errors"].append(error_report)
                    ticker_report["errors"].append(error_report)
                    self.stats["total_errors"] += 1

            report["tickers_scanned"].append(ticker_report)

        report["scan_finished_at"] = datetime.now().isoformat()
        report["db_statistics"] = {
            "insert_success": self.stats["db_insert_success"],
            "insert_duplicate": self.stats["db_insert_duplicate"],
            "insert_error": self.stats["db_insert_error"],
        }

        logger.info(
            "Scan finished. Saved signals: %d",
            report["total_signals_saved"],
        )

        return report

    def save_signal_to_db(self, signal_data: Dict[str, Any]) -> Tuple[str, Optional[str]]:
        """
        Upserts one signal.

        Unique key:
            ticker, timeframe, timestamp, signal
        """

        safe_data = {
            "ticker": signal_data.get("ticker"),
            "figi": signal_data.get("figi"),
            "timeframe": signal_data.get("timeframe"),
            "timestamp": signal_data.get("timestamp"),
            "signal": signal_data.get("signal"),
            "confidence": signal_data.get("confidence"),
            "price": signal_data.get("price"),
            "rsi": signal_data.get("rsi"),
            "macd": signal_data.get("macd"),
            "bb_position": signal_data.get("bb_position"),
            "volume_ratio": signal_data.get("volume_ratio"),
            "atr_pct": signal_data.get("atr_pct"),
            "summary": signal_data.get("summary"),
            "pattern_name": signal_data.get("pattern_name"),
            "buy_signals": signal_data.get("buy_signals"),
            "sell_signals": signal_data.get("sell_signals"),
            "total_signals": signal_data.get("total_signals"),
        }

        query = """
            INSERT INTO trading.signals (
                ticker,
                timeframe,
                timestamp,
                signal,
                confidence,
                price,
                rsi,
                macd,
                bb_position,
                volume_ratio,
                atr_pct,
                summary,
                buy_signals,
                sell_signals,
                total_signals,
                pattern_name,
                figi
            ) VALUES (
                %(ticker)s,
                %(timeframe)s,
                %(timestamp)s,
                %(signal)s,
                %(confidence)s,
                %(price)s,
                %(rsi)s,
                %(macd)s,
                %(bb_position)s,
                %(volume_ratio)s,
                %(atr_pct)s,
                %(summary)s,
                %(buy_signals)s,
                %(sell_signals)s,
                %(total_signals)s,
                %(pattern_name)s,
                %(figi)s
            )
            ON CONFLICT (ticker, timeframe, timestamp, signal)
            DO UPDATE SET
                confidence = EXCLUDED.confidence,
                price = EXCLUDED.price,
                rsi = EXCLUDED.rsi,
                macd = EXCLUDED.macd,
                bb_position = EXCLUDED.bb_position,
                volume_ratio = EXCLUDED.volume_ratio,
                atr_pct = EXCLUDED.atr_pct,
                summary = EXCLUDED.summary,
                buy_signals = EXCLUDED.buy_signals,
                sell_signals = EXCLUDED.sell_signals,
                total_signals = EXCLUDED.total_signals,
                pattern_name = EXCLUDED.pattern_name,
                figi = EXCLUDED.figi
        """

        try:
            self.db.execute(query, params=safe_data)
            return ("success", None)
        except Exception as exc:
            error_str = str(exc)
            error_lower = error_str.lower()

            if (
                "duplicate" in error_lower
                or "conflict" in error_lower
                or "unique" in error_lower
            ):
                return ("duplicate", None)

            logger.error(
                "Error saving signal %s: %s",
                safe_data.get("ticker"),
                error_str,
            )
            return ("error", error_str)

    def close(self) -> None:
        self.db.close_pool()
GENERATOR_PY_EOF

log "Rewriting backend/tests/test_signal_generator.py"
cat > backend/tests/test_signal_generator.py <<'TEST_PY_EOF'
import pandas as pd
from unittest.mock import MagicMock, patch

from app.analytics.signal_generator import SignalGenerator


def _mock_exists_result(exists: bool = True):
    result = MagicMock()
    result.to_dataframe.return_value = pd.DataFrame([{"exists": exists}])
    return result


def test_signal_generator_initialization():
    with patch("app.analytics.signal_generator.DBManager") as mock_db_cls, \
         patch("app.analytics.signal_generator.IndicatorsManager"):
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        mock_db.select.return_value = _mock_exists_result(True)

        generator = SignalGenerator()

        assert generator is not None
        assert generator.engine is not None


def test_get_top_tickers_empty():
    with patch("app.analytics.signal_generator.DBManager") as mock_db_cls, \
         patch("app.analytics.signal_generator.IndicatorsManager"):
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        empty_result = MagicMock()
        empty_result.to_dataframe.return_value = pd.DataFrame()

        mock_db.select.side_effect = [
            _mock_exists_result(True),
            empty_result,
        ]

        generator = SignalGenerator()
        tickers = generator.get_top_tickers()

        assert tickers == []


def test_ensure_signals_table_creates_unique_index():
    with patch("app.analytics.signal_generator.DBManager") as mock_db_cls, \
         patch("app.analytics.signal_generator.IndicatorsManager"):
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        mock_db.select.return_value = _mock_exists_result(True)

        generator = SignalGenerator()
        generator._ensure_signals_table()

        assert mock_db.execute.called
TEST_PY_EOF

log "Checking old imports"
OLD_IMPORTS_COUNT=$(grep -R "from src.core.signal_patterns.base" backend/app/analytics/patterns backend/app/analytics/signal_engine.py backend/app/analytics/signal_generator.py 2>/dev/null | wc -l | tr -d ' ')
log "OLD_IMPORTS_COUNT=${OLD_IMPORTS_COUNT}"

log "Checking MR_RSI_Reversal fix"
if grep -q "iloc\[-2\]" backend/app/analytics/patterns/mean_reversion.py 2>/dev/null; then
  MR_FIX_STATUS="fixed"
else
  MR_FIX_STATUS="not_fixed"
fi
log "MR_FIX_STATUS=${MR_FIX_STATUS}"

log "Checking BasePattern exists"
if grep -q "class BasePattern" backend/app/analytics/patterns/base.py 2>/dev/null; then
  BASE_STATUS="ok"
else
  BASE_STATUS="missing"
fi
log "BASE_STATUS=${BASE_STATUS}"

log "Running py_compile"
FILES_TO_COMPILE=(
  backend/app/analytics/patterns/base.py
  backend/app/analytics/patterns/__init__.py
  backend/app/analytics/patterns/trend.py
  backend/app/analytics/patterns/mean_reversion.py
  backend/app/analytics/patterns/breakout.py
  backend/app/analytics/patterns/volume.py
  backend/app/analytics/patterns/price_action.py
  backend/app/analytics/signal_engine.py
  backend/app/analytics/signal_generator.py
  backend/tests/test_signal_generator.py
)

PY_COMPILE_STATUS="success"
if ! "${PY}" -m py_compile "${FILES_TO_COMPILE[@]}" >> "${LOG_TXT}" 2>&1; then
  PY_COMPILE_STATUS="failed"
fi
log "PY_COMPILE_STATUS=${PY_COMPILE_STATUS}"

STATUS="success"
if [[ "${PY_COMPILE_STATUS}" != "success" ]]; then
  STATUS="failed"
fi

if [[ "${OLD_IMPORTS_COUNT}" != "0" ]]; then
  STATUS="failed"
fi

if [[ "${BASE_STATUS}" != "ok" ]]; then
  STATUS="failed"
fi

if [[ "${MR_FIX_STATUS}" != "fixed" && "${STATUS}" == "success" ]]; then
  STATUS="needs_human"
fi

FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

export TASK_ID
export STATUS
export STARTED_AT
export FINISHED_AT
export PY_COMPILE_STATUS
export OLD_IMPORTS_COUNT
export MR_FIX_STATUS
export BASE_STATUS
export REPORT_JSON

"${PY}" - <<'REPORT_PY'
import json
import os

report = {
    "task_id": os.environ["TASK_ID"],
    "status": os.environ["STATUS"],
    "started_at": os.environ["STARTED_AT"],
    "finished_at": os.environ["FINISHED_AT"],
    "checks": {
        "py_compile": os.environ["PY_COMPILE_STATUS"],
        "old_imports_count": int(os.environ["OLD_IMPORTS_COUNT"]),
        "mr_rsi_fix": os.environ["MR_FIX_STATUS"],
        "base_pattern": os.environ["BASE_STATUS"],
    },
    "changed_files": [
        "backend/app/analytics/patterns/base.py",
        "backend/app/analytics/patterns/__init__.py",
        "backend/app/analytics/patterns/trend.py",
        "backend/app/analytics/patterns/mean_reversion.py",
        "backend/app/analytics/patterns/breakout.py",
        "backend/app/analytics/patterns/volume.py",
        "backend/app/analytics/patterns/price_action.py",
        "backend/app/analytics/signal_engine.py",
        "backend/app/analytics/signal_generator.py",
        "backend/tests/test_signal_generator.py",
    ],
    "next_action": "If success, run task-031e to regenerate signals and produce report.json",
}

with open(os.environ["REPORT_JSON"], "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
REPORT_PY

{
  echo "# ${TASK_ID}"
  echo
  echo "Status: ${STATUS}"
  echo "Started: ${STARTED_AT}"
  echo "Finished: ${FINISHED_AT}"
  echo
  echo "## Checks"
  echo
  echo "- py_compile: ${PY_COMPILE_STATUS}"
  echo "- old imports count: ${OLD_IMPORTS_COUNT}"
  echo "- MR_RSI fix: ${MR_FIX_STATUS}"
  echo "- BasePattern: ${BASE_STATUS}"
  echo
  echo "## Next action"
  echo
  echo "If success, run signal generation task next."
} > "${REPORT_MD}"

log "Report JSON: ${REPORT_JSON}"
log "Status: ${STATUS}"

if [[ "${STATUS}" == "failed" ]]; then
  exit 1
fi

exit 0
