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

// ---- Strategy Lab (task-106) ----
export interface StrategyConfig {
  patterns: Record<string, Record<string, unknown>>;
  confirm_windows: number[];
  commission_pct: number;
  slippage_pct: number;
  risk_reward: { risk: number; reward: number } | null;
  n_runs: number;
  strategy_name?: string;
}
export interface Strategy {
  id: number;
  name: string;
  config: StrategyConfig;
  created_at: string | null;
  in_paper_test?: boolean;
  locked?: boolean;
  description?: string | null;
}
export interface FullSampleMetrics {
  n: number;
  pf: number | null;
  exp_pct: number | null;
  wr: number | null;
  maxdd_pct: number | null;
}
export interface WfPeriod {
  n: number;
  pf: number | null;
  wr: number | null;
  exp_pct: number | null;
}
export interface WalkforwardMetrics {
  ticker: string;
  periods: Record<string, WfPeriod>;
  pf_gt1: string;
  min_pf: number | null;
  avg_pf: number | null;
}
export interface BacktestResultRow {
  id: number;
  ticker: string;
  test_type: "full_sample" | "walkforward";
  depth: string | null;
  metrics: FullSampleMetrics | WalkforwardMetrics | null;
  created_at: string | null;
}
export interface StrategyJobSnapshot {
  status?: string;
  stage?: string;
  started_at?: string | null;
  error?: string | null;
  tickers_total?: number;
  tickers_done?: number;
  current_ticker?: string | null;
  strategy_id?: number;
}

// ---- Paper Trading (task-126) ----
export interface PaperSummary {
  total: number;
  open: number;
  pending: number;
  closed: number;
  realized_pnl_rub: number;
  win_rate: number | null;
  wins: number;
  losses: number;
}
export interface PaperFactors {
  signal_source: string[];
  window_mode: string[];
  rr_mode: string[];
  entry_mode: string[];
}
export interface PaperOverview {
  strategy_name: string;
  strategy_description: string | null;
  factors: PaperFactors;
  summary: PaperSummary;
}
export interface PaperPosition {
  id: number;
  ticker: string;
  entry_ts: string | null;
  entry_price: number | null;
  exit_ts: string | null;
  exit_price: number | null;
  stop_price: number | null;
  take_price: number | null;
  status: string;
  exit_reason: string | null;
  signal_source: string | null;
  window_mode: string | null;
  rr_mode: string | null;
  entry_mode: string | null;
  pnl_rub: number | null;
  pnl_pct: number | null;
  size_lots: number | null;
  lot_size: number | null;
  created_at: string | null;
  updated_at: string | null;
}
export interface LivePosition {
  id: number;
  ticker: string;
  entry_ts: string | null;
  entry_price: number;
  exit_ts: string | null;
  exit_price: number | null;
  stop_price: number;
  take_price: number;
  status: string;
  exit_reason: string | null;
  pnl_rub: number | null;
  pnl_pct: number | null;
  size_lots: number;
  lot_size: number;
  strategy_name: string;
  created_at: string | null;
  updated_at: string | null;
  current_price: number | null;
}
export interface DynamicsPoint {
  ts: string;
  pnl_rub: number;
  cum_pnl_rub: number;
  closed: number;
  wins: number;
}
export interface PaperDynamics {
  timeframe: string;
  points: DynamicsPoint[];
  cum_pnl_rub: number;
}
export interface NotificationStatus {
  status: "connected" | "disconnected";
  configured: boolean;
  checked_at: string;
}

export interface PatternParam {
  key: string;
  label: string;
  type: 'select' | 'multiselect' | 'number' | 'text' | 'boolean';
  options?: (string | number)[];
  min?: number;
  max?: number;
  step?: number;
  default?: unknown;
}

export interface PatternDef {
  id: string;
  label: string;
  hint?: string;
  category?: string;
  params: PatternParam[];
}

// ---- Pattern chart preview (Epic #87, Issue #89) ----
export interface OverlayRay {
  type: "ray";
  from_ts: string;
  to_ts: string;
  price: number;
  level_type?: string;
  method?: string;
  color?: string;
}

export interface OverlayBand {
  type: "band";
  from_ts: string;
  to_ts: string;
  lower: number;
  upper: number;
  level_type?: string;
  method?: string;
  color?: string;
}

export interface OverlayLinePoint {
  ts?: string;
  timestamp?: string;
  value: number;
}

export interface OverlayLine {
  type: "line";
  color?: string;
  label?: string;
  points: OverlayLinePoint[];
}

export interface OverlayMarker {
  type: "marker";
  ts?: string;
  timestamp?: string;
  position?: "aboveBar" | "belowBar" | "inBar";
  color?: string;
  shape?: "arrowUp" | "arrowDown" | "circle" | "square";
  text?: string;
  signal?: string;
}

export type ChartOverlay = OverlayRay | OverlayBand | OverlayLine | OverlayMarker;

export interface PatternPreviewResponse {
  status: "ok" | "empty" | "error" | "unsupported";
  ticker: string;
  pattern_id: string;
  timeframe: string | null;
  date_from: string;
  date_to: string;
  candles: Candle[];
  overlays: ChartOverlay[];
  error?: string;
  meta?: Record<string, unknown>;
}
