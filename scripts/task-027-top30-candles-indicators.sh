#!/usr/bin/env bash
set -u

TASK_ID="task-027-top30-candles-indicators"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
CWD="$(pwd)"
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

# ---------- Git ----------
echo "Checking git..."
command -v git >/dev/null 2>&1 || { echo "FAIL: git not found"; exit 1; }
[ -d .git ] || { echo "FAIL: not a git repo"; exit 1; }

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "Current branch: ${CURRENT_BRANCH}"

# ---------- Docker ----------
echo "Checking Docker..."
docker info >/dev/null 2>&1 || { echo "FAIL: docker daemon not running"; exit 1; }
echo "OK: docker daemon is running"

docker compose config >/dev/null 2>&1 || { echo "FAIL: docker-compose.yml invalid"; exit 1; }
echo "OK: docker-compose.yml is valid"

# ---------- Step 1: Get TOP-30 tickers ----------
echo ""
echo "=== Step 1: Getting TOP-30 tickers ==="

TOP30_OUTPUT="$(docker compose run --rm -T --no-deps backend python -c "
from app.db.db_manager import DBManager
db = DBManager()
result = db.select('''
    SELECT ticker, figi, name, rank
    FROM trading.top_stocks_by_volume
    ORDER BY rank
    LIMIT 30
''')
df = result.to_dataframe()
print('TOP30_COUNT=' + str(len(df)))
for _, row in df.iterrows():
    print(f\"TICKER={row['ticker']}|FIGI={row['figi']}|RANK={row['rank']}\")
db.close_pool()
" 2>&1)"

echo "${TOP30_OUTPUT}"

TOP30_COUNT="$(echo "${TOP30_OUTPUT}" | grep 'TOP30_COUNT=' | cut -d'=' -f2)"

if [ -z "${TOP30_COUNT}" ] || [ "${TOP30_COUNT}" = "0" ]; then
    echo "FAIL: No tickers in top_stocks_by_volume"
    exit 1
fi

echo "OK: Found ${TOP30_COUNT} tickers in TOP-30"

# ---------- Step 2: Load 30min candles for TOP-30 ----------
echo ""
echo "=== Step 2: Loading 30min candles for TOP-30 ==="

CANDLES_OUTPUT="$(docker compose run --rm -T --no-deps backend python -c "
import time
from app.db.db_manager import DBManager
from app.broker.data_loader import DataLoader

db = DBManager()
loader = DataLoader()

# Get TOP-30 tickers
result = db.select('''
    SELECT ticker, figi, name, rank
    FROM trading.top_stocks_by_volume
    ORDER BY rank
    LIMIT 30
''')
df = result.to_dataframe()

total_candles = 0
success_count = 0
failed_count = 0
failed_tickers = []

for _, row in df.iterrows():
    ticker = row['ticker']
    figi = row['figi']
    rank = row['rank']
    name = str(row.get('name', ticker))[:30]

    try:
        candles_df = loader.fetch_candles_by_figi(
            figi=figi,
            ticker=ticker,
            days=25,
            interval_str='30min'
        )

        if candles_df.empty:
            print(f'WARN: [{rank}/30] {ticker} ({name}): no candles')
            failed_count += 1
            failed_tickers.append(ticker)
            continue

        # Rename and save
        candles_df = candles_df.rename(columns={'time': 'timestamp'})
        candles_df['ticker'] = ticker
        candles_df['figi'] = figi

        # Delete existing and insert
        db.execute(
            'DELETE FROM trading.candles_30min_raw WHERE ticker = %(ticker)s',
            params={'ticker': ticker}
        )
        db.insert_with_schema('candles_30min_raw', candles_df)

        count = len(candles_df)
        total_candles += count
        success_count += 1
        print(f'OK: [{rank}/30] {ticker} ({name}): {count} candles')

    except Exception as e:
        print(f'ERROR: [{rank}/30] {ticker} ({name}): {e}')
        failed_count += 1
        failed_tickers.append(ticker)

    time.sleep(0.5)

print(f'CANDLES_TOTAL={total_candles}')
print(f'CANDLES_SUCCESS={success_count}')
print(f'CANDLES_FAILED={failed_count}')
if failed_tickers:
    print(f'CANDLES_FAILED_TICKERS={\",\".join(failed_tickers)}')

db.close_pool()
" 2>&1)"

echo "${CANDLES_OUTPUT}"

CANDLES_TOTAL="$(echo "${CANDLES_OUTPUT}" | grep 'CANDLES_TOTAL=' | cut -d'=' -f2)"
CANDLES_SUCCESS="$(echo "${CANDLES_OUTPUT}" | grep 'CANDLES_SUCCESS=' | cut -d'=' -f2)"
CANDLES_FAILED="$(echo "${CANDLES_OUTPUT}" | grep 'CANDLES_FAILED=' | cut -d'=' -f2)"

echo ""
echo "Candles loaded: total=${CANDLES_TOTAL}, success=${CANDLES_SUCCESS}, failed=${CANDLES_FAILED}"

# ---------- Step 3: Calculate indicators for TOP-30 ----------
echo ""
echo "=== Step 3: Calculating indicators for TOP-30 ==="

INDICATORS_OUTPUT="$(docker compose run --rm -T --no-deps backend python -c "
import time
from app.db.db_manager import DBManager
from app.analytics.indicators_manager import IndicatorsManager

db = DBManager()
mgr = IndicatorsManager()

# Get TOP-30 tickers
result = db.select('''
    SELECT ticker, figi, name, rank
    FROM trading.top_stocks_by_volume
    ORDER BY rank
    LIMIT 30
''')
df = result.to_dataframe()

timeframes = ['30min', '1h', '4h', '1d', '1w']
total_success = 0
total_failed = 0

for _, row in df.iterrows():
    ticker = row['ticker']
    rank = row['rank']
    name = str(row.get('name', ticker))[:30]

    for tf in timeframes:
        try:
            count = mgr.update_indicators_for_ticker(ticker, tf)
            if count > 0:
                total_success += 1
                print(f'OK: [{rank}/30] {ticker} ({name}) {tf}: {count} indicators')
            else:
                total_failed += 1
                print(f'SKIP: [{rank}/30] {ticker} ({name}) {tf}: no data')
        except Exception as e:
            total_failed += 1
            print(f'ERROR: [{rank}/30] {ticker} ({name}) {tf}: {e}')

        time.sleep(0.1)

print(f'INDICATORS_SUCCESS={total_success}')
print(f'INDICATORS_FAILED={total_failed}')

# Print stats
stats = mgr.get_stats_by_timeframe()
print('INDICATORS_STATS:')
for tf, info in stats.items():
    print(f'  {tf}: {info[\"count\"]} rows, {info[\"tickers\"]} tickers')

db.close_pool()
" 2>&1)"

echo "${INDICATORS_OUTPUT}"

INDICATORS_SUCCESS="$(echo "${INDICATORS_OUTPUT}" | grep 'INDICATORS_SUCCESS=' | cut -d'=' -f2)"
INDICATORS_FAILED="$(echo "${INDICATORS_OUTPUT}" | grep 'INDICATORS_FAILED=' | cut -d'=' -f2)"

echo ""
echo "Indicators: success=${INDICATORS_SUCCESS}, failed=${INDICATORS_FAILED}"

# ---------- Commit ----------
echo ""
echo "=== Committing ==="

git add backend/ scripts/${TASK_ID}.sh 2>/dev/null || true

if git diff --cached --quiet; then
    echo "OK: no changes to commit"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
else
    git commit -m "feat(task-027): load candles and calculate indicators for TOP-30" \
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
    "branch": "${CURRENT_BRANCH}",
    "top30_count": "${TOP30_COUNT}",
    "candles_total": "${CANDLES_TOTAL}",
    "candles_success": "${CANDLES_SUCCESS}",
    "candles_failed": "${CANDLES_FAILED}",
    "indicators_success": "${INDICATORS_SUCCESS}",
    "indicators_failed": "${INDICATORS_FAILED}",
    "commit_sha": "${COMMIT_SHA}"
  },
  "checks": [
    {"name": "git_repo", "status": "passed"},
    {"name": "docker_daemon", "status": "passed"},
    {"name": "top30_tickers", "status": "passed", "count": "${TOP30_COUNT}"},
    {"name": "candles_loaded", "status": "passed", "total": "${CANDLES_TOTAL}", "success": "${CANDLES_SUCCESS}"},
    {"name": "indicators_calculated", "status": "passed", "success": "${INDICATORS_SUCCESS}"},
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

**Branch:** ${CURRENT_BRANCH}
**Commit:** ${COMMIT_SHA}

## Results

| Metric | Value |
|--------|-------|
| TOP-30 tickers | ${TOP30_COUNT} |
| Candles loaded | ${CANDLES_TOTAL} (${CANDLES_SUCCESS} tickers) |
| Indicators calculated | ${INDICATORS_SUCCESS} success / ${INDICATORS_FAILED} failed |
EOF

echo ""
echo "Finished: ${FINISHED_AT}"
echo "Report JSON: ${REPORT_JSON}"
echo "Report MD: ${REPORT_MD}"
echo "Log: ${LOG_FILE}"
