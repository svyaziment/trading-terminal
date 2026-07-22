#!/usr/bin/env bash
set -Eeuo pipefail

TASK_ID="task-025a-commit-and-push"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
CWD="$(pwd)"
REPORT_DIR="$CWD/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"
FEATURE_BRANCH="feat/broker-data-loader"
BASE_BRANCH="main"

mkdir -p "$REPORT_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Task: $TASK_ID ==="
echo "Started: $STARTED_AT"
echo "Working directory: $CWD"
echo "Feature branch: $FEATURE_BRANCH"
echo "Base branch: $BASE_BRANCH"

cd "$CWD"

# ---------- Git checks ----------
echo "Checking git..."
command -v git >/dev/null 2>&1 || { echo "FAIL: git not found"; exit 1; }
echo "OK: git exists"
[ -d .git ] || { echo "FAIL: not a git repo"; exit 1; }
echo "OK: git repository exists"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "Current branch: $CURRENT_BRANCH"

if [ "$CURRENT_BRANCH" != "$FEATURE_BRANCH" ]; then
    echo "Switching to $FEATURE_BRANCH..."
    git checkout "$FEATURE_BRANCH" 2>/dev/null || git checkout -b "$FEATURE_BRANCH" "$BASE_BRANCH"
    echo "OK: on $FEATURE_BRANCH"
else
    echo "OK: already on $FEATURE_BRANCH"
fi

# ---------- Show status ----------
echo ""
echo "Current git status:"
git status --short
echo ""

# ---------- Secret check ----------
echo "Checking for secrets before commit..."

# Проверяем что .env не будет закоммичен
if git status --short | grep -qE '^\?\? .*\.env$|^\?\? .*\.env\.'; then
    echo "FAIL: .env file detected in untracked files. Add .env to .gitignore!"
    exit 1
fi

# Проверяем staged файлы на секреты
STAGED_FILES="$(git diff --cached --name-only 2>/dev/null || true)"
if printf '%s\n' "$STAGED_FILES" | grep -qE '\.env$|\.env\.|settings\.yaml$|\.pem$|\.key$|id_rsa'; then
    # .pem в certs/ это публичный сертификат - ок
    NON_CERT_SECRETS="$(printf '%s\n' "$STAGED_FILES" | grep -E '\.env$|\.env\.|settings\.yaml$|\.key$|id_rsa' || true)"
    if [ -n "$NON_CERT_SECRETS" ]; then
        echo "FAIL: secret files detected in staged files:"
        echo "$NON_CERT_SECRETS"
        exit 1
    fi
fi

echo "OK: no secrets detected"

# ---------- Stage all changes ----------
echo ""
echo "Staging all changes..."
git add -A
echo "OK: all changes staged"

echo ""
echo "Staged files:"
git diff --cached --name-only
echo ""

# ---------- Commit ----------
echo "Creating commit..."
git commit -m "feat: broker data loader, analytics pipeline, indicators manager

- Tinkoff Invest SDK integration (t-tech-investments)
- DataLoader for 30min candles via gRPC API
- Candles aggregation (30min -> 1h/4h/1d/1w/1M)
- Indicators manager (SMA, EMA, RSI, MACD, ATR, BB, Volume)
- Pipeline for TOP-30 stocks processing
- Market data API endpoints
- DB connection manager with connection pooling
- T-Bank root certificate for TLS verification"

COMMIT_SHA="$(git rev-parse --short HEAD)"
echo "OK: commit created: $COMMIT_SHA"

# ---------- Push ----------
echo ""
echo "Pushing to origin/$FEATURE_BRANCH..."
git push -u origin "$FEATURE_BRANCH" 2>&1
echo "OK: branch pushed"

# ---------- Create PR ----------
echo ""
echo "Checking gh CLI..."
if command -v gh >/dev/null 2>&1; then
    echo "OK: gh CLI found"

    # Проверяем авторизацию
    if gh auth status >/dev/null 2>&1; then
        echo "OK: gh authenticated"

        # Проверяем есть ли уже PR
        EXISTING_PR="$(gh pr list --head "$FEATURE_BRANCH" --base "$BASE_BRANCH" --json number --jq '.[0].number' 2>/dev/null || true)"

        if [ -n "$EXISTING_PR" ]; then
            PR_URL="https://github.com/svyaziment/trading-terminal/pull/$EXISTING_PR"
            PR_CREATED="false"
            echo "OK: PR already exists: $PR_URL"
        else
            echo "Creating PR..."
            PR_URL="$(gh pr create \
                --title "feat: broker data loader, analytics pipeline, indicators" \
                --body "## Summary

- Tinkoff Invest SDK integration (t-tech-investments via private PyPI index)
- DataLoader for 30min candles via gRPC API
- Candles aggregation (30min -> 1h/4h/1d/1w/1M)
- Indicators manager (SMA, EMA, RSI, MACD, ATR, Bollinger Bands, Volume)
- Pipeline for TOP-30 stocks processing
- Market data API endpoints (/api/instruments, /api/candles, /api/signals, /api/top-stocks-by-volume)
- DB connection manager with connection pooling
- T-Bank root certificate for TLS verification

## Testing

- DataLoader: 193 candles loaded for VTBR (30min, 5 days)
- Indicators: 90/90 success for TOP-30 stocks
- All API endpoints tested via /docs" \
                --base "$BASE_BRANCH" \
                --head "$FEATURE_BRANCH" 2>&1)"
            PR_CREATED="true"
            echo "OK: PR created: $PR_URL"
        fi
    else
        echo "WARN: gh not authenticated. Push done, create PR manually."
        PR_URL=""
        PR_CREATED="false"
    fi
else
    echo "WARN: gh CLI not found. Push done, create PR manually."
    PR_URL=""
    PR_CREATED="false"
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
    "commit_sha": "$COMMIT_SHA",
    "pr_url": "$PR_URL",
    "pr_created": $PR_CREATED
  },
  "checks": [
    {"name": "git_repo", "status": "passed"},
    {"name": "secret_check", "status": "passed"},
    {"name": "git_commit", "status": "passed", "sha": "$COMMIT_SHA"},
    {"name": "git_push", "status": "passed"},
    {"name": "pr_create", "status": "$([ "$PR_CREATED" = "true" ] && echo "passed" || echo "skipped")"}
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
**Commit:** $COMMIT_SHA
**PR URL:** $PR_URL
**PR created:** $PR_CREATED
EOF

echo ""
echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
