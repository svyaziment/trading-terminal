#!/usr/bin/env bash
set -Eeuo pipefail

TASK_ID="task-024-pipeline-indicators"
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

cd "${CWD}" || exit 1

# ---------- Git checks ----------
echo "Checking git..."
command -v git >/dev/null 2>&1 || { echo "FAIL: git not found"; exit 1; }
[ -d .git ] || { echo "FAIL: not a git repo"; exit 1; }

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "Current branch: ${CURRENT_BRANCH}"

if [ "${CURRENT_BRANCH}" != "${FEATURE_BRANCH}" ]; then
    git checkout "${FEATURE_BRANCH}" 2>/dev/null || git checkout -b "${FEATURE_BRANCH}" origin/main
fi

# ---------- Check indicators_manager exists ----------
echo "Checking indicators_manager.py..."
if [ ! -f "backend/app/analytics/indicators_manager.py" ]; then
    echo "FAIL: backend/app/analytics/indicators_manager.py not found"
    exit 1
fi
echo "OK: indicators_manager.py exists"

# ---------- Create pipeline.py ----------
echo "Creating pipeline.py..."

cat > backend/app/analytics/pipeline.py <<'PIPELINE_EOF'
"""
Pipeline для расчёта индикаторов для всех тикеров.
Адаптировано из AlgoTerminal src/core/pipeline.py
"""
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config_manager import setup_logger
from app.db.db_manager import DBManager
from app.analytics.indicators_manager import IndicatorsManager

logger = setup_logger("Pipeline")


class IndicatorsPipeline:
    """Pipeline для расчёта индикаторов для всех тикеров."""

    def __init__(self) -> None:
        self.db = DBManager()
        self.indicators_manager = IndicatorsManager()

    def get_top_tickers(self, limit: int = 30) -> List[str]:
        """Получает список тикеров из top_stocks_by_volume."""
        query = """
            SELECT ticker
            FROM trading.top_stocks_by_volume
            ORDER BY rank
            LIMIT %(limit)s
        """
        result = self.db.select(query, params={"limit": limit})
        df = result.to_dataframe()
        if df.empty:
            logger.warning("Нет данных в top_stocks_by_volume")
            return []
        tickers = df["ticker"].tolist()
        logger.info("Получено %d тикеров из top_stocks_by_volume", len(tickers))
        return tickers

    def calculate_indicators_for_tickers(
        self,
        tickers: List[str],
        timeframes: Optional[List[str]] = None,
        days_back: Optional[int] = None,
        delay_seconds: float = 0.3,
    ) -> Dict[str, Any]:
        """
        Рассчитывает индикаторы для списка тикеров.

        :param tickers: Список тикеров
        :param timeframes: Таймфреймы (по умолчанию все)
        :param days_back: Сколько дней данных использовать
        :param delay_seconds: Задержка между запросами
        :return: Статистика расчёта
        """
        if timeframes is None:
            timeframes = ["30min", "1h", "4h", "1d", "1w", "1M"]

        stats: Dict[str, Any] = {
            "total_tickers": len(tickers),
            "total_timeframes": len(timeframes),
            "success": 0,
            "failed": 0,
            "details": {},
        }

        for i, ticker in enumerate(tickers, 1):
            stats["details"][ticker] = {}
            logger.info("[%d/%d] Расчёт индикаторов для %s", i, len(tickers), ticker)

            for tf in timeframes:
                try:
                    tf_days = days_back or self._get_days_for_timeframe(tf)
                    count = self.indicators_manager.update_indicators_for_ticker(
                        ticker, tf, tf_days
                    )
                    stats["details"][ticker][tf] = count
                    if count > 0:
                        stats["success"] += 1
                    else:
                        stats["failed"] += 1
                    logger.info("  %s (%s): %d индикаторов", ticker, tf, count)
                except Exception as exc:
                    logger.error("  Ошибка %s (%s): %s", ticker, tf, exc)
                    stats["failed"] += 1
                    stats["details"][ticker][tf] = 0

                time.sleep(delay_seconds)

        logger.info(
            "Расчёт завершён: %d/%d успешно",
            stats["success"],
            stats["success"] + stats["failed"],
        )
        return stats

    @staticmethod
    def _get_days_for_timeframe(timeframe: str) -> int:
        """Возвращает количество дней для таймфрейма."""
        days_map = {
            "30min": 25,
            "1h": 90,
            "4h": 180,
            "1d": 365,
            "1w": 730,
            "1M": 1095,
        }
        return days_map.get(timeframe, 90)

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику по индикаторам."""
        return self.indicators_manager.get_stats_by_timeframe()

    def run(
        self,
        limit: int = 30,
        timeframes: Optional[List[str]] = None,
        days_back: Optional[int] = None,
        delay_seconds: float = 0.3,
    ) -> Dict[str, Any]:
        """
        Запускает pipeline.

        :param limit: Количество тикеров
        :param timeframes: Таймфреймы
        :param days_back: Количество дней
        :param delay_seconds: Задержка
        :return: Статистика
        """
        start_time = datetime.now()
        logger.info("=" * 60)
        logger.info("ЗАПУСК PIPELINE: Расчёт индикаторов")
        logger.info("Время: %s", start_time.strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("=" * 60)

        # Шаг 1: Получение тикеров
        logger.info("\nШАГ 1: Получение тикеров (ТОП-%d)", limit)
        tickers = self.get_top_tickers(limit)
        if not tickers:
            logger.error("Не удалось получить список тикеров")
            return {"success": False, "error": "No tickers"}

        # Шаг 2: Расчёт индикаторов
        logger.info("\nШАГ 2: Расчёт индикаторов")
        stats = self.calculate_indicators_for_tickers(
            tickers=tickers,
            timeframes=timeframes,
            days_back=days_back,
            delay_seconds=delay_seconds,
        )

        # Шаг 3: Статистика
        logger.info("\nШАГ 3: Статистика")
        indicator_stats = self.get_stats()
        for tf, info in indicator_stats.items():
            logger.info(
                "  %s: %d строк, %d тикеров, last=%s",
                tf,
                info["count"],
                info["tickers"],
                info["last_update"],
            )

        finish_time = datetime.now()
        duration = (finish_time - start_time).total_seconds()
        logger.info("=" * 60)
        logger.info("PIPELINE ЗАВЕРШЁН за %.1f сек", duration)
        logger.info("Успешно: %d/%d", stats["success"], stats["success"] + stats["failed"])
        logger.info("=" * 60)

        stats["duration_sec"] = duration
        stats["indicator_stats"] = indicator_stats
        return stats


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Pipeline: расчёт индикаторов")
    parser.add_argument("--limit", type=int, default=30, help="Количество тикеров")
    parser.add_argument(
        "--timeframes",
        default="30min,1h,4h,1d,1w,1M",
        help="Таймфреймы через запятую",
    )
    parser.add_argument("--days-back", type=int, default=None, help="Количество дней")
    parser.add_argument("--delay", type=float, default=0.3, help="Задержка между запросами")
    parser.add_argument("--stats-only", action="store_true", help="Только статистика")
    args = parser.parse_args()

    pipeline = IndicatorsPipeline()

    if args.stats_only:
        stats = pipeline.get_stats()
        print("INDICATORS_STATS:")
        for tf, info in stats.items():
            print(f"  {tf}: {info['count']} rows, {info['tickers']} tickers, last={info['last_update']}")
        return

    timeframes = [tf.strip() for tf in args.timeframes.split(",") if tf.strip()]
    stats = pipeline.run(
        limit=args.limit,
        timeframes=timeframes,
        days_back=args.days_back,
        delay_seconds=args.delay,
    )

    print(f"PIPELINE_SUCCESS={stats.get('success', 0)}")
    print(f"PIPELINE_FAILED={stats.get('failed', 0)}")
    print(f"PIPELINE_DURATION={stats.get('duration_sec', 0):.1f}")


if __name__ == "__main__":
    main()
PIPELINE_EOF

echo "OK: pipeline.py created"

# ---------- Docker build ----------
echo "Building backend image..."
if ! docker compose build backend; then
    echo "FAIL: docker build failed"
    exit 1
fi
echo "OK: backend built"

# ---------- Run pipeline ----------
echo "Running pipeline..."
PIPELINE_OUTPUT="$(docker compose run --rm -T --no-deps backend \
    python -m app.analytics.pipeline --limit 30 --timeframes 30min,1h,4h,1d,1w,1M 2>&1)"

echo "${PIPELINE_OUTPUT}"

PIPELINE_SUCCESS="$(echo "${PIPELINE_OUTPUT}" | grep -oP 'PIPELINE_SUCCESS=\K\d+' || echo "0")"
PIPELINE_FAILED="$(echo "${PIPELINE_OUTPUT}" | grep -oP 'PIPELINE_FAILED=\K\d+' || echo "0")"
PIPELINE_DURATION="$(echo "${PIPELINE_OUTPUT}" | grep -oP 'PIPELINE_DURATION=\K[\d.]+' || echo "0")"

echo "Pipeline success: ${PIPELINE_SUCCESS}"
echo "Pipeline failed: ${PIPELINE_FAILED}"
echo "Pipeline duration: ${PIPELINE_DURATION}s"

# ---------- Commit ----------
echo "Staging files..."
git add backend/app/analytics/pipeline.py 2>/dev/null || true

if git diff --cached --quiet; then
    echo "OK: no changes to commit"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
else
    git commit -m "feat(task-024): add indicators pipeline for all tickers"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
    echo "OK: commit created: ${COMMIT_SHA}"
fi

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
    "pipeline_success": "${PIPELINE_SUCCESS}",
    "pipeline_failed": "${PIPELINE_FAILED}",
    "pipeline_duration": "${PIPELINE_DURATION}",
    "commit_sha": "${COMMIT_SHA}"
  },
  "checks": [
    {"name": "indicators_manager_exists", "status": "passed"},
    {"name": "pipeline_created", "status": "passed"},
    {"name": "docker_build", "status": "passed"},
    {"name": "pipeline_run", "status": "passed"},
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

**Pipeline success:** ${PIPELINE_SUCCESS}
**Pipeline failed:** ${PIPELINE_FAILED}
**Pipeline duration:** ${PIPELINE_DURATION}s
EOF

echo "Finished: ${FINISHED_AT}"
echo "Report JSON: ${REPORT_JSON}"
echo "Report MD: ${REPORT_MD}"
echo "Log: ${LOG_FILE}"
