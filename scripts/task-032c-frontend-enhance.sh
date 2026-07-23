#!/usr/bin/env bash
set -u

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

TASK_ID="task-032c-frontend-enhance"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PROJECT_ROOT="$(pwd)"
REPORT_DIR="${PROJECT_ROOT}/reports/${TASK_ID}"
mkdir -p "${REPORT_DIR}"

LOG_TXT="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"
REPORT_MD="${REPORT_DIR}/report.md"

: > "${LOG_TXT}"

STATUS="success"
STAGE="done"

NODE_VERSION=""
NPM_VERSION=""
DIST_EXISTS=false
DIST_FILES_COUNT=0

BACKEND_RESTARTED="not_attempted"
API_HEALTH="unreachable"
API_SIGNALS="unreachable"
API_STATS="unreachable"

log() {
  echo "$1" | tee -a "${LOG_TXT}"
}

log "Task: ${TASK_ID}"
log "Started: ${STARTED_AT}"
log "Project root: ${PROJECT_ROOT}"

write_report() {
  FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  NEXT_ACTION="Check log.txt"
  if [[ "${STATUS}" == "success" ]]; then
    NEXT_ACTION="Run: cd frontend and npm run dev. Open http://localhost:5173"
  elif [[ "${STATUS}" == "needs_human" ]]; then
    NEXT_ACTION="Frontend build is OK, but backend/API is not ready. Check Docker and backend logs."
  fi

  cat > "${REPORT_JSON}" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "${STATUS}",
  "stage": "${STAGE}",
  "started_at": "${STARTED_AT}",
  "finished_at": "${FINISHED_AT}",
  "node_version": "${NODE_VERSION}",
  "npm_version": "${NPM_VERSION}",
  "dist_exists": ${DIST_EXISTS},
  "dist_files_count": ${DIST_FILES_COUNT},
  "backend_restarted": "${BACKEND_RESTARTED}",
  "api_checks": {
    "health": "${API_HEALTH}",
    "signals": "${API_SIGNALS}",
    "signals_stats": "${API_STATS}"
  },
  "next_action": "${NEXT_ACTION}"
}
JSON

  cat > "${REPORT_MD}" <<MD
# ${TASK_ID}

Status: ${STATUS}
Stage: ${STAGE}
Started: ${STARTED_AT}
Finished: ${FINISHED_AT}

Node: ${NODE_VERSION}
NPM: ${NPM_VERSION}

dist exists: ${DIST_EXISTS}
dist files: ${DIST_FILES_COUNT}

Backend restarted: ${BACKEND_RESTARTED}

API checks:

- health: ${API_HEALTH}
- signals: ${API_SIGNALS}
- signals stats: ${API_STATS}

Next action:

${NEXT_ACTION}
MD
}

check_url() {
  local url="$1"
  local marker="$2"
  local body=""

  if command -v curl >/dev/null 2>&1; then
    body=$(curl -s -m 8 "${url}" 2>>"${LOG_TXT}" || echo "")
  else
    body=$(node -e "fetch(process.argv[1], { signal: AbortSignal.timeout(8000) }).then(function(r){return r.text();}).then(function(t){console.log(t);}).catch(function(){console.log('');});" "${url}" 2>>"${LOG_TXT}" || echo "")
  fi

  if echo "${body}" | grep -q "${marker}"; then
    echo "ok"
  else
    echo "unreachable"
  fi
}

if ! command -v node >/dev/null 2>&1; then
  log "ERROR: Node.js not found"
  STATUS="failed"
  STAGE="node_not_found"
  write_report
  exit 0
fi

if ! command -v npm >/dev/null 2>&1; then
  log "ERROR: npm not found"
  STATUS="failed"
  STAGE="npm_not_found"
  write_report
  exit 0
fi

NODE_VERSION="$(node --version)"
NPM_VERSION="$(npm --version)"

log "Node: ${NODE_VERSION}"
log "NPM: ${NPM_VERSION}"

log "Updating backend/app/api/market_data.py"

cat > backend/app/api/market_data.py <<'PY_MARKET'
import datetime
import decimal
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query


def serialize_value(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    return value


def result_to_records(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    columns = result.get("columns", [])
    data = result.get("data", [])
    records = []

    for row in data:
        record = {}
        for column, value in zip(columns, row):
            record[column] = serialize_value(value)
        records.append(record)

    return records


def get_db():
    try:
        from app.db.db_manager import DBManager

        return DBManager()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable: {exc}",
        )


def register_routes(app: FastAPI) -> None:
    @app.get("/api/instruments")
    def get_instruments(
        limit: int = Query(100, ge=1, le=1000),
        ticker: Optional[str] = Query(None),
        figi: Optional[str] = Query(None),
        exchange: Optional[str] = Query(None),
        instrument_type: Optional[str] = Query(None),
    ):
        clauses = []
        params: Dict[str, Any] = {}

        if ticker:
            clauses.append("ticker = %(ticker)s")
            params["ticker"] = ticker

        if figi:
            clauses.append("figi = %(figi)s")
            params["figi"] = figi

        if exchange:
            clauses.append("exchange = %(exchange)s")
            params["exchange"] = exchange

        if instrument_type:
            clauses.append("instrument_type = %(instrument_type)s")
            params["instrument_type"] = instrument_type

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        query = f"""
            SELECT
                figi,
                ticker,
                name,
                instrument_type,
                class_code,
                currency,
                lot_size,
                min_price_increment,
                is_tradable,
                exchange,
                country_of_risk,
                created_at,
                updated_at
            FROM trading.instruments
            {where}
            ORDER BY ticker
            LIMIT %(limit)s
        """

        params["limit"] = limit

        try:
            db = get_db()
            result = db.select(query, params)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        items = result_to_records(result)

        return {
            "items": items,
            "count": len(items),
        }

    @app.get("/api/candles")
    def get_candles(
        limit: int = Query(100, ge=1, le=5000),
        ticker: Optional[str] = Query(None),
        figi: Optional[str] = Query(None),
        timeframe: Optional[str] = Query(None),
    ):
        clauses = []
        params: Dict[str, Any] = {}

        if ticker:
            clauses.append("ticker = %(ticker)s")
            params["ticker"] = ticker

        if figi:
            clauses.append("figi = %(figi)s")
            params["figi"] = figi

        if timeframe:
            clauses.append("timeframe = %(timeframe)s")
            params["timeframe"] = timeframe

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        query = f"""
            SELECT
                ticker,
                figi,
                timestamp,
                timeframe,
                open,
                high,
                low,
                close,
                volume,
                created_at
            FROM trading.candles_aggregated
            {where}
            ORDER BY timestamp DESC
            LIMIT %(limit)s
        """

        params["limit"] = limit

        try:
            db = get_db()
            result = db.select(query, params)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        items = result_to_records(result)

        return {
            "items": items,
            "count": len(items),
        }

    @app.get("/api/signals/stats")
    def get_signal_stats(
        ticker: Optional[str] = Query(None),
        timeframe: Optional[str] = Query(None),
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
    ):
        clauses: List[str] = []
        params: Dict[str, Any] = {}

        if ticker:
            clauses.append("ticker = %(ticker)s")
            params["ticker"] = ticker

        if timeframe:
            clauses.append("timeframe = %(timeframe)s")
            params["timeframe"] = timeframe

        if date_from:
            clauses.append("timestamp >= %(date_from)s::timestamp")
            params["date_from"] = date_from

        if date_to:
            clauses.append("timestamp <= %(date_to)s::timestamp")
            params["date_to"] = date_to

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        try:
            db = get_db()

            total_records = result_to_records(
                db.select(
                    f"SELECT count(*) AS cnt FROM trading.signals {where}",
                    params,
                )
            )
            total = int(total_records[0]["cnt"]) if total_records else 0

            latest_records = result_to_records(
                db.select(
                    f"SELECT max(timestamp) AS latest_timestamp FROM trading.signals {where}",
                    params,
                )
            )
            latest_timestamp = latest_records[0]["latest_timestamp"] if latest_records else None

            by_direction = result_to_records(
                db.select(
                    f"""
                    SELECT signal, count(*) AS cnt
                    FROM trading.signals
                    {where}
                    GROUP BY signal
                    ORDER BY cnt DESC
                    """,
                    params,
                )
            )

            by_timeframe = result_to_records(
                db.select(
                    f"""
                    SELECT timeframe, count(*) AS cnt
                    FROM trading.signals
                    {where}
                    GROUP BY timeframe
                    ORDER BY cnt DESC
                    """,
                    params,
                )
            )

            pattern_where_clauses = clauses + [
                "pattern_name IS NOT NULL",
                "pattern_name <> ''",
            ]
            pattern_where = "WHERE " + " AND ".join(pattern_where_clauses)

            by_pattern_combined = result_to_records(
                db.select(
                    f"""
                    SELECT pattern_name, count(*) AS cnt
                    FROM trading.signals
                    {pattern_where}
                    GROUP BY pattern_name
                    ORDER BY cnt DESC
                    LIMIT 50
                    """,
                    params,
                )
            )

            atomic_query = f"""
                WITH split_patterns AS (
                    SELECT trim(unnest(string_to_array(pattern_name, ','))) AS pattern_name
                    FROM trading.signals
                    {pattern_where}
                )
                SELECT pattern_name, count(*) AS cnt
                FROM split_patterns
                WHERE pattern_name IS NOT NULL
                  AND pattern_name <> ''
                GROUP BY pattern_name
                ORDER BY cnt DESC
                LIMIT 50
            """

            by_pattern = result_to_records(db.select(atomic_query, params))

            return {
                "total": total,
                "latest_timestamp": latest_timestamp,
                "by_direction": by_direction,
                "by_timeframe": by_timeframe,
                "by_pattern": by_pattern,
                "by_pattern_combined": by_pattern_combined,
            }

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/signals")
    def get_signals(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        ticker: Optional[str] = Query(None),
        timeframe: Optional[str] = Query(None),
        signal: Optional[str] = Query(None),
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
        sort_by: str = Query("timestamp"),
        sort_dir: str = Query("desc"),
    ):
        allowed_sort_columns = {
            "id",
            "ticker",
            "figi",
            "timeframe",
            "timestamp",
            "signal",
            "confidence",
            "price",
            "rsi",
            "macd",
            "bb_position",
            "volume_ratio",
            "atr_pct",
            "pattern_name",
            "created_at",
        }

        if sort_by not in allowed_sort_columns:
            sort_by = "timestamp"

        sort_dir = sort_dir.lower()
        if sort_dir not in {"asc", "desc"}:
            sort_dir = "desc"

        clauses = []
        params: Dict[str, Any] = {}

        if ticker:
            clauses.append("ticker = %(ticker)s")
            params["ticker"] = ticker

        if timeframe:
            clauses.append("timeframe = %(timeframe)s")
            params["timeframe"] = timeframe

        if signal:
            clauses.append("signal = %(signal)s")
            params["signal"] = signal

        if date_from:
            clauses.append("timestamp >= %(date_from)s::timestamp")
            params["date_from"] = date_from

        if date_to:
            clauses.append("timestamp <= %(date_to)s::timestamp")
            params["date_to"] = date_to

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        try:
            db = get_db()

            total_records = result_to_records(
                db.select(
                    f"SELECT count(*) AS cnt FROM trading.signals {where}",
                    params,
                )
            )
            total = int(total_records[0]["cnt"]) if total_records else 0

            query = f"""
                SELECT
                    id,
                    ticker,
                    figi,
                    timeframe,
                    timestamp,
                    signal,
                    confidence,
                    price,
                    rsi,
                    macd,
                    bb_position,
                    volume_ratio,
                    atr_pct,
                    summary,
                    buy_signals,
                    sell_signals,
                    total_signals,
                    pattern_name,
                    created_at
                FROM trading.signals
                {where}
                ORDER BY {sort_by} {sort_dir} NULLS LAST
                LIMIT %(limit)s OFFSET %(offset)s
            """

            params["limit"] = limit
            params["offset"] = offset

            result = db.select(query, params)

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        items = result_to_records(result)

        return {
            "items": items,
            "count": len(items),
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/top-stocks-by-volume")
    def get_top_stocks_by_volume(
        limit: int = Query(100, ge=1, le=1000),
        report_date: Optional[str] = Query(None),
        ticker: Optional[str] = Query(None),
    ):
        clauses = []
        params: Dict[str, Any] = {}

        if report_date:
            clauses.append("report_date = %(report_date)s::date")
            params["report_date"] = report_date

        if ticker:
            clauses.append("ticker = %(ticker)s")
            params["ticker"] = ticker

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        query = f"""
            SELECT
                rank,
                report_date,
                ticker,
                figi,
                name,
                sum_volume,
                candle_count,
                first_date,
                last_date,
                period_start,
                period_end,
                created_at
            FROM trading.top_stocks_by_volume
            {where}
            ORDER BY report_date DESC, rank ASC
            LIMIT %(limit)s
        """

        params["limit"] = limit

        try:
            db = get_db()
            result = db.select(query, params)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        items = result_to_records(result)

        return {
            "items": items,
            "count": len(items),
        }
PY_MARKET

log "Updating frontend package.json"
mkdir -p frontend/src/components

cat > frontend/package.json <<'PKG_JSON'
{
  "name": "trading-terminal-frontend",
  "private": true,
  "version": "0.2.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "lightweight-charts": "^4.2.3",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "autoprefixer": "^10.4.19",
    "postcss": "^8.4.39",
    "tailwindcss": "^3.4.6",
    "typescript": "^5.5.3",
    "vite": "^5.3.4"
  }
}
PKG_JSON

cat > frontend/tsconfig.json <<'TSCONFIG_JSON'
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"]
}
TSCONFIG_JSON

cat > frontend/vite.config.js <<'VITE_JS'
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
VITE_JS

cat > frontend/tailwind.config.js <<'TAILWIND_JS'
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {},
  },
  plugins: [],
};
TAILWIND_JS

cat > frontend/postcss.config.js <<'POSTCSS_JS'
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
POSTCSS_JS

cat > frontend/index.html <<'INDEX_HTML'
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Trading Terminal</title>
  </head>
  <body class="bg-slate-950 text-slate-100">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
INDEX_HTML

cat > frontend/src/index.css <<'INDEX_CSS'
@tailwind base;
@tailwind components;
@tailwind utilities;
INDEX_CSS

cat > frontend/src/main.tsx <<'MAIN_TSX'
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
MAIN_TSX

cat > frontend/src/types.ts <<'TYPES_TS'
export interface ListResponse<T> {
  items: T[];
  count: number;
}

export interface PaginatedResponse<T> {
  items: T[];
  count: number;
  total: number;
  limit: number;
  offset: number;
}

export interface Signal {
  id: number;
  ticker: string;
  figi: string | null;
  timeframe: string;
  timestamp: string;
  signal: string;
  confidence: number | null;
  price: number | null;
  rsi: number | null;
  macd: number | null;
  bb_position: number | null;
  volume_ratio: number | null;
  atr_pct: number | null;
  summary: string | null;
  buy_signals: number | null;
  sell_signals: number | null;
  total_signals: number | null;
  pattern_name: string | null;
  created_at: string | null;
}

export interface Candle {
  ticker: string;
  figi: string | null;
  timestamp: string;
  timeframe: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  created_at: string | null;
}

export interface TopStock {
  rank: number;
  report_date: string;
  ticker: string;
  figi: string;
  name: string | null;
  sum_volume: number;
  candle_count: number;
  first_date: string | null;
  last_date: string | null;
  period_start: string;
  period_end: string;
  created_at: string | null;
}

export interface Instrument {
  figi: string;
  ticker: string;
  name: string | null;
  instrument_type: string | null;
  class_code: string | null;
  currency: string | null;
  lot_size: number | null;
  min_price_increment: number | null;
  is_tradable: boolean | null;
  exchange: string | null;
  country_of_risk: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface DirectionCount {
  signal: string;
  cnt: number;
}

export interface TimeframeCount {
  timeframe: string;
  cnt: number;
}

export interface PatternCount {
  pattern_name: string;
  cnt: number;
}

export interface SignalStats {
  total: number;
  latest_timestamp: string | null;
  by_direction: DirectionCount[];
  by_timeframe: TimeframeCount[];
  by_pattern: PatternCount[];
  by_pattern_combined: PatternCount[];
}
TYPES_TS

cat > frontend/src/api.ts <<'API_TS'
import type {
  Candle,
  Instrument,
  ListResponse,
  PaginatedResponse,
  Signal,
  SignalStats,
  TopStock,
} from "./types";

function toQuery(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();

  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }

  const query = search.toString();
  return query ? `?${query}` : "";
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }

  return (await response.json()) as T;
}

export function getSignals(params: {
  limit?: number;
  offset?: number;
  ticker?: string;
  timeframe?: string;
  signal?: string;
  date_from?: string;
  date_to?: string;
  sort_by?: string;
  sort_dir?: string;
}) {
  return getJson<PaginatedResponse<Signal>>(`/api/signals${toQuery(params)}`);
}

export function getSignalStats(params: {
  ticker?: string;
  timeframe?: string;
  date_from?: string;
  date_to?: string;
} = {}) {
  return getJson<SignalStats>(`/api/signals/stats${toQuery(params)}`);
}

export function getCandles(params: {
  ticker?: string;
  figi?: string;
  timeframe?: string;
  limit?: number;
}) {
  return getJson<ListResponse<Candle>>(`/api/candles${toQuery(params)}`);
}

export function getTopStocks(limit = 30) {
  return getJson<ListResponse<TopStock>>(
    `/api/top-stocks-by-volume${toQuery({ limit })}`
  );
}

export function getInstruments(limit = 100) {
  return getJson<ListResponse<Instrument>>(
    `/api/instruments${toQuery({ limit })}`
  );
}
API_TS

cat > frontend/src/App.tsx <<'APP_TSX'
import { useState } from "react";
import SignalsPanel from "./components/SignalsPanel";
import PatternStatsPanel from "./components/PatternStatsPanel";
import TopStocksPanel from "./components/TopStocksPanel";
import InstrumentsPanel from "./components/InstrumentsPanel";

type Tab = "signals" | "stats" | "top" | "instruments";

const tabs: Array<{ id: Tab; label: string }> = [
  { id: "signals", label: "Сигналы" },
  { id: "stats", label: "Статистика" },
  { id: "top", label: "ТОП-30" },
  { id: "instruments", label: "Инструменты" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("signals");

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-800 bg-slate-900/70 px-6 py-4">
        <h1 className="text-xl font-semibold">Trading Terminal</h1>
        <p className="text-sm text-slate-400">
          MOEX analytics and trading signals
        </p>
      </header>

      <nav className="flex gap-2 border-b border-slate-800 px-6 py-3">
        {tabs.map((item) => (
          <button
            key={item.id}
            onClick={() => setTab(item.id)}
            className={
              "rounded px-3 py-1.5 text-sm font-medium transition " +
              (tab === item.id
                ? "bg-sky-500/20 text-sky-300"
                : "text-slate-400 hover:bg-slate-800 hover:text-slate-200")
            }
          >
            {item.label}
          </button>
        ))}
      </nav>

      <main className="p-6">
        {tab === "signals" && <SignalsPanel />}
        {tab === "stats" && <PatternStatsPanel />}
        {tab === "top" && <TopStocksPanel />}
        {tab === "instruments" && <InstrumentsPanel />}
      </main>
    </div>
  );
}
APP_TSX

cat > frontend/src/components/CandleChart.tsx <<'CANDLE_CHART_TSX'
import { useEffect, useRef } from "react";
import { createChart, ColorType } from "lightweight-charts";
import type { UTCTimestamp } from "lightweight-charts";
import type { Candle } from "../types";

function toUnixTimestamp(value: string): number {
  const normalized = value.includes("T") ? value : value.replace(" ", "T");
  const withZone =
    normalized.endsWith("Z") || normalized.includes("+")
      ? normalized
      : normalized + "Z";

  return Math.floor(new Date(withZone).getTime() / 1000);
}

export default function CandleChart({
  candles,
  height = 320,
}: {
  candles: Candle[];
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    const chart = createChart(container, {
      width: container.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: "#020617" },
        textColor: "#cbd5e1",
      },
      grid: {
        vertLines: { color: "#1e293b" },
        horzLines: { color: "#1e293b" },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const series = chart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#f43f5e",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#f43f5e",
    });

    const data = candles
      .filter(
        (candle) =>
          candle.open !== null &&
          candle.high !== null &&
          candle.low !== null &&
          candle.close !== null
      )
      .map((candle) => ({
        time: toUnixTimestamp(candle.timestamp) as UTCTimestamp,
        open: Number(candle.open),
        high: Number(candle.high),
        low: Number(candle.low),
        close: Number(candle.close),
      }))
      .sort((a, b) => Number(a.time) - Number(b.time));

    series.setData(data);
    chart.timeScale().fitContent();

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth });
    };

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [candles, height]);

  if (candles.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-slate-500">
        Нет свечей для отображения
      </div>
    );
  }

  return <div ref={containerRef} style={{ height: `${height}px`, width: "100%" }} />;
}
CANDLE_CHART_TSX

cat > frontend/src/components/SignalDetailModal.tsx <<'SIGNAL_DETAIL_TSX'
import { useEffect, useState } from "react";
import type { Candle, Signal } from "../types";
import { getCandles } from "../api";
import CandleChart from "./CandleChart";

function Field({
  label,
  value,
}: {
  label: string;
  value: string | number | null | undefined;
}) {
  const text =
    value === null || value === undefined || value === "" ? "—" : String(value);

  return (
    <div className="rounded border border-slate-800 bg-slate-900/60 px-3 py-2">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-sm">{text}</div>
    </div>
  );
}

export default function SignalDetailModal({
  signal,
  onClose,
}: {
  signal: Signal;
  onClose: () => void;
}) {
  const [candles, setCandles] = useState<Candle[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);

      try {
        const response = await getCandles({
          ticker: signal.ticker,
          timeframe: signal.timeframe,
          limit: 300,
        });

        if (!cancelled) {
          setCandles(response.items);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, [signal]);

  const fields = [
    { label: "Ticker", value: signal.ticker },
    { label: "FIGI", value: signal.figi },
    { label: "Timeframe", value: signal.timeframe },
    { label: "Timestamp", value: signal.timestamp.replace("T", " ").slice(0, 19) },
    { label: "Signal", value: signal.signal },
    { label: "Confidence", value: signal.confidence },
    { label: "Price", value: signal.price },
    { label: "RSI", value: signal.rsi },
    { label: "MACD", value: signal.macd },
    { label: "BB Position", value: signal.bb_position },
    { label: "Volume Ratio", value: signal.volume_ratio },
    { label: "ATR %", value: signal.atr_pct },
    { label: "Buy Signals", value: signal.buy_signals },
    { label: "Sell Signals", value: signal.sell_signals },
    { label: "Total Signals", value: signal.total_signals },
    { label: "Pattern", value: signal.pattern_name },
  ];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-5xl overflow-y-auto rounded border border-slate-700 bg-slate-950 p-4"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h3 className="text-lg font-semibold">
              {signal.ticker} · {signal.timeframe} · {signal.signal}
            </h3>
            <p className="text-sm text-slate-400">
              {signal.timestamp.replace("T", " ").slice(0, 19)}
            </p>
          </div>

          <button
            onClick={onClose}
            className="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300 transition hover:bg-slate-800"
          >
            Закрыть
          </button>
        </div>

        <div className="mb-4 grid grid-cols-2 gap-2 md:grid-cols-4">
          {fields.map((field) => (
            <Field key={field.label} label={field.label} value={field.value} />
          ))}
        </div>

        <div className="mb-2 rounded border border-slate-800 bg-slate-900/60 px-3 py-2">
          <div className="text-xs text-slate-500">Summary</div>
          <div className="text-sm">{signal.summary ?? "—"}</div>
        </div>

        <div>
          <h4 className="mb-2 text-sm font-semibold text-slate-300">
            Свечи ({signal.ticker}, {signal.timeframe})
          </h4>

          {error && (
            <div className="mb-2 rounded border border-rose-700 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
              Ошибка загрузки свечей: {error}
            </div>
          )}

          {loading ? (
            <div className="flex h-64 items-center justify-center text-sm text-slate-500">
              Загрузка свечей...
            </div>
          ) : (
            <CandleChart candles={candles} height={360} />
          )}
        </div>
      </div>
    </div>
  );
}
SIGNAL_DETAIL_TSX

cat > frontend/src/components/SignalsPanel.tsx <<'SIGNALS_PANEL_TSX'
import { useEffect, useState } from "react";
import { getSignals } from "../api";
import type { Signal } from "../types";
import SignalDetailModal from "./SignalDetailModal";

type SortKey =
  | "timestamp"
  | "ticker"
  | "figi"
  | "timeframe"
  | "signal"
  | "confidence"
  | "price"
  | "rsi"
  | "macd"
  | "bb_position"
  | "volume_ratio"
  | "atr_pct"
  | "pattern_name";

type SortDir = "asc" | "desc";

interface Filters {
  ticker: string;
  timeframe: string;
  signal: string;
  dateFrom: string;
  dateTo: string;
}

const columns: Array<{ key: SortKey; label: string; numeric?: boolean }> = [
  { key: "timestamp", label: "Timestamp" },
  { key: "ticker", label: "Ticker" },
  { key: "figi", label: "FIGI" },
  { key: "timeframe", label: "TF" },
  { key: "signal", label: "Signal" },
  { key: "confidence", label: "Conf", numeric: true },
  { key: "price", label: "Price", numeric: true },
  { key: "rsi", label: "RSI", numeric: true },
  { key: "macd", label: "MACD", numeric: true },
  { key: "bb_position", label: "BB%", numeric: true },
  { key: "volume_ratio", label: "VolRatio", numeric: true },
  { key: "atr_pct", label: "ATR%", numeric: true },
  { key: "pattern_name", label: "Pattern" },
];

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }

  return Number(value).toFixed(digits);
}

function SignalBadge({ value }: { value: string }) {
  let className = "rounded px-2 py-0.5 text-xs font-semibold ";

  if (value === "BUY") {
    className += "bg-emerald-500/20 text-emerald-300";
  } else if (value === "SELL") {
    className += "bg-rose-500/20 text-rose-300";
  } else {
    className += "bg-slate-500/20 text-slate-300";
  }

  return <span className={className}>{value}</span>;
}

export default function SignalsPanel() {
  const [items, setItems] = useState<Signal[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Signal | null>(null);

  const [ticker, setTicker] = useState("");
  const [timeframe, setTimeframe] = useState("");
  const [signal, setSignal] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const [filters, setFilters] = useState<Filters>({
    ticker: "",
    timeframe: "",
    signal: "",
    dateFrom: "",
    dateTo: "",
  });

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sortBy, setSortBy] = useState<SortKey>("timestamp");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  async function load() {
    setLoading(true);
    setError(null);

    try {
      const response = await getSignals({
        limit: pageSize,
        offset: (page - 1) * pageSize,
        ticker: filters.ticker || undefined,
        timeframe: filters.timeframe || undefined,
        signal: filters.signal || undefined,
        date_from: filters.dateFrom || undefined,
        date_to: filters.dateTo ? `${filters.dateTo} 23:59:59` : undefined,
        sort_by: sortBy,
        sort_dir: sortDir,
      });

      setItems(response.items);
      setTotal(response.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [filters, page, pageSize, sortBy, sortDir, reloadToken]);

  useEffect(() => {
    if (!autoRefresh) {
      return;
    }

    const id = window.setInterval(() => {
      setReloadToken((token) => token + 1);
    }, 30000);

    return () => window.clearInterval(id);
  }, [autoRefresh]);

  function applyFilters() {
    setFilters({
      ticker,
      timeframe,
      signal,
      dateFrom,
      dateTo,
    });
    setPage(1);
  }

  function resetFilters() {
    setTicker("");
    setTimeframe("");
    setSignal("");
    setDateFrom("");
    setDateTo("");
    setFilters({
      ticker: "",
      timeframe: "",
      signal: "",
      dateFrom: "",
      dateTo: "",
    });
    setPage(1);
  }

  function toggleSort(key: SortKey) {
    if (sortBy === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortBy(key);
      setSortDir(key === "timestamp" ? "desc" : "asc");
    }

    setPage(1);
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="space-y-1 text-sm">
          <span className="block text-slate-400">Ticker</span>
          <input
            value={ticker}
            onChange={(event) => setTicker(event.target.value.toUpperCase())}
            placeholder="VTBR"
            className="w-36 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm outline-none focus:border-sky-500"
          />
        </label>

        <label className="space-y-1 text-sm">
          <span className="block text-slate-400">Timeframe</span>
          <select
            value={timeframe}
            onChange={(event) => setTimeframe(event.target.value)}
            className="w-32 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm outline-none focus:border-sky-500"
          >
            <option value="">Все</option>
            <option value="30min">30min</option>
            <option value="1h">1h</option>
            <option value="4h">4h</option>
            <option value="1d">1d</option>
          </select>
        </label>

        <label className="space-y-1 text-sm">
          <span className="block text-slate-400">Signal</span>
          <select
            value={signal}
            onChange={(event) => setSignal(event.target.value)}
            className="w-28 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm outline-none focus:border-sky-500"
          >
            <option value="">Все</option>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </select>
        </label>

        <label className="space-y-1 text-sm">
          <span className="block text-slate-400">Date from</span>
          <input
            type="date"
            value={dateFrom}
            onChange={(event) => setDateFrom(event.target.value)}
            className="w-40 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm outline-none focus:border-sky-500"
          />
        </label>

        <label className="space-y-1 text-sm">
          <span className="block text-slate-400">Date to</span>
          <input
            type="date"
            value={dateTo}
            onChange={(event) => setDateTo(event.target.value)}
            className="w-40 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm outline-none focus:border-sky-500"
          />
        </label>

        <button
          onClick={applyFilters}
          className="rounded bg-sky-500/20 px-3 py-1.5 text-sm font-medium text-sky-300 transition hover:bg-sky-500/30"
        >
          Применить
        </button>

        <button
          onClick={resetFilters}
          className="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300 transition hover:bg-slate-800"
        >
          Сброс
        </button>

        <button
          onClick={() => void load()}
          disabled={loading}
          className="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300 transition hover:bg-slate-800 disabled:opacity-50"
        >
          {loading ? "Загрузка..." : "Обновить"}
        </button>

        <label className="flex items-center gap-2 text-sm text-slate-400">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(event) => setAutoRefresh(event.target.checked)}
          />
          Автообновление 30 сек
        </label>
      </div>

      {error && (
        <div className="rounded border border-rose-700 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
          Ошибка: {error}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 text-sm text-slate-400">
        <div>Всего сигналов: {total}</div>
        <div>Показано: {items.length}</div>

        <div className="ml-auto flex items-center gap-2">
          <button
            disabled={page <= 1 || loading}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
            className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40"
          >
            Назад
          </button>

          <span>
            Страница {page} из {totalPages}
          </span>

          <button
            disabled={page >= totalPages || loading}
            onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
            className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40"
          >
            Вперёд
          </button>

          <select
            value={pageSize}
            onChange={(event) => {
              setPageSize(Number(event.target.value));
              setPage(1);
            }}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm outline-none focus:border-sky-500"
          >
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      </div>

      <div className="overflow-x-auto rounded border border-slate-800">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  onClick={() => toggleSort(column.key)}
                  className={
                    "cursor-pointer select-none px-2 py-2 " +
                    (column.numeric ? "text-right" : "text-left")
                  }
                >
                  {column.label}
                  {sortBy === column.key ? (sortDir === "asc" ? " ↑" : " ↓") : ""}
                </th>
              ))}
              <th className="px-2 py-2 text-left">Summary</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr
                key={item.id}
                onClick={() => setSelected(item)}
                className="cursor-pointer border-t border-slate-800 hover:bg-slate-900/60"
              >
                <td className="whitespace-nowrap px-2 py-1">
                  {item.timestamp.replace("T", " ").slice(0, 19)}
                </td>
                <td className="px-2 py-1 font-medium">{item.ticker}</td>
                <td className="px-2 py-1 text-slate-400">{item.figi ?? "—"}</td>
                <td className="px-2 py-1">{item.timeframe}</td>
                <td className="px-2 py-1">
                  <SignalBadge value={item.signal} />
                </td>
                <td className="px-2 py-1 text-right">
                  {formatNumber(item.confidence, 2)}
                </td>
                <td className="px-2 py-1 text-right">
                  {formatNumber(item.price, 4)}
                </td>
                <td className="px-2 py-1 text-right">
                  {formatNumber(item.rsi, 2)}
                </td>
                <td className="px-2 py-1 text-right">
                  {formatNumber(item.macd, 4)}
                </td>
                <td className="px-2 py-1 text-right">
                  {formatNumber(item.bb_position, 2)}
                </td>
                <td className="px-2 py-1 text-right">
                  {formatNumber(item.volume_ratio, 2)}
                </td>
                <td className="px-2 py-1 text-right">
                  {formatNumber(item.atr_pct, 2)}
                </td>
                <td className="max-w-xs px-2 py-1 text-slate-300">
                  {item.pattern_name ?? "—"}
                </td>
                <td className="max-w-xl px-2 py-1 text-slate-300">
                  {item.summary ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <SignalDetailModal signal={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
SIGNALS_PANEL_TSX

cat > frontend/src/components/PatternStatsPanel.tsx <<'PATTERN_STATS_TSX'
import { useEffect, useState } from "react";
import { getSignalStats } from "../api";
import type { SignalStats } from "../types";

export default function PatternStatsPanel() {
  const [stats, setStats] = useState<SignalStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  async function load() {
    setLoading(true);
    setError(null);

    try {
      const response = await getSignalStats();
      setStats(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [reloadToken]);

  useEffect(() => {
    if (!autoRefresh) {
      return;
    }

    const id = window.setInterval(() => {
      setReloadToken((token) => token + 1);
    }, 30000);

    return () => window.clearInterval(id);
  }, [autoRefresh]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Статистика по сигналам</h2>

        <button
          onClick={() => void load()}
          disabled={loading}
          className="rounded bg-sky-500/20 px-3 py-1.5 text-sm font-medium text-sky-300 transition hover:bg-sky-500/30 disabled:opacity-50"
        >
          {loading ? "Загрузка..." : "Обновить"}
        </button>

        <label className="flex items-center gap-2 text-sm text-slate-400">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(event) => setAutoRefresh(event.target.checked)}
          />
          Автообновление 30 сек
        </label>
      </div>

      {error && (
        <div className="rounded border border-rose-700 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
          Ошибка: {error}
        </div>
      )}

      {stats && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <div className="rounded border border-slate-800 bg-slate-900/60 px-4 py-3">
              <div className="text-sm text-slate-400">Всего сигналов</div>
              <div className="text-2xl font-semibold">{stats.total}</div>
            </div>

            <div className="rounded border border-slate-800 bg-slate-900/60 px-4 py-3">
              <div className="text-sm text-slate-400">Последний сигнал</div>
              <div className="text-lg font-semibold">
                {stats.latest_timestamp
                  ? String(stats.latest_timestamp).replace("T", " ").slice(0, 19)
                  : "—"}
              </div>
            </div>

            <div className="rounded border border-slate-800 bg-slate-900/60 px-4 py-3">
              <div className="text-sm text-slate-400">Уникальных паттернов</div>
              <div className="text-2xl font-semibold">
                {stats.by_pattern.length}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <div className="rounded border border-slate-800">
              <div className="border-b border-slate-800 bg-slate-900 px-3 py-2 text-sm font-semibold">
                По направлению
              </div>
              <table className="min-w-full text-sm">
                <tbody>
                  {stats.by_direction.map((item) => (
                    <tr key={item.signal} className="border-t border-slate-800">
                      <td className="px-3 py-1">{item.signal}</td>
                      <td className="px-3 py-1 text-right">{item.cnt}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="rounded border border-slate-800">
              <div className="border-b border-slate-800 bg-slate-900 px-3 py-2 text-sm font-semibold">
                По таймфреймам
              </div>
              <table className="min-w-full text-sm">
                <tbody>
                  {stats.by_timeframe.map((item) => (
                    <tr key={item.timeframe} className="border-t border-slate-800">
                      <td className="px-3 py-1">{item.timeframe}</td>
                      <td className="px-3 py-1 text-right">{item.cnt}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="rounded border border-slate-800">
              <div className="border-b border-slate-800 bg-slate-900 px-3 py-2 text-sm font-semibold">
                Паттерны
              </div>
              <div className="max-h-96 overflow-y-auto">
                <table className="min-w-full text-sm">
                  <tbody>
                    {stats.by_pattern.map((item) => (
                      <tr key={item.pattern_name} className="border-t border-slate-800">
                        <td className="px-3 py-1">{item.pattern_name}</td>
                        <td className="px-3 py-1 text-right">{item.cnt}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="rounded border border-slate-800">
            <div className="border-b border-slate-800 bg-slate-900 px-3 py-2 text-sm font-semibold">
              Комбинации паттернов
            </div>
            <div className="max-h-96 overflow-y-auto">
              <table className="min-w-full text-sm">
                <tbody>
                  {stats.by_pattern_combined.map((item) => (
                    <tr key={item.pattern_name} className="border-t border-slate-800">
                      <td className="px-3 py-1">{item.pattern_name}</td>
                      <td className="px-3 py-1 text-right">{item.cnt}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
PATTERN_STATS_TSX

cat > frontend/src/components/TopStocksPanel.tsx <<'TOP_PANEL_TSX'
import { useEffect, useState } from "react";
import { getTopStocks } from "../api";
import type { TopStock } from "../types";

export default function TopStocksPanel() {
  const [items, setItems] = useState<TopStock[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);

    try {
      const response = await getTopStocks(30);
      setItems(response.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold">ТОП-30 по объёму</h2>
        <button
          onClick={() => void load()}
          disabled={loading}
          className="rounded bg-sky-500/20 px-3 py-1.5 text-sm font-medium text-sky-300 transition hover:bg-sky-500/30 disabled:opacity-50"
        >
          {loading ? "Загрузка..." : "Обновить"}
        </button>
      </div>

      {error && (
        <div className="rounded border border-rose-700 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
          Ошибка: {error}
        </div>
      )}

      <div className="overflow-x-auto rounded border border-slate-800">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              <th className="px-2 py-2 text-left">Rank</th>
              <th className="px-2 py-2 text-left">Report Date</th>
              <th className="px-2 py-2 text-left">Ticker</th>
              <th className="px-2 py-2 text-left">Name</th>
              <th className="px-2 py-2 text-left">FIGI</th>
              <th className="px-2 py-2 text-right">Volume</th>
              <th className="px-2 py-2 text-right">Candles</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr
                key={`${item.report_date}-${item.ticker}`}
                className="border-t border-slate-800 hover:bg-slate-900/60"
              >
                <td className="px-2 py-1">{item.rank}</td>
                <td className="px-2 py-1">{item.report_date}</td>
                <td className="px-2 py-1 font-medium">{item.ticker}</td>
                <td className="px-2 py-1">{item.name ?? "—"}</td>
                <td className="px-2 py-1 text-slate-400">{item.figi}</td>
                <td className="px-2 py-1 text-right">
                  {Number(item.sum_volume).toLocaleString("ru-RU")}
                </td>
                <td className="px-2 py-1 text-right">{item.candle_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
TOP_PANEL_TSX

cat > frontend/src/components/InstrumentsPanel.tsx <<'INSTRUMENTS_PANEL_TSX'
import { useEffect, useState } from "react";
import { getInstruments } from "../api";
import type { Instrument } from "../types";

export default function InstrumentsPanel() {
  const [items, setItems] = useState<Instrument[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);

    try {
      const response = await getInstruments(100);
      setItems(response.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold">Инструменты</h2>
        <button
          onClick={() => void load()}
          disabled={loading}
          className="rounded bg-sky-500/20 px-3 py-1.5 text-sm font-medium text-sky-300 transition hover:bg-sky-500/30 disabled:opacity-50"
        >
          {loading ? "Загрузка..." : "Обновить"}
        </button>
      </div>

      {error && (
        <div className="rounded border border-rose-700 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
          Ошибка: {error}
        </div>
      )}

      <div className="overflow-x-auto rounded border border-slate-800">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              <th className="px-2 py-2 text-left">Ticker</th>
              <th className="px-2 py-2 text-left">Name</th>
              <th className="px-2 py-2 text-left">FIGI</th>
              <th className="px-2 py-2 text-left">Type</th>
              <th className="px-2 py-2 text-left">Currency</th>
              <th className="px-2 py-2 text-right">Lot</th>
              <th className="px-2 py-2 text-left">Tradable</th>
              <th className="px-2 py-2 text-left">Exchange</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr
                key={item.figi}
                className="border-t border-slate-800 hover:bg-slate-900/60"
              >
                <td className="px-2 py-1 font-medium">{item.ticker}</td>
                <td className="px-2 py-1">{item.name ?? "—"}</td>
                <td className="px-2 py-1 text-slate-400">{item.figi}</td>
                <td className="px-2 py-1">{item.instrument_type ?? "—"}</td>
                <td className="px-2 py-1">{item.currency ?? "—"}</td>
                <td className="px-2 py-1 text-right">{item.lot_size ?? "—"}</td>
                <td className="px-2 py-1">{String(item.is_tradable ?? "—")}</td>
                <td className="px-2 py-1">{item.exchange ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
INSTRUMENTS_PANEL_TSX

log "Running npm install"
if ! (cd frontend && npm install --no-audit --no-fund --progress=false >> "${LOG_TXT}" 2>&1); then
  log "ERROR: npm install failed"
  STATUS="failed"
  STAGE="npm_install"
  write_report
  exit 0
fi

log "Running npm run build"
if ! (cd frontend && npm run build >> "${LOG_TXT}" 2>&1); then
  log "ERROR: npm run build failed"
  STATUS="failed"
  STAGE="npm_build"
  write_report
  exit 0
fi

if [[ -d frontend/dist ]]; then
  DIST_EXISTS=true
  DIST_FILES_COUNT=$(find frontend/dist -type f 2>/dev/null | wc -l | tr -d ' ')
  if [[ -z "${DIST_FILES_COUNT}" ]]; then
    DIST_FILES_COUNT=0
  fi
  log "frontend/dist exists, files: ${DIST_FILES_COUNT}"
else
  log "ERROR: frontend/dist not found after build"
  STATUS="failed"
  STAGE="dist_missing"
  write_report
  exit 0
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  log "Rebuilding and starting backend"
  BACKEND_RESTARTED="attempted"

  if docker compose up -d --build backend >> "${LOG_TXT}" 2>&1; then
    for i in $(seq 1 30); do
      API_HEALTH="$(check_url http://localhost:8000/health status)"
      if [[ "${API_HEALTH}" == "ok" ]]; then
        log "Backend health is OK"
        break
      fi
      sleep 2
    done

    API_SIGNALS="$(check_url 'http://localhost:8000/api/signals?limit=1' total)"
    API_STATS="$(check_url 'http://localhost:8000/api/signals/stats' by_pattern)"

    if [[ "${API_HEALTH}" == "ok" ]]; then
      BACKEND_RESTARTED="ok"
    else
      BACKEND_RESTARTED="failed"
    fi
  else
    BACKEND_RESTARTED="failed"
  fi
else
  log "Docker Compose not available"
  BACKEND_RESTARTED="docker_not_available"
fi

log "API health: ${API_HEALTH}"
log "API signals: ${API_SIGNALS}"
log "API stats: ${API_STATS}"

if [[ "${STATUS}" == "success" ]]; then
  if [[ "${BACKEND_RESTARTED}" != "ok" || "${API_HEALTH}" != "ok" || "${API_SIGNALS}" != "ok" || "${API_STATS}" != "ok" ]]; then
    STATUS="needs_human"
    STAGE="backend_api_not_ready"
  fi
fi

write_report
log "Report JSON: ${REPORT_JSON}"
log "Status: ${STATUS}"
