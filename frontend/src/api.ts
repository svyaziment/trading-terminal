import type {
  Candle,
  Instrument,
  ListResponse,
  PaginatedResponse,
  Signal,
  SignalStats,
  TopStock,
} from "./types";

function toQuery(params: object): string {
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
    let msg = `HTTP ${response.status}: ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") msg = body.detail;
      else if (body?.detail?.message) msg = body.detail.message;
    } catch { /* ignore */ }
    throw new Error(msg);
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

// ---- Strategy Lab (task-106) ----
import type {
  Strategy,
  StrategyConfig,
  BacktestResultRow,
  StrategyJobSnapshot,
} from "./types";

async function postJson<T>(url: string, payload: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    let msg = `HTTP ${response.status}: ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") msg = body.detail;
      else if (body?.detail?.message) msg = body.detail.message;
    } catch { /* ignore */ }
    throw new Error(msg);
  }
  return (await response.json()) as T;
}

async function deleteJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { method: "DELETE" });
  if (!response.ok) {
    let msg = `HTTP ${response.status}: ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") msg = body.detail;
      else if (body?.detail?.message) msg = body.detail.message;
    } catch { /* ignore */ }
    throw new Error(msg);
  }
  return (await response.json()) as T;
}

export function deleteStrategy(id: number) {
  return deleteJson<{ deleted: boolean; id: number }>(`/api/strategies/${id}`);
}

export function saveStrategy(payload: { name: string; config: StrategyConfig }) {
  return postJson<{ id: number; name: string; config: StrategyConfig }>(
    "/api/strategies",
    payload
  );
}
export function listStrategies() {
  return getJson<{ strategies: Strategy[] }>("/api/strategies");
}
export function runStrategy(
  id: number,
  payload: { tickers: string[]; test_types: string[]; depth: string; date_from?: string; date_to?: string }
) {
  return postJson<{ accepted: boolean; job: StrategyJobSnapshot }>(
    `/api/strategies/${id}/run`,
    payload
  );
}
export function strategyResults(id: number) {
  return getJson<{ strategy_id: number; results: BacktestResultRow[] }>(
    `/api/strategies/${id}/results`
  );
}
export function strategyRunStatus() {
  return getJson<StrategyJobSnapshot>("/api/strategies/run/status");
}
export function getBigTickers(minCandles = 250000) {
  return getJson<{ min_candles: number; tickers: string[] }>(
    `/api/tickers/big${toQuery({ min_candles: minCandles })}`
  );
}
export function getStrategyDataRange() {
  return getJson<{ min_date: string | null; max_date: string | null }>(
    "/api/strategies/data-range"
  );
}

// ---- Paper Trading (task-126) ----
import type {
  LivePosition,
  NotificationStatus,
  PaperOverview,
  PaperPosition,
  PaperDynamics,
} from "./types";

export interface FactorFilters {
  signal_source?: string;
  window_mode?: string;
  rr_mode?: string;
  entry_mode?: string;
}

export function getPaperOverview(filters: FactorFilters = {}) {
  return getJson<PaperOverview>(`/api/paper-trading/overview${toQuery(filters)}`);
}
export function getPaperPositions(
  params: FactorFilters & {
    status?: string;
    ticker?: string;
    limit?: number;
    offset?: number;
    sort_by?: string;
    sort_dir?: string;
    date_from?: string;
    date_to?: string;
  }
) {
  return getJson<{ items: PaperPosition[]; total: number; limit: number; offset: number }>(
    `/api/paper-trading/positions${toQuery(params)}`
  );
}
export function getPaperDynamics(
  params: FactorFilters & {
    timeframe: string;
    ticker?: string;
    date_from?: string;
    date_to?: string;
  }
) {
  return getJson<PaperDynamics>(`/api/paper-trading/dynamics${toQuery(params)}`);
}

export function getLivePositions(params: {
  status: string;
  ticker?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_dir?: string;
}) {
  return getJson<{ items: LivePosition[]; total: number; limit: number; offset: number }>(
    `/api/live-trading/positions${toQuery(params)}`
  );
}

export function getLiveDynamics(params: {
  timeframe: string;
  ticker?: string;
  date_from?: string;
  date_to?: string;
}) {
  return getJson<PaperDynamics>(`/api/live-trading/dynamics${toQuery(params)}`);
}

export function getNotificationStatus() {
  return getJson<NotificationStatus>("/api/notifications/status");
}

export function getPatterns(): Promise<{ patterns: import("./types").PatternDef[] }> {
  return getJson<{ patterns: import("./types").PatternDef[] }>("/api/patterns");
}

export function getStrategyPlugins(): Promise<{ plugins: string[] }> {
  return getJson<{ plugins: string[] }>("/api/strategies/plugins");
}
