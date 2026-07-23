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
