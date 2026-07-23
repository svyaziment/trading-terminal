#!/usr/bin/env bash
set -u

TASK_ID="task-032-frontend-init"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
REPORT_DIR="reports/${TASK_ID}"
mkdir -p "${REPORT_DIR}"

LOG_TXT="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"
REPORT_MD="${REPORT_DIR}/report.md"

: > "${LOG_TXT}"

STATUS="success"
STAGE="done"

log() {
  echo "$1" | tee -a "${LOG_TXT}"
}

log "Task: ${TASK_ID}"
log "Started: ${STARTED_AT}"

create_report() {
  FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  FRONTEND_FILES_COUNT=$(find frontend -type f -not -path '*/node_modules/*' -not -path '*/dist/*' 2>/dev/null | wc -l | tr -d ' ')
  if [[ -z "${FRONTEND_FILES_COUNT}" ]]; then
    FRONTEND_FILES_COUNT=0
  fi

  cat > "${REPORT_JSON}" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "${STATUS}",
  "stage": "${STAGE}",
  "started_at": "${STARTED_AT}",
  "finished_at": "${FINISHED_AT}",
  "frontend_files_count": ${FRONTEND_FILES_COUNT},
  "next_action": "If success: cd frontend && npm run dev. Backend should be running on http://localhost:8000."
}
JSON

  cat > "${REPORT_MD}" <<MD
# ${TASK_ID}

Status: ${STATUS}
Stage: ${STAGE}
Started: ${STARTED_AT}
Finished: ${FINISHED_AT}
Frontend files created: ${FRONTEND_FILES_COUNT}

Run backend:

  docker compose up -d backend

Run frontend:

  cd frontend
  npm run dev

Open:

  http://localhost:5173
MD
}

if ! command -v node >/dev/null 2>&1; then
  log "ERROR: Node.js is not installed"
  STATUS="needs_human"
  STAGE="node_not_found"
  create_report
  exit 0
fi

if ! command -v npm >/dev/null 2>&1; then
  log "ERROR: npm is not installed"
  STATUS="needs_human"
  STAGE="npm_not_found"
  create_report
  exit 0
fi

NODE_VERSION="$(node --version)"
NPM_VERSION="$(npm --version)"

log "Node: ${NODE_VERSION}"
log "NPM: ${NPM_VERSION}"

log "Creating frontend directory"
mkdir -p frontend/src/components

cat > frontend/package.json <<'PKG_JSON'
{
  "name": "trading-terminal-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
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

cat > frontend/.gitignore <<'GITIGNORE'
node_modules
dist
*.local
.DS_Store
GITIGNORE

cat > frontend/README.md <<'README_MD'
# Trading Terminal Frontend

React + TypeScript + Vite + Tailwind frontend for Trading Terminal.

Backend API should be available on:

  http://localhost:8000

Start backend:

  docker compose up -d backend

Install frontend dependencies:

  npm install

Start dev server:

  npm run dev

Open:

  http://localhost:5173

Build:

  npm run build
README_MD

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

export interface Signal {
  id: number;
  ticker: string;
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
TYPES_TS

cat > frontend/src/api.ts <<'API_TS'
import type { Instrument, ListResponse, Signal, TopStock } from "./types";

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
  ticker?: string;
  timeframe?: string;
  signal?: string;
}) {
  return getJson<ListResponse<Signal>>(`/api/signals${toQuery(params)}`);
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
import TopStocksPanel from "./components/TopStocksPanel";
import InstrumentsPanel from "./components/InstrumentsPanel";

type Tab = "signals" | "top" | "instruments";

const tabs: Array<{ id: Tab; label: string }> = [
  { id: "signals", label: "Сигналы" },
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
        {tab === "top" && <TopStocksPanel />}
        {tab === "instruments" && <InstrumentsPanel />}
      </main>
    </div>
  );
}
APP_TSX

cat > frontend/src/components/SignalsPanel.tsx <<'SIGNALS_TSX'
import { useEffect, useState } from "react";
import { getSignals } from "../api";
import type { Signal } from "../types";

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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [ticker, setTicker] = useState("");
  const [timeframe, setTimeframe] = useState("");
  const [signal, setSignal] = useState("");

  async function load() {
    setLoading(true);
    setError(null);

    try {
      const response = await getSignals({
        limit: 200,
        ticker: ticker || undefined,
        timeframe: timeframe || undefined,
        signal: signal || undefined,
      });
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

      <div className="text-sm text-slate-400">
        Показано сигналов: {items.length}
      </div>

      <div className="overflow-x-auto rounded border border-slate-800">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              <th className="px-2 py-2 text-left">Timestamp</th>
              <th className="px-2 py-2 text-left">Ticker</th>
              <th className="px-2 py-2 text-left">TF</th>
              <th className="px-2 py-2 text-left">Signal</th>
              <th className="px-2 py-2 text-right">Conf</th>
              <th className="px-2 py-2 text-right">Price</th>
              <th className="px-2 py-2 text-right">RSI</th>
              <th className="px-2 py-2 text-right">MACD</th>
              <th className="px-2 py-2 text-right">BB%</th>
              <th className="px-2 py-2 text-right">VolRatio</th>
              <th className="px-2 py-2 text-right">ATR%</th>
              <th className="px-2 py-2 text-left">Summary</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr
                key={item.id}
                className="border-t border-slate-800 hover:bg-slate-900/60"
              >
                <td className="whitespace-nowrap px-2 py-1">
                  {item.timestamp.replace("T", " ").slice(0, 19)}
                </td>
                <td className="px-2 py-1 font-medium">{item.ticker}</td>
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
                <td className="max-w-xl px-2 py-1 text-slate-300">
                  {item.summary ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
SIGNALS_TSX

cat > frontend/src/components/TopStocksPanel.tsx <<'TOP_TSX'
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
TOP_TSX

cat > frontend/src/components/InstrumentsPanel.tsx <<'INSTRUMENTS_TSX'
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
INSTRUMENTS_TSX

log "Frontend files created"

log "Running npm install"
if ! (cd frontend && npm install >> "${LOG_TXT}" 2>&1); then
  log "ERROR: npm install failed"
  STATUS="failed"
  STAGE="npm_install"
  create_report
  exit 0
fi

log "Running npm run build"
if ! (cd frontend && npm run build >> "${LOG_TXT}" 2>&1); then
  log "ERROR: npm run build failed"
  STATUS="failed"
  STAGE="npm_build"
  create_report
  exit 0
fi

log "Frontend build succeeded"
create_report
log "Done"
