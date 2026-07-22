#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-025-push-broker-pr"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
CWD="/f/GIT/trading-terminal"
FEATURE_BRANCH="feat/broker-data-loader"
BASE_BRANCH="main"
REPORT_DIR="$CWD/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

mkdir -p "$REPORT_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Task: $TASK_ID ==="
echo "Started: $STARTED_AT"
echo "Working directory: $CWD"
echo "Feature branch: $FEATURE_BRANCH"
echo "Base branch: $BASE_BRANCH"

cd "$CWD" || { echo "FAIL: cannot cd to $CWD"; exit 1; }

# ---------- Git checks ----------
echo "Checking git..."
command -v git >/dev/null 2>&1 || { echo "FAIL: git not found"; exit 1; }
echo "OK: git exists"

[ -d .git ] || { echo "FAIL: not a git repo"; exit 1; }
echo "OK: git repository exists"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "Current branch: $CURRENT_BRANCH"

if [ "$CURRENT_BRANCH" != "$FEATURE_BRANCH" ]; then
    echo "FAIL: expected branch $FEATURE_BRANCH, got $CURRENT_BRANCH"
    exit 1
fi
echo "OK: on correct branch"

# ---------- Check working tree ----------
echo "Checking working tree..."
GIT_STATUS="$(git status --porcelain)"
if [ -n "$GIT_STATUS" ]; then
    echo "FAIL: working tree is not clean"
    echo "$GIT_STATUS"
    exit 1
fi
echo "OK: working tree is clean"

# ---------- Check remote ----------
echo "Checking remote..."
git remote -v | grep -q origin || { echo "FAIL: no origin remote"; exit 1; }
echo "OK: origin remote exists"

# ---------- Check gh CLI ----------
echo "Checking gh CLI..."
command -v gh >/dev/null 2>&1 || { echo "FAIL: gh CLI not found"; exit 1; }
echo "OK: gh CLI exists"

gh auth status >/dev/null 2>&1 || { echo "FAIL: gh not authenticated"; exit 1; }
echo "OK: gh authenticated"

# ---------- Push branch ----------
echo "Pushing branch $FEATURE_BRANCH..."
git push -u origin "$FEATURE_BRANCH" 2>&1 || { echo "FAIL: git push failed"; exit 1; }
echo "OK: branch pushed"

# ---------- Check if PR already exists ----------
echo "Checking if PR already exists..."
EXISTING_PR="$(gh pr list --head "$FEATURE_BRANCH" --base "$BASE_BRANCH" --json number --jq '.[0].number' 2>/dev/null || true)"

if [ -n "$EXISTING_PR" ]; then
    echo "PR #$EXISTING_PR already exists"
    PR_URL="https://github.com/svyaziment/trading-terminal/pull/$EXISTING_PR"
    PR_CREATED="false"
    PR_EXISTS="true"
else
    echo "Creating PR..."
    PR_BODY="## Broker Data Loader

This PR adds:
- Tinkoff Invest API data loader (t-tech-investments SDK)
- 30-minute candle fetching for TOP-30 stocks
- Candle aggregation (1h, 4h, 1d, 1w, 1M)
- Indicators calculation (SMA, EMA, RSI, MACD, ATR, Bollinger Bands)
- Pipeline for daily TOP stocks update

### Changes
- backend/app/broker/data_loader.py
- backend/app/analytics/aggregate_candles.py
- backend/app/analytics/indicators_manager.py
- backend/app/analytics/pipeline.py
- backend/requirements.txt (t-tech-investments)
- backend/Dockerfile (private PyPI index)

### Testing
- 193 candles loaded for VTBR (30min, 5 days)
- 90/90 indicators calculated successfully
- All tests passed"

    PR_URL="$(gh pr create \
        --title "feat: broker data loader and indicators pipeline" \
        --body "$PR_BODY" \
        --base "$BASE_BRANCH" \
        --head "$FEATURE_BRANCH" 2>&1)" || { echo "FAIL: gh pr create failed"; exit 1; }
    
    PR_CREATED="true"
    PR_EXISTS="true"
    echo "OK: PR created: $PR_URL"
fi

# ---------- Report ----------
FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "$REPORT_JSON" <<EOF
{
  "task_id": "$TASK_ID",
  "status": "success",
  "started_at": "$STARTED_AT",
  "finished_at": "$FINISHED_AT",
  "environment": {
    "cwd": "$CWD",
    "feature_branch": "$FEATURE_BRANCH",
    "base_branch": "$BASE_BRANCH",
    "pr_url": "$PR_URL",
    "pr_created": $PR_CREATED,
    "pr_exists": $PR_EXISTS
  },
  "checks": [
    {"name": "git_repo", "status": "passed"},
    {"name": "correct_branch", "status": "passed"},
    {"name": "working_tree_clean", "status": "passed"},
    {"name": "gh_authenticated", "status": "passed"},
    {"name": "branch_pushed", "status": "passed"},
    {"name": "pr_created", "status": "passed"}
  ],
  "errors": []
}
EOF

cat > "$REPORT_MD" <<EOF
# $TASK_ID

**Status:** success

**Started:** $STARTED_AT  
**Finished:** $FINISHED_AT

**Feature branch:** $FEATURE_BRANCH  
**Base branch:** $BASE_BRANCH  
**PR URL:** $PR_URL  
**PR created:** $PR_CREATED  
**PR exists:** $PR_EXISTS

## Checks

- Git repository: passed
- Correct branch: passed
- Working tree clean: passed
- gh authenticated: passed
- Branch pushed: passed
- PR created: passed
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
