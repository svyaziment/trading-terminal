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
