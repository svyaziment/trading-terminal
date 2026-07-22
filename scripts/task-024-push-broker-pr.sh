#!/usr/bin/env bash
set -Eeuo pipefail

TASK_ID="task-024-push-broker-pr"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"

FEATURE_BRANCH="feat/broker-data-loader"
BASE_BRANCH="main"
PR_TITLE="feat: Tinkoff data loader for 30min candles"
PR_BODY="Add Tinkoff Invest API data loader.

- Loads 30min candles via t-tech-investments SDK
- Supports interval mapping (30min, 1h, 4h, 1d, 1w, 1M)
- SSL configuration for T-Bank API
- Verified: 193 candles loaded successfully"

mkdir -p "$REPORT_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Task: ${TASK_ID} ==="

cd "$ROOT_DIR"

# Коммитим data_loader если есть изменения
git add backend/app/broker/ scripts/ 2>/dev/null || true
if ! git diff --cached --quiet; then
  git commit -m "feat(task-023): Tinkoff data loader for 30min candles"
  echo "OK: commit created"
else
  echo "OK: no changes to commit"
fi

# Пушим ветку
git push -u origin "$FEATURE_BRANCH"
echo "OK: branch pushed"

# Создаём PR
PR_URL="$(gh pr create \
  --title "$PR_TITLE" \
  --body "$PR_BODY" \
  --base "$BASE_BRANCH" \
  --head "$FEATURE_BRANCH" 2>&1)"

echo "PR_URL=$PR_URL"

cat > "$REPORT_JSON" <<EOF
{
  "task_id": "$TASK_ID",
  "status": "success",
  "pr_url": "$PR_URL",
  "branch": "$FEATURE_BRANCH"
}
EOF

echo "=== Done ==="
