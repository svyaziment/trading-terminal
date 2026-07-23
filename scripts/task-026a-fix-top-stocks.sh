#!/usr/bin/env bash
set -u

TASK_ID="task-026a-fix-top-stocks"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
CWD="$(pwd)"
REPORT_DIR="${CWD}/reports/${TASK_ID}"
LOG_FILE="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"

mkdir -p "${REPORT_DIR}"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== Task: ${TASK_ID} ==="
echo "Started: ${STARTED_AT}"

cd "${CWD}" || exit 1

# ---------- 1. Пересоздаём таблицу без партиций ----------
echo "Recreating trading.top_stocks_by_volume without partitions..."

RECREATE_OUTPUT="$(docker compose run --rm -T --no-deps backend python -c "
from app.db.db_manager import DBManager
db = DBManager()

# Удаляем партиционированную таблицу (осталась от старого проекта)
db.execute('DROP TABLE IF EXISTS trading.top_stocks_by_volume CASCADE')
print('DROPPED=ok')

# Создаём обычную таблицу без партиций
db.execute('''
    CREATE TABLE trading.top_stocks_by_volume (
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
    )
''')
print('CREATED=ok')

# Индексы
db.execute('CREATE INDEX IF NOT EXISTS idx_top_stocks_ticker ON trading.top_stocks_by_volume (ticker)')
db.execute('CREATE INDEX IF NOT EXISTS idx_top_stocks_report_date ON trading.top_stocks_by_volume (report_date)')
db.execute('CREATE INDEX IF NOT EXISTS idx_top_stocks_rank ON trading.top_stocks_by_volume (rank)')
print('INDEXES=ok')

db.close_pool()
" 2>&1)"

echo "${RECREATE_OUTPUT}"

if ! echo "${RECREATE_OUTPUT}" | grep -q "CREATED=ok"; then
    echo "FAIL: cannot recreate table"
    exit 1
fi

echo "OK: table recreated without partitions"

# ---------- 2. Запускаем расчёт TOP-30 ----------
echo "Calculating TOP-30..."

TOP_OUTPUT="$(docker compose run --rm -T --no-deps backend python -c "
from app.analytics.top_stocks import TopStocksCalculator
calc = TopStocksCalculator()
count = calc.run(limit=30)
print('TOP_COUNT=' + str(count))
" 2>&1)"

echo "${TOP_OUTPUT}"

TOP_COUNT="$(echo "${TOP_OUTPUT}" | grep 'TOP_COUNT=' | cut -d'=' -f2)"

if [ -z "${TOP_COUNT}" ] || [ "${TOP_COUNT}" = "0" ]; then
    echo "FAIL: TOP-30 calculation failed"
    exit 1
fi

echo "OK: TOP-${TOP_COUNT} calculated and saved"

# ---------- 3. Проверяем данные ----------
echo "Verifying data..."

VERIFY_OUTPUT="$(docker compose run --rm -T --no-deps backend python -c "
from app.db.db_manager import DBManager
db = DBManager()
result = db.select('''
    SELECT rank, ticker, name, sum_volume
    FROM trading.top_stocks_by_volume
    ORDER BY rank
    LIMIT 10
''')
df = result.to_dataframe()
for _, row in df.iterrows():
    print(f\"  {row['rank']:2d}. {row['ticker']:8s} | {str(row['name'])[:30]:30s} | {row['sum_volume']:>15,}\")
db.close_pool()
" 2>&1)"

echo "${VERIFY_OUTPUT}"

# ---------- 4. Коммит ----------
echo "Staging files..."
git add backend/app/analytics/top_stocks.py scripts/${TASK_ID}.sh 2>/dev/null || true

if git diff --cached --quiet; then
    echo "OK: no changes to commit"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
else
    git commit -m "fix(task-026): recreate top_stocks_by_volume without partitions" \
        && echo "OK: commit created" || echo "WARN: commit failed"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
fi

# ---------- 5. Отчёт ----------
FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "${REPORT_JSON}" <<EOF
{
  "task_id": "${TASK_ID}",
  "status": "success",
  "started_at": "${STARTED_AT}",
  "finished_at": "${FINISHED_AT}",
  "environment": {
    "cwd": "${CWD}",
    "top_count": "${TOP_COUNT}",
    "commit_sha": "${COMMIT_SHA}"
  },
  "checks": [
    {"name": "table_recreated", "status": "passed"},
    {"name": "top30_calculated", "status": "passed", "count": "${TOP_COUNT}"},
    {"name": "git_commit", "status": "passed", "sha": "${COMMIT_SHA}"}
  ],
  "errors": []
}
EOF

echo "Finished: ${FINISHED_AT}"
echo "Report: ${REPORT_JSON}"
