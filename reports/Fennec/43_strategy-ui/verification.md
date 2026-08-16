# Verification Report: Issue #43 — UI выбора стратегии (Strategy Lab)

## Frontend Changes

### 1. Dropdown "Стратегия (плагин)"
- **Location**: `frontend/src/components/StrategyLab.tsx`, секция "Стратегия"
- **Behavior**: 
  - Список плагинов из `GET /api/strategies/plugins` (atr_reversal, levels_reversal)
  - Дефолт: `levels_reversal` (бит-в-бит паритет со старым движком)
  - Disabled при `isLocked` (paper trading)
  - Восстановление `strategy_name` из `config` при `loadStrategy`

### 2. TypeScript Types
- `frontend/src/types.ts`: `StrategyConfig.strategy_name?: string`
- `frontend/src/api.ts`: `getStrategyPlugins()` → `GET /api/strategies/plugins`

### 3. Build Verification
```bash
cd frontend && npx tsc --noEmit
```
**Result**: ✅ PASS (0 errors)

## Backend Changes (⚠️ SHOULD BE SEPARATE PR)

### Files Modified
- `backend/app/api/strategy_jobs.py` (+19/-1)

### Changes
1. **New Endpoint**: `GET /api/strategies/plugins`
   - Returns: `{"plugins": ["atr_reversal", "levels_reversal"]}`
   - Registered BEFORE `/api/strategies/{strategy_id}` routes

2. **_run_job Logic**:
   - Extracts `strategy_name` from `config`
   - If present: routes `full_sample` through `run_portfolio_backtest` (plugin engine)
   - If absent: uses legacy `run_strategy_backtest` (backward compatibility)

### Recommendation
Backend changes belong to a separate issue (e.g., "Backend: Strategy Plugin Integration in Strategy Lab") and should be in a separate PR.

## Acceptance Criteria (Issue #43)
- [x] Dropdown "Стратегия" in Strategy Lab UI
- [x] List of available plugins from backend
- [x] Default selection: `levels_reversal`
- [x] Disabled when strategy is locked (paper trading)
- [x] `strategy_name` included in config payload
- [x] `strategy_name` restored when loading saved strategy
- [x] TypeScript compilation: PASS

## Scope Violation
⚠️ **Backend code (`strategy_jobs.py`) included in Frontend-only issue #43**
- Should be moved to separate PR for Issue #40 or new backend issue
- Current PR should contain ONLY frontend changes

## Next Steps
1. Split backend changes into separate PR
2. Assign Fennec to this PR
3. Add "Part of #39" (Strategy Plugin System epic)
4. After fixes: ready for merge
