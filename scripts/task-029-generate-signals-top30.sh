#!/usr/bin/env bash
set -u

TASK_ID="task-029-generate-signals-top30"
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

# ---------- Step 1: Generate signals for TOP-30 ----------
echo ""
echo "=== Step 1: Generating signals for TOP-30 tickers ==="

# Python-скрипт для генерации сигналов и вывода JSON-отчёта в stdout
PYTHON_SCRIPT=$(cat <<'PYTHON_EOF'
import json
import time
import sys
import traceback
from app.analytics.signal_generator import SignalGenerator

def main():
    start = time.time()
    gen = SignalGenerator()
    
    tickers = gen.get_top_tickers(limit=30)
    if not tickers:
        print(json.dumps({"error": "No tickers found in top_stocks_by_volume"}))
        sys.exit(1)
    
    print(f"Tickers count: {len(tickers)}", file=sys.stderr)
    
    report = gen.scan_and_save_signals(
        tickers=tickers,
        timeframes=['30min', '1h', '4h', '1d'],
        lookback=1000
    )
    
    report['duration_sec'] = round(time.time() - start, 2)
    report['tickers_scanned'] = len(tickers)
    report['tickers_list'] = tickers
    
    gen.close()
    
    # Выводим JSON-отчёт в stdout
    print(json.dumps(report, default=str, ensure_ascii=False))
    
if __name__ == "__main__":
    main()
PYTHON_EOF
)

# Запускаем контейнер и передаём Python-скрипт через stdin
OUTPUT=$(docker compose run --rm -T backend python -c "$PYTHON_SCRIPT" 2>&1)
EXIT_CODE=$?

# Сохраняем сырой вывод в лог
echo "$OUTPUT" >> "${LOG_FILE}"

if [ $EXIT_CODE -ne 0 ]; then
    echo "ERROR: Signal generation failed with exit code $EXIT_CODE"
    echo '{"status": "failed", "task_id": "'"${TASK_ID}"'", "error": "Signal generation failed"}'
    exit 1
fi

# Извлекаем последнюю строку вывода (она должна быть JSON-отчётом)
REPORT_JSON_CONTENT=$(echo "$OUTPUT" | tail -n 1)

# Проверяем, что это валидный JSON
if ! echo "$REPORT_JSON_CONTENT" | jq empty 2>/dev/null; then
    echo "ERROR: Failed to parse JSON report from output"
    echo "Last line: $REPORT_JSON_CONTENT"
    echo '{"status": "failed", "task_id": "'"${TASK_ID}"'", "error": "Invalid JSON output"}'
    exit 1
fi

# Сохраняем JSON-отчёт в файл
echo "$REPORT_JSON_CONTENT" > "${REPORT_JSON}"

# Извлекаем ключевые поля для отчёта в markdown
TOTAL_SIGNALS=$(echo "$REPORT_JSON_CONTENT" | jq -r '.total_signals_saved // 0')
TOTAL_CANDLES=$(echo "$REPORT_JSON_CONTENT" | jq -r '.total_candles_analyzed // 0')
ERRORS_COUNT=$(echo "$REPORT_JSON_CONTENT" | jq -r '.errors | length // 0')
DURATION=$(echo "$REPORT_JSON_CONTENT" | jq -r '.duration_sec // 0')

echo "✅ Generated $TOTAL_SIGNALS signals from $TOTAL_CANDLES candles in ${DURATION}s"

# ---------- Step 2: Create human-readable report ----------
cat > "${REPORT_MD}" <<EOF
# ${TASK_ID}

**Status:** success

**Started:** ${STARTED_AT}
**Finished:** $(date -u +%Y-%m-%dT%H:%M:%SZ)

**Branch:** ${CURRENT_BRANCH}

## Results

| Metric | Value |
|--------|-------|
| Tickers scanned | $(echo "$REPORT_JSON_CONTENT" | jq -r '.tickers_scanned // 0') |
| Total signals saved | ${TOTAL_SIGNALS} |
| Candles analyzed | ${TOTAL_CANDLES} |
| Errors | ${ERRORS_COUNT} |
| Duration | ${DURATION} sec |

## Pattern statistics

$(echo "$REPORT_JSON_CONTENT" | jq -r '.pattern_statistics | to_entries | sort_by(.value) | reverse | map("| \(.key) | \(.value) |") | join("\n")' | sed 's/^/| Pattern | Count |\n|--------|-------|\n/')

EOF

# ---------- Step 3: Commit ----------
echo ""
echo "=== Step 3: Committing results ==="

git add scripts/${TASK_ID}.sh reports/${TASK_ID}/ 2>/dev/null || true

if git diff --cached --quiet; then
    echo "OK: no changes to commit"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
else
    git commit -m "feat(task-029): generate signals for TOP-30 tickers" \
        && echo "OK: commit created" || echo "WARN: commit failed"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
fi

echo "OK: HEAD commit: ${COMMIT_SHA}"

# ---------- Final report ----------
FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Обновляем JSON-отчёт, добавляя метаданные
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
