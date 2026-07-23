#!/usr/bin/env bash
set -u

TASK_ID="task-030-generate-signals-with-more-data"
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

# ---------- Step 1: Get tickers (TOP-30) ----------
echo ""
echo "=== Step 1: Getting TOP-30 tickers ==="

TICKERS_JSON=$(docker compose run --rm -T backend python -c "
from app.db.db_manager import DBManager
import json
db = DBManager()
result = db.select('''
    SELECT ticker, figi, name, rank
    FROM trading.top_stocks_by_volume
    ORDER BY rank
    LIMIT 30
''')
df = result.to_dataframe()
tickers = df['ticker'].tolist()
print(json.dumps(tickers))
db.close_pool()
")

if [[ -z "$TICKERS_JSON" || "$TICKERS_JSON" == "[]" ]]; then
    echo "ERROR: No tickers found in top_stocks_by_volume"
    echo '{"status": "failed", "task_id": "'"${TASK_ID}"'", "error": "No tickers"}'
    exit 1
fi

# Clean up JSON (remove any extra output)
TICKERS_JSON=$(echo "$TICKERS_JSON" | grep -E '^\[.*\]$' | head -1)
if [[ -z "$TICKERS_JSON" ]]; then
    echo "ERROR: Failed to parse tickers JSON"
    exit 1
fi

echo "Tickers: $TICKERS_JSON"

# ---------- Step 2: Load 30min candles for 365 days ----------
echo ""
echo "=== Step 2: Loading 30min candles for 365 days (rate-limited) ==="

LOAD_OUTPUT=$(docker compose run --rm -T backend python -c "
import json
import time
import sys
from app.db.db_manager import DBManager
from app.broker.data_loader import DataLoader

tickers = $TICKERS_JSON
db = DBManager()
loader = DataLoader()

success = 0
failed = 0
failed_tickers = []

for ticker in tickers:
    # Get figi
    result = db.select('SELECT figi FROM trading.instruments WHERE ticker = %(ticker)s', params={'ticker': ticker})
    df = result.to_dataframe()
    if df.empty:
        print(f'ERROR: No figi for {ticker}', file=sys.stderr)
        failed += 1
        failed_tickers.append(ticker)
        continue
    figi = df.iloc[0]['figi']
    
    try:
        candles_df = loader.fetch_candles_by_figi(
            figi=figi,
            ticker=ticker,
            days=365,          # Максимально возможный период
            interval_str='30min'
        )
        if candles_df.empty:
            print(f'WARN: No candles for {ticker}', file=sys.stderr)
            failed += 1
            failed_tickers.append(ticker)
            continue
        
        # Rename and save
        candles_df = candles_df.rename(columns={'time': 'timestamp'})
        candles_df['ticker'] = ticker
        candles_df['figi'] = figi
        
        # Overwrite
        db.execute('DELETE FROM trading.candles_30min_raw WHERE ticker = %(ticker)s', params={'ticker': ticker})
        db.insert_with_schema('candles_30min_raw', candles_df)
        
        count = len(candles_df)
        print(f'OK: {ticker} -> {count} candles', file=sys.stderr)
        success += 1
    except Exception as e:
        print(f'ERROR: {ticker} -> {e}', file=sys.stderr)
        failed += 1
        failed_tickers.append(ticker)
    
    time.sleep(0.5)  # избегаем троттлинга

print(json.dumps({'success': success, 'failed': failed, 'failed_tickers': failed_tickers}))
db.close_pool()
")

echo "$LOAD_OUTPUT"
LOAD_RESULT=$(echo "$LOAD_OUTPUT" | grep -E '^\{.*\}$' | head -1)
if [[ -z "$LOAD_RESULT" ]]; then
    echo "WARNING: Could not parse load result, but continuing..."
else
    SUCCESS_COUNT=$(echo "$LOAD_RESULT" | jq -r '.success')
    FAILED_COUNT=$(echo "$LOAD_RESULT" | jq -r '.failed')
    echo "Load result: success=$SUCCESS_COUNT, failed=$FAILED_COUNT"
fi

# ---------- Step 3: Aggregate candles (all timeframes) ----------
echo ""
echo "=== Step 3: Aggregating candles for all timeframes ==="

AGGREGATE_OUTPUT=$(docker compose run --rm -T backend python -c "
import time
from app.db.db_manager import DBManager
from app.analytics.aggregate_candles import AggregateCandles

db = DBManager()
aggr = AggregateCandles(db)
tickers = $TICKERS_JSON
timeframes = ['1h', '4h', '1d', '1w', '1M']

total_agg = 0
for ticker in tickers:
    for tf in timeframes:
        try:
            count = aggr.aggregate_for_ticker(ticker, tf)
            if count > 0:
                total_agg += count
                print(f'OK: {ticker} {tf} -> {count} rows', flush=True)
            else:
                print(f'SKIP: {ticker} {tf} no data', flush=True)
        except Exception as e:
            print(f'ERROR: {ticker} {tf} -> {e}', flush=True)
        time.sleep(0.1)
print(f'TOTAL_AGGREGATED={total_agg}')
db.close_pool()
")

echo "$AGGREGATE_OUTPUT"
TOTAL_AGG=$(echo "$AGGREGATE_OUTPUT" | grep 'TOTAL_AGGREGATED=' | tail -1 | cut -d'=' -f2)
echo "Total aggregated rows: $TOTAL_AGG"

# ---------- Step 4: Calculate indicators ----------
echo ""
echo "=== Step 4: Calculating indicators ==="

INDICATORS_OUTPUT=$(docker compose run --rm -T backend python -c "
import time
from app.db.db_manager import DBManager
from app.analytics.indicators_manager import IndicatorsManager

db = DBManager()
mgr = IndicatorsManager()
tickers = $TICKERS_JSON
timeframes = ['30min', '1h', '4h', '1d', '1w', '1M']

total_ind = 0
for ticker in tickers:
    for tf in timeframes:
        try:
            count = mgr.update_indicators_for_ticker(ticker, tf)
            if count > 0:
                total_ind += count
                print(f'OK: {ticker} {tf} -> {count} indicators', flush=True)
            else:
                print(f'SKIP: {ticker} {tf} no data', flush=True)
        except Exception as e:
            print(f'ERROR: {ticker} {tf} -> {e}', flush=True)
        time.sleep(0.1)
print(f'TOTAL_INDICATORS={total_ind}')
db.close_pool()
")

echo "$INDICATORS_OUTPUT"
TOTAL_IND=$(echo "$INDICATORS_OUTPUT" | grep 'TOTAL_INDICATORS=' | tail -1 | cut -d'=' -f2)
echo "Total indicator rows: $TOTAL_IND"

# ---------- Step 5: Generate signals (with larger lookback) ----------
echo ""
echo "=== Step 5: Generating signals with lookback=2000 ==="

SIGNAL_OUTPUT=$(docker compose run --rm -T backend python -c "
import json
import time
import sys
from app.analytics.signal_generator import SignalGenerator

tickers = $TICKERS_JSON
gen = SignalGenerator()
start = time.time()

report = gen.scan_and_save_signals(
    tickers=tickers,
    timeframes=['30min', '1h', '4h', '1d'],
    lookback=2000
)

report['duration_sec'] = round(time.time() - start, 2)
report['tickers_scanned'] = len(tickers)
gen.close()
print(json.dumps(report, default=str, ensure_ascii=False))
")

# Extract JSON from output (ignore stderr lines)
REPORT_JSON_CONTENT=$(echo "$SIGNAL_OUTPUT" | grep -E '^\{' | head -1)

if [[ -z "$REPORT_JSON_CONTENT" ]]; then
    echo "ERROR: No JSON output from signal generation"
    echo '{"status": "failed", "task_id": "'"${TASK_ID}"'", "error": "No JSON output"}'
    exit 1
fi

# Validate JSON
if ! echo "$REPORT_JSON_CONTENT" | jq empty 2>/dev/null; then
    echo "WARNING: Invalid JSON, trying to salvage..."
    # Attempt to fix by taking last line
    REPORT_JSON_CONTENT=$(echo "$SIGNAL_OUTPUT" | tail -1)
    if ! echo "$REPORT_JSON_CONTENT" | jq empty 2>/dev/null; then
        echo "ERROR: Cannot parse JSON"
        exit 1
    fi
fi

echo "$REPORT_JSON_CONTENT" > "${REPORT_JSON}"

# Extract key stats
TOTAL_SIGNALS=$(echo "$REPORT_JSON_CONTENT" | jq -r '.total_signals_saved // 0')
ERRORS_COUNT=$(echo "$REPORT_JSON_CONTENT" | jq -r '.errors | length // 0')
DURATION=$(echo "$REPORT_JSON_CONTENT" | jq -r '.duration_sec // 0')

echo "✅ Generated $TOTAL_SIGNALS signals in ${DURATION}s, errors: $ERRORS_COUNT"

# ---------- Step 6: Create human-readable report ----------
cat > "${REPORT_MD}" <<EOF
# ${TASK_ID}

**Status:** success

**Started:** ${STARTED_AT}
**Finished:** $(date -u +%Y-%m-%dT%H:%M:%SZ)

**Branch:** ${CURRENT_BRANCH}

## Results

| Metric | Value |
|--------|-------|
| Tickers processed | $(echo "$REPORT_JSON_CONTENT" | jq -r '.tickers_scanned // 0') |
| Total signals saved | ${TOTAL_SIGNALS} |
| Candles analyzed | $(echo "$REPORT_JSON_CONTENT" | jq -r '.total_candles_analyzed // 0') |
| Errors | ${ERRORS_COUNT} |
| Duration | ${DURATION} sec |

## Aggregation / Indicators
- Aggregated rows: ${TOTAL_AGG:-N/A}
- Indicator rows: ${TOTAL_IND:-N/A}

## Pattern statistics

$(echo "$REPORT_JSON_CONTENT" | jq -r '.pattern_statistics | to_entries | sort_by(.value) | reverse | map("| \(.key) | \(.value) |") | join("\n")' | sed 's/^/| Pattern | Count |\n|--------|-------|\n/')

EOF

# ---------- Step 7: Commit ----------
echo ""
echo "=== Step 7: Committing results ==="

git add scripts/${TASK_ID}.sh reports/${TASK_ID}/ 2>/dev/null || true

if git diff --cached --quiet; then
    echo "OK: no changes to commit"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
else
    git commit -m "feat(task-030): load more data and generate signals for TOP-30" \
        && echo "OK: commit created" || echo "WARN: commit failed"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
fi

echo "OK: HEAD commit: ${COMMIT_SHA}"

# ---------- Final report ----------
FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

jq --arg task "$TASK_ID" \
   --arg started "$STARTED_AT" \
   --arg finished "$FINISHED_AT" \
   --arg branch "$CURRENT_BRANCH" \
   --arg commit "$COMMIT_SHA" \
   '. + {task_id: $task, started_at: $started, finished_at: $finished, branch: $branch, commit_sha: $commit}' \
   "${REPORT_JSON}" > "${REPORT_JSON}.tmp" && mv "${REPORT_JSON}.tmp" "${REPORT_JSON}"

echo ""
echo "Finished: ${FINISHED_AT}"
echo "Report JSON: ${REPORT_JSON}"
echo "Report MD: ${REPORT_MD}"
echo "Log: ${LOG_FILE}"
