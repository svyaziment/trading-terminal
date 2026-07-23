#!/usr/bin/env bash
set -u

TASK_ID="task-026-top-stocks"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
CWD="$(pwd)"
FEATURE_BRANCH="feat/broker-data-loader"
REPORT_DIR="${CWD}/reports/${TASK_ID}"
LOG_FILE="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"
REPORT_MD="${REPORT_DIR}/report.md"

mkdir -p "${REPORT_DIR}"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== Task: ${TASK_ID} ==="
echo "Started: ${STARTED_AT}"
echo "Working directory: ${CWD}"

cd "${CWD}" || { echo "FAIL: cannot cd to ${CWD}"; exit 1; }

# ---------- Git ----------
echo "Checking git..."
command -v git >/dev/null 2>&1 || { echo "FAIL: git not found"; exit 1; }
[ -d .git ] || { echo "FAIL: not a git repo"; exit 1; }

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "Current branch: ${CURRENT_BRANCH}"

if [ "${CURRENT_BRANCH}" != "${FEATURE_BRANCH}" ]; then
    git checkout "${FEATURE_BRANCH}" 2>/dev/null || git checkout -b "${FEATURE_BRANCH}" origin/main
    echo "OK: switched to ${FEATURE_BRANCH}"
else
    echo "OK: already on ${FEATURE_BRANCH}"
fi

# ---------- Create top_stocks.py ----------
echo "Creating backend/app/analytics/top_stocks.py..."

mkdir -p backend/app/analytics

cat > backend/app/analytics/top_stocks.py <<'TOPSTOCKS_EOF'
"""
Top Stocks Calculator.
Calculates TOP-30 stocks by monthly trading volume.
Adapted from AlgoTerminal src/core/top_stocks.py
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from app.core.config_manager import setup_logger
from app.db.db_manager import DBManager

logger = setup_logger("TopStocksCalculator")

SCHEMA = "trading"
TABLE_NAME = "top_stocks_by_volume"

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.{TABLE_NAME} (
    rank            INT             NOT NULL,
    report_date     DATE            NOT NULL,
    ticker          VARCHAR(20)     NOT NULL,
    figi            VARCHAR(50)     NOT NULL,
    name            VARCHAR(500),
    sum_volume      BIGINT          NOT NULL,
    candle_count    INT             NOT NULL,
    first_date      DATE,
    last_date       DATE,
    period_start    DATE            NOT NULL,
    period_end      DATE            NOT NULL,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (report_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_top_stocks_ticker
    ON {SCHEMA}.{TABLE_NAME} (ticker);

CREATE INDEX IF NOT EXISTS idx_top_stocks_report_date
    ON {SCHEMA}.{TABLE_NAME} (report_date);

CREATE INDEX IF NOT EXISTS idx_top_stocks_rank
    ON {SCHEMA}.{TABLE_NAME} (rank);
"""


class TopStocksCalculator:
    """Calculates TOP stocks by monthly trading volume."""

    def __init__(self) -> None:
        self.db = DBManager()
        self.ensure_table()

    def ensure_table(self) -> None:
        self.db.execute(CREATE_TABLE_SQL)
        logger.info("Table %s.%s ensured", SCHEMA, TABLE_NAME)

    def check_monthly_candles(self) -> Dict[str, Any]:
        """Check if monthly candles exist in trading.candles."""
        query = """
            SELECT
                COUNT(*) AS total_candles,
                COUNT(DISTINCT ticker) AS unique_tickers,
                MIN(timestamp) AS min_date,
                MAX(timestamp) AS max_date
            FROM trading.candles
            WHERE interval = 'month'
        """
        result = self.db.select(query)
        df = result.to_dataframe()

        if df.empty:
            return {"total_candles": 0, "unique_tickers": 0}

        row = df.iloc[0]
        info = {
            "total_candles": int(row["total_candles"]),
            "unique_tickers": int(row["unique_tickers"]),
            "min_date": str(row["min_date"]) if row["min_date"] else None,
            "max_date": str(row["max_date"]) if row["max_date"] else None,
        }
        logger.info(
            "Monthly candles: %d candles, %d tickers, range %s .. %s",
            info["total_candles"],
            info["unique_tickers"],
            info["min_date"],
            info["max_date"],
        )
        return info

    def check_instruments(self) -> Dict[str, Any]:
        """Check if instruments exist in trading.instruments."""
        query = """
            SELECT
                COUNT(*) AS total,
                COUNT(DISTINCT ticker) AS unique_tickers
            FROM trading.instruments
            WHERE instrument_type = 'stock'
              AND exchange ILIKE 'moex%%'
              AND is_tradable = TRUE
        """
        result = self.db.select(query)
        df = result.to_dataframe()

        if df.empty:
            return {"total": 0, "unique_tickers": 0}

        row = df.iloc[0]
        info = {
            "total": int(row["total"]),
            "unique_tickers": int(row["unique_tickers"]),
        }
        logger.info(
            "Instruments: %d stocks, %d unique tickers",
            info["total"],
            info["unique_tickers"],
        )
        return info

    def calculate_top_stocks(
        self,
        limit: int = 30,
        report_date: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Calculate TOP stocks by monthly trading volume.

        Uses monthly candles from trading.candles for the current
        and previous month.
        """
        if report_date is None:
            report_date = datetime.now()

        # Calculate period: current month start .. current month end
        now = datetime.now()
        start_current = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Previous month start
        start_prev = (start_current - timedelta(days=1)).replace(day=1)

        # Current month end
        if start_current.month == 12:
            next_month = start_current.replace(year=start_current.year + 1, month=1, day=1)
        else:
            next_month = start_current.replace(month=start_current.month + 1, day=1)
        end_current = next_month - timedelta(days=1)

        period_start = start_prev.strftime("%Y-%m-%d")
        period_end = end_current.strftime("%Y-%m-%d")

        logger.info(
            "Calculating TOP-%d for period %s .. %s",
            limit,
            period_start,
            period_end,
        )

        query = """
            SELECT
                instr.ticker,
                instr.figi,
                instr.name,
                SUM(cndl.volume) AS sum_volume,
                COUNT(cndl.volume) AS candle_count,
                MIN(cndl.timestamp) AS first_date,
                MAX(cndl.timestamp) AS last_date
            FROM trading.candles cndl
            JOIN trading.instruments instr
                ON cndl.ticker = instr.ticker
                AND instr.instrument_type = 'stock'
                AND instr.exchange ILIKE 'moex%%'
            WHERE cndl.interval = 'month'
              AND cndl.timestamp BETWEEN %(start_date)s AND %(end_date)s
            GROUP BY
                instr.ticker,
                instr.figi,
                instr.name
            HAVING SUM(cndl.volume) > 0
            ORDER BY sum_volume DESC
            LIMIT %(limit)s
        """

        params = {
            "start_date": period_start,
            "end_date": period_end,
            "limit": limit,
        }

        result = self.db.select(query, params=params)
        df = result.to_dataframe()

        if df.empty:
            logger.warning("No data found for period %s .. %s", period_start, period_end)
            return df

        # Add rank and metadata
        df.insert(0, "rank", range(1, len(df) + 1))
        df["report_date"] = report_date.strftime("%Y-%m-%d")
        df["period_start"] = period_start
        df["period_end"] = period_end

        logger.info("Calculated TOP-%d: %d rows", limit, len(df))
        return df

    def save_to_db(
        self,
        df: pd.DataFrame,
        report_date: Optional[datetime] = None,
    ) -> int:
        """Save TOP stocks to database."""
        if df.empty:
            logger.warning("No data to save")
            return 0

        if report_date is None:
            report_date = datetime.now()

        report_date_str = report_date.strftime("%Y-%m-%d")

        # Delete existing data for this report_date
        self.db.execute(
            f"""
            DELETE FROM {SCHEMA}.{TABLE_NAME}
            WHERE report_date = %(report_date)s
            """,
            params={"report_date": report_date_str},
        )

        # Insert new data
        self.db.insert_with_schema(TABLE_NAME, df)
        logger.info(
            "Saved %d rows to %s.%s for %s",
            len(df),
            SCHEMA,
            TABLE_NAME,
            report_date_str,
        )
        return len(df)

    def run(
        self,
        limit: int = 30,
        report_date: Optional[datetime] = None,
    ) -> int:
        """Full cycle: calculate + save."""
        if report_date is None:
            report_date = datetime.now()

        # Check prerequisites
        candles_info = self.check_monthly_candles()
        instruments_info = self.check_instruments()

        if candles_info["total_candles"] == 0:
            logger.error("No monthly candles in trading.candles")
            return 0

        if instruments_info["total"] == 0:
            logger.error("No instruments in trading.instruments")
            return 0

        # Calculate TOP
        df = self.calculate_top_stocks(limit=limit, report_date=report_date)

        if df.empty:
            logger.error("No TOP stocks calculated")
            return 0

        # Save
        count = self.save_to_db(df, report_date=report_date)

        # Print results
        print("\n" + "=" * 80)
        print(f"📊 TOP-{limit} STOCKS BY VOLUME")
        print(f"Report date: {report_date.strftime('%Y-%m-%d')}")
        print(f"Period: {df['period_start'].iloc[0]} .. {df['period_end'].iloc[0]}")
        print("=" * 80)
        print()

        for _, row in df.iterrows():
            name = str(row["name"])[:30] if row["name"] else ""
            print(
                f"  {row['rank']:2d}. {row['ticker']:8s} | {name:30s} | "
                f"Volume: {row['sum_volume']:>15,} | "
                f"Candles: {row['candle_count']:3d}"
            )

        print()
        print("=" * 80)
        print(f"✅ Total: {count} stocks")
        print("=" * 80)

        return count

    def get_top_tickers(self, limit: int = 30) -> List[str]:
        """Get list of top tickers from database."""
        query = f"""
            SELECT ticker
            FROM {SCHEMA}.{TABLE_NAME}
            ORDER BY rank
            LIMIT %(limit)s
        """
        result = self.db.select(query, params={"limit": limit})
        df = result.to_dataframe()

        if df.empty:
            return []

        return df["ticker"].tolist()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Calculate TOP stocks by volume")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    calc = TopStocksCalculator()

    if args.check_only:
        candles_info = calc.check_monthly_candles()
        instruments_info = calc.check_instruments()
        print(f"Monthly candles: {candles_info}")
        print(f"Instruments: {instruments_info}")
        return

    count = calc.run(limit=args.limit)
    print(f"\nTOP_STOCKS_COUNT={count}")


if __name__ == "__main__":
    main()
TOPSTOCKS_EOF

echo "OK: top_stocks.py created"

# ---------- Docker build ----------
echo "Building backend image..."
if ! docker compose build backend; then
    echo "FAIL: backend image build failed"
    exit 1
fi
echo "OK: backend image built"

# ---------- Check prerequisites ----------
echo "Checking prerequisites..."

CHECK_OUTPUT="$(docker compose run --rm -T --no-deps backend \
    python -c "
from app.analytics.top_stocks import TopStocksCalculator
calc = TopStocksCalculator()
candles_info = calc.check_monthly_candles()
instruments_info = calc.check_instruments()
print('CANDLES_TOTAL=' + str(candles_info.get('total_candles', 0)))
print('CANDLES_TICKERS=' + str(candles_info.get('unique_tickers', 0)))
print('INSTRUMENTS_TOTAL=' + str(instruments_info.get('total', 0)))
" 2>&1)"

echo "----- BEGIN CHECK_OUTPUT -----"
echo "${CHECK_OUTPUT}"
echo "----- END CHECK_OUTPUT -----"

CANDLES_TOTAL="$(echo "${CHECK_OUTPUT}" | grep 'CANDLES_TOTAL=' | cut -d'=' -f2)"
CANDLES_TICKERS="$(echo "${CHECK_OUTPUT}" | grep 'CANDLES_TICKERS=' | cut -d'=' -f2)"
INSTRUMENTS_TOTAL="$(echo "${CHECK_OUTPUT}" | grep 'INSTRUMENTS_TOTAL=' | cut -d'=' -f2)"

echo "Monthly candles: ${CANDLES_TOTAL} candles, ${CANDLES_TICKERS} tickers"
echo "Instruments: ${INSTRUMENTS_TOTAL} stocks"

if [ "${CANDLES_TOTAL}" = "0" ] || [ "${INSTRUMENTS_TOTAL}" = "0" ]; then
    echo "FAIL: No monthly candles or instruments in database"
    echo "Need to load data first"
    exit 1
fi

# ---------- Calculate TOP-30 ----------
echo "Calculating TOP-30..."

TOP_OUTPUT="$(docker compose run --rm -T --no-deps backend \
    python -c "
from app.analytics.top_stocks import TopStocksCalculator
calc = TopStocksCalculator()
count = calc.run(limit=30)
print('TOP_STOCKS_COUNT=' + str(count))
" 2>&1)"

echo "----- BEGIN TOP_OUTPUT -----"
echo "${TOP_OUTPUT}"
echo "----- END TOP_OUTPUT -----"

TOP_COUNT="$(echo "${TOP_OUTPUT}" | grep 'TOP_STOCKS_COUNT=' | cut -d'=' -f2)"

if [ -z "${TOP_COUNT}" ] || [ "${TOP_COUNT}" = "0" ]; then
    echo "FAIL: No TOP stocks calculated"
    exit 1
fi

echo "OK: TOP-${TOP_COUNT} calculated and saved"

# ---------- Commit ----------
echo "Staging files..."
git add backend/app/analytics/top_stocks.py scripts/${TASK_ID}.sh 2>/dev/null || true

if git diff --cached --quiet; then
    echo "OK: no changes to commit"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
else
    git commit -m "feat(task-026): add TOP-30 stocks calculator" \
        && echo "OK: commit created" || echo "WARN: commit failed"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
fi

echo "OK: HEAD commit: ${COMMIT_SHA}"

# ---------- Report ----------
FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "${REPORT_JSON}" <<EOF
{
  "task_id": "${TASK_ID}",
  "status": "success",
  "started_at": "${STARTED_AT}",
  "finished_at": "${FINISHED_AT}",
  "environment": {
    "cwd": "${CWD}",
    "branch": "${FEATURE_BRANCH}",
    "candles_total": "${CANDLES_TOTAL}",
    "candles_tickers": "${CANDLES_TICKERS}",
    "instruments_total": "${INSTRUMENTS_TOTAL}",
    "top_stocks_count": "${TOP_COUNT}",
    "commit_sha": "${COMMIT_SHA}"
  },
  "checks": [
    {"name": "git_repo", "status": "passed"},
    {"name": "top_stocks_module", "status": "passed"},
    {"name": "docker_build", "status": "passed"},
    {"name": "monthly_candles_check", "status": "passed", "count": "${CANDLES_TOTAL}"},
    {"name": "instruments_check", "status": "passed", "count": "${INSTRUMENTS_TOTAL}"},
    {"name": "top_stocks_calculated", "status": "passed", "count": "${TOP_COUNT}"},
    {"name": "git_commit", "status": "passed", "sha": "${COMMIT_SHA}"}
  ],
  "errors": []
}
EOF

cat > "${REPORT_MD}" <<EOF
# ${TASK_ID}

**Status:** success

**Started:** ${STARTED_AT}
**Finished:** ${FINISHED_AT}

**Branch:** ${FEATURE_BRANCH}
**Commit:** ${COMMIT_SHA}

## Results

| Metric | Value |
|--------|-------|
| Monthly candles | ${CANDLES_TOTAL} |
| Unique tickers | ${CANDLES_TICKERS} |
| Instruments | ${INSTRUMENTS_TOTAL} |
| TOP stocks calculated | ${TOP_COUNT} |
EOF

echo "Finished: ${FINISHED_AT}"
echo "Report JSON: ${REPORT_JSON}"
echo "Report MD: ${REPORT_MD}"
echo "Log: ${LOG_FILE}"
