import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import DataTable, { type ColumnDef, type FilterState, type FilterValue, formatFilterValue } from "./ui/DataTable";
import FilterChips from "./ui/FilterChips";
import DatePicker, { localTodayIso } from "./ui/DatePicker";
import PatternSettingsModal from "./PatternSettingsModal";
import {
  saveStrategy,
  listStrategies,
  deleteStrategy,
  runStrategy,
  strategyResults,
  strategyRunStatus,
  getBigTickers,
  getStrategyDataRange,
  getPatterns,
  getStrategyPlugins,
} from "../api";
import type {
  Strategy,
  StrategyConfig,
  BacktestResultRow,
  FullSampleMetrics,
  WalkforwardMetrics,
  PatternDef,
} from "../types";

// issue-12 temporary compatibility helpers (UI modal is #15)
export function patternsToArray(patterns: unknown): string[] {
  if (Array.isArray(patterns)) return patterns as string[];
  if (patterns && typeof patterns === "object") return Object.keys(patterns as Record<string, unknown>);
  return [];
}

export function patternsFromArray(patterns: unknown): Record<string, Record<string, unknown>> {
  const arr = Array.isArray(patterns)
    ? (patterns as string[])
    : patterns && typeof patterns === "object"
      ? Object.keys(patterns as Record<string, unknown>)
      : [];

  const result: Record<string, Record<string, unknown>> = {};
  for (const p of arr) {
    result[p] = {};
  }

  return result;
}


/** Offline-only constructor surface. Never used to filter a live GET /api/patterns payload. */
const FALLBACK_PATTERNS: PatternDef[] = [
  { id: "levels_reversal", label: "Levels Reversal", hint: "цена в зоне 4h + подтверждение", category: "levels", params: [] },
  { id: "signal_4h_buy", label: "4H Buy", hint: "активный BUY из trading.signals", category: "signal", params: [] },
];

const PATTERN_CATEGORY_ORDER = [
  "levels",
  "signal",
  "trend",
  "price_action",
  "volume",
  "mean_reversion",
  "breakout",
] as const;

const PATTERN_CATEGORY_LABELS_RU: Record<string, string> = {
  levels: "Уровни",
  signal: "Сигнал",
  trend: "Тренд",
  price_action: "Ценовое действие",
  volume: "Объём",
  mean_reversion: "Возврат к среднему",
  breakout: "Пробой",
  other: "Другие",
};

type PatternGroup = { category: string; label: string; patterns: PatternDef[] };

function groupPatternsByCategory(defs: PatternDef[]): PatternGroup[] {
  const buckets = new Map<string, PatternDef[]>();
  for (const def of defs) {
    const category = def.category || "other";
    const list = buckets.get(category);
    if (list) list.push(def);
    else buckets.set(category, [def]);
  }
  const groups: PatternGroup[] = [];
  const seen = new Set<string>();
  for (const category of PATTERN_CATEGORY_ORDER) {
    const patterns = buckets.get(category);
    if (!patterns?.length) continue;
    groups.push({
      category,
      label: PATTERN_CATEGORY_LABELS_RU[category] ?? category,
      patterns,
    });
    seen.add(category);
  }
  for (const [category, patterns] of buckets) {
    if (seen.has(category) || !patterns.length) continue;
    groups.push({
      category,
      label: PATTERN_CATEGORY_LABELS_RU[category] ?? category,
      patterns,
    });
  }
  return groups;
}

const DEPTHS = [
  { id: "express", label: "Экспресс", hint: "6 мес · 3 тикера" },
  { id: "serious", label: "Серьёзный", hint: "6 мес · 15 · WF" },
  { id: "very_serious", label: "Полный", hint: "2 года · все · WF" },
];
const METHODS = [
  { id: "full_sample", label: "Full-sample" },
  { id: "walkforward", label: "Walk-forward" },
];
const WF_PERIODS = ["2024-H2", "2025-H1", "2025-H2", "2026-H1"];

const fmt = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d);

function pfTone(pf: number | null | undefined): string {
  if (pf === null || pf === undefined || Number.isNaN(pf)) return "text-slate-500";
  if (pf < 1.0) return "text-rose-400";
  if (pf < 1.3) return "text-amber-300";
  if (pf < 2.0) return "text-emerald-300";
  return "text-emerald-400 font-semibold";
}
function pfHeat(pf: number | null | undefined): string {
  if (pf === null || pf === undefined || Number.isNaN(pf))
    return "bg-slate-800/40 text-slate-500";
  if (pf < 1.0) return "bg-rose-500/15 text-rose-300";
  if (pf < 1.5) return "bg-amber-500/10 text-amber-300";
  if (pf < 2.5) return "bg-emerald-500/10 text-emerald-300";
  return "bg-emerald-500/20 text-emerald-200 font-semibold";
}

function Section(props: { title: string; badge?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
          {props.title}
        </h3>
        {props.badge && (
          <span className="rounded border border-sky-700/50 bg-sky-500/10 px-1.5 py-0.5 font-mono text-[10px] text-sky-300">
            {props.badge}
          </span>
        )}
      </div>
      {props.children}
    </section>
  );
}

function Chip(props: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
  title?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      title={props.title}
      disabled={props.disabled}
      onClick={props.onClick}
      className={
        "rounded border px-2 py-1 font-mono text-[11px] transition-all duration-150 active:scale-95 disabled:cursor-not-allowed " +
        (props.on
          ? "border-sky-500/70 bg-sky-500/20 text-sky-200 shadow-[0_0_10px_rgba(56,189,248,0.15)]"
          : "border-slate-700 bg-slate-900 text-slate-400 hover:border-slate-500 hover:text-slate-200")
      }
    >
      {props.children}
    </button>
  );
}

export default function StrategyLab() {
  const [name, setName] = useState("");
  const [registry, setRegistry] = useState<PatternDef[]>([]);
  const [patternConfigs, setPatternConfigs] = useState<Record<string, Record<string, unknown>>>({
    levels_reversal: {},
    signal_4h_buy: {},
  });
  const [settingsTarget, setSettingsTarget] = useState<string | null>(null);
  const [strategyName, setStrategyName] = useState<string>("levels_reversal");
  const [availablePlugins, setAvailablePlugins] = useState<string[]>([]);
  const [commission, setCommission] = useState("0.06");
  const [slippage, setSlippage] = useState("0");
  const [rrOn, setRrOn] = useState(true);
  const [rrRisk, setRrRisk] = useState("1");
  const [rrReward, setRrReward] = useState("2");
  const [methods, setMethods] = useState<string[]>(["full_sample", "walkforward"]);
  const [depth, setDepth] = useState("express");
const [dateFrom, setDateFrom] = useState("");
const [dateTo, setDateTo] = useState("");
const [dataRange, setDataRange] = useState<{ min_date: string | null; max_date: string | null } | null>(null);
  const [tickers, setTickers] = useState<string[]>(["RUAL", "GMKN", "PIKK"]);
  const [bigTickers, setBigTickers] = useState<string[]>([]);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [results, setResults] = useState<BacktestResultRow[]>([]);
  const [job, setJob] = useState<{ status?: string; tickers_total?: number; tickers_done?: number; current_ticker?: string | null; error?: string | null; started_at?: string | null; finished_at?: string | null; stage?: string }>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState(false);
  const prevStatus = useRef<string | undefined>(undefined);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [justFinished, setJustFinished] = useState<"done" | "failed" | null>(null);
  const [prefillFlash, setPrefillFlash] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Strategy | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [nowTs, setNowTs] = useState(Date.now());
  const pendingRun = useRef<number | null>(null);
  const lastTickerRef = useRef<string | null>(null);

  const enabledIds = useMemo(() => Object.keys(patternConfigs), [patternConfigs]);
  const levelsOn = "levels_reversal" in patternConfigs;

  function registryDefaults(id: string): Record<string, unknown> {
    const def = registry.find((d) => d.id === id);
    const out: Record<string, unknown> = {};
    if (def) {
      for (const p of def.params) if (p.default !== undefined) out[p.key] = p.default;
    }
    return out;
  }

  function effectiveParams(id: string): Record<string, unknown> {
    return { ...registryDefaults(id), ...(patternConfigs[id] ?? {}) };
  }

  function isTuned(id: string): boolean {
    const saved = patternConfigs[id] ?? {};
    const defs = registryDefaults(id);
    return Object.keys(saved).some((k) => JSON.stringify(saved[k] ?? null) !== JSON.stringify(defs[k] ?? null));
  }

  const windows = useMemo(() => {
    if (!levelsOn) return [] as number[];
    const w = effectiveParams("levels_reversal").confirm_windows;
    return Array.isArray(w) ? (w as number[]) : ([] as number[]);
  }, [patternConfigs, registry, levelsOn]);

  const patternDefs = useMemo<PatternDef[]>(
    () => (registry.length > 0 ? registry : FALLBACK_PATTERNS),
    [registry],
  );
  const patternGroups = useMemo(() => groupPatternsByCategory(patternDefs), [patternDefs]);

  const settingsDef = settingsTarget
    ? registry.find((d) => d.id === settingsTarget) ?? patternDefs.find((d) => d.id === settingsTarget) ?? null
    : null;

  const config: StrategyConfig = useMemo(() => {
    const full: Record<string, Record<string, unknown>> = {};
    for (const id of Object.keys(patternConfigs)) full[id] = effectiveParams(id);
    return {
      patterns: full,
      confirm_windows: windows,
      commission_pct: parseFloat(commission) || 0,
      slippage_pct: parseFloat(slippage) || 0,
      risk_reward: rrOn
        ? { risk: parseFloat(rrRisk) || 1, reward: parseFloat(rrReward) || 2 }
        : null,
      n_runs: 1,
      strategy_name: strategyName,
    };
  }, [patternConfigs, registry, windows, commission, slippage, rrOn, rrRisk, rrReward, strategyName]);

  const selectedStrategy = strategies.find((s) => s.id === selectedId) ?? null;
  const isLocked = selectedStrategy?.locked === true;

  async function reloadStrategies() {
    try {
      const r = await listStrategies();
      setStrategies(r.strategies);
    } catch { /* transient */ }
  }
  async function loadResults(id: number) {
    try {
      const r = await strategyResults(id);
      setResults(r.results);
    } catch { /* transient */ }
  }

  useEffect(() => {
    void (async () => {
      try {
        const pt = await getPatterns();
        setRegistry(pt.patterns);
      } catch { /* transient */ }
      try {
        const sp = await getStrategyPlugins();
        setAvailablePlugins(sp.plugins);
      } catch { /* transient */ }
      try {
        const bt = await getBigTickers();
        setBigTickers(bt.tickers);
      } catch { /* transient */ }
    try {
      const dr = await getStrategyDataRange();
      setDataRange(dr);
    } catch { /* transient */ }
      await reloadStrategies();
    })();
  }, []);

  const running = job.status === "running";
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const s = await strategyRunStatus();
        if (alive) setJob(s);
      } catch { /* transient */ }
    };
    void tick();
    const id = window.setInterval(tick, running ? 2000 : 6000);
    return () => { alive = false; window.clearInterval(id); };
  }, [running]);

  // live log of per-ticker progress
  useEffect(() => {
    if (job.status === "running") {
      const tk = job.current_ticker ?? null;
      if (tk && tk !== lastTickerRef.current) {
        lastTickerRef.current = tk;
        const line = "→ " + tk + " (" + (job.tickers_done ?? 0) + "/" + (job.tickers_total ?? "…") + ")";
        setLogLines((p) => [...p.slice(-7), line]);
      }
    } else {
      lastTickerRef.current = null;
    }
  }, [job.status, job.current_ticker, job.tickers_done, job.tickers_total]);
  
  // elapsed timer while running
  useEffect(() => {
    if (job.status !== "running") return;
    const id = window.setInterval(() => setNowTs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [job.status]);
  
  // completion: load results + flash (robust even if "running" was never observed)
  useEffect(() => {
    const st = job.status;
    if (st === "done" || st === "failed") {
      const pid = pendingRun.current;
      if (pid !== null) {
        pendingRun.current = null;
        void loadResults(pid);
        setJustFinished(st);
        setLogLines((p) => [...p.slice(-7), st === "done" ? "✓ расчёт завершён" : "✗ ошибка: " + (job.error ?? "?")]);
        window.setTimeout(() => setJustFinished(null), 3000);
      } else if (prevStatus.current === "running" && st === "done" && selectedId !== null) {
        void loadResults(selectedId);
      }
    }
    prevStatus.current = st;
  }, [job.status, selectedId, job.error]);
  
  const elapsedSec = running && job.started_at ? Math.max(0, Math.floor((nowTs - new Date(job.started_at).getTime()) / 1000)) : null;
  const elapsedFmt = elapsedSec === null ? "" : Math.floor(elapsedSec / 60) + ":" + String(elapsedSec % 60).padStart(2, "0");

  function togglePattern(id: string) {
    setPatternConfigs((pc) => {
      if (id in pc) {
        const next = { ...pc };
        delete next[id];
        return next;
      }
      return { ...pc, [id]: {} };
    });
  }
  function openSettings(id: string) {
    setPatternConfigs((pc) => (id in pc ? pc : { ...pc, [id]: {} }));
    setSettingsTarget(id);
  }
  function applySettings(id: string, values: Record<string, unknown>) {
    setPatternConfigs((pc) => ({ ...pc, [id]: values }));
    setSettingsTarget(null);
  }
  function toggleMethod(id: string) {
    setMethods((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  }
  function toggleCustomPeriod() {
  if (depth === "custom") {
    setDepth("express");
  } else {
    setDepth("custom");
    setMethods(["full_sample"]);
    const today = localTodayIso();
    setDateFrom((d) => d || today);
    setDateTo((d) => d || today);
  }
}
function toggleTicker(t: string) {
    setTickers((p) => (p.includes(t) ? p.filter((x) => x !== t) : [...p, t]));
  }

  function loadStrategy(s: Strategy) {
    setSelectedId(s.id);
    setName(s.name);
    const c = s.config;
    // config.patterns: старый формат (список id) или новый (record id -> params)
    const rawPatterns = c.patterns as unknown;
    if (Array.isArray(rawPatterns)) {
      const rec: Record<string, Record<string, unknown>> = {};
      for (const pid of rawPatterns as string[]) rec[pid] = {};
      if (rec["levels_reversal"] && Array.isArray(c.confirm_windows)) {
        rec["levels_reversal"] = { confirm_windows: c.confirm_windows };
      }
      setPatternConfigs(rec);
    } else {
      setPatternConfigs((rawPatterns as Record<string, Record<string, unknown>> | null) ?? {});
    }
    setStrategyName(c.strategy_name ?? "levels_reversal");
    setCommission(String(c.commission_pct ?? 0.06));
    setSlippage(String(c.slippage_pct ?? 0));
    setRrOn(c.risk_reward !== null && c.risk_reward !== undefined);
    setRrRisk(String(c.risk_reward?.risk ?? 1));
    setRrReward(String(c.risk_reward?.reward ?? 2));
    const rp = (c as StrategyConfig & { run_params?: { tickers?: string[]; test_types?: string[]; depth?: string; date_from?: string | null; date_to?: string | null } }).run_params;
    if (rp) {
      setPrefillFlash(true);
      window.setTimeout(() => setPrefillFlash(false), 1800);
      if (Array.isArray(rp.test_types) && rp.test_types.length > 0) setMethods(rp.test_types);
      if (Array.isArray(rp.tickers) && rp.tickers.length > 0) setTickers(rp.tickers);
      if (rp.date_from && rp.date_to) {
        setDepth("custom");
        setDateFrom(rp.date_from);
        setDateTo(rp.date_to);
      } else if (rp.depth) {
        setDepth(rp.depth);
      }
    }
    setError(null);
    void loadResults(s.id);
  }

  function copyToNew() {
    if (!selectedStrategy) return;
    setName(selectedStrategy.name + "_v2");
    setSelectedId(null);
    setError(null);
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteStrategy(deleteTarget.id);
      if (selectedId === deleteTarget.id) {
        setSelectedId(null);
        setResults([]);
      }
      setDeleteTarget(null);
      await reloadStrategies();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  }

  async function handleSaveRun() {
    const nm = name.trim();
    if (!nm) { setError("Укажите имя стратегии"); return; }
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(nm)) { setError("Имя: английские буквы, цифры, _ и - (1-64)"); return; }
    if (enabledIds.length === 0) { setError("Выберите хотя бы один паттерн"); return; }
    if (tickers.length === 0) { setError("Выберите хотя бы один тикер"); return; }
    if (methods.length === 0) { setError("Выберите хотя бы один метод теста"); return; }
if (depth === "custom") {
  if (!dateFrom || !dateTo) { setError("Укажите период «с» и «до»"); return; }
  if (dateFrom > dateTo) { setError("Дата «с» не может быть позже даты «до»"); return; }
}
    setBusy(true); setError(null);
    try {
      const saved = await saveStrategy({ name: nm, config });
      setSelectedId(saved.id);
      setFlash(true);
      window.setTimeout(() => setFlash(false), 1400);
      const run = await runStrategy(
  saved.id,
  depth === "custom"
    ? { tickers, test_types: ["full_sample"], depth: "express", date_from: dateFrom, date_to: dateTo }
    : { tickers, test_types: methods, depth }
);
      pendingRun.current = saved.id;
      setLogLines(["→ запуск расчёта…"]);
      setJustFinished(null);
      if (run.job) setJob(run.job);
      await reloadStrategies();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const fullSample = useMemo(() => results.filter((r) => r.test_type === "full_sample"), [results]);
  const walkforward = useMemo(() => results.filter((r) => r.test_type === "walkforward"), [results]);

  const summary = useMemo(() => {
    const rows = fullSample.filter((r) => {
      const m = r.metrics as FullSampleMetrics | null;
      return m && m.pf !== null && m.pf !== undefined;
    });
    if (rows.length === 0) return null;
    const pfs = rows.map((r) => (r.metrics as FullSampleMetrics).pf as number);
    const totalN = rows.reduce((a, r) => a + ((r.metrics as FullSampleMetrics).n || 0), 0);
    const best = rows.reduce((a, b) =>
      ((a.metrics as FullSampleMetrics).pf as number) >= ((b.metrics as FullSampleMetrics).pf as number) ? a : b
    );
    return {
      avgPf: pfs.reduce((a, b) => a + b, 0) / pfs.length,
      totalN,
      bestTicker: best.ticker,
      bestPf: (best.metrics as FullSampleMetrics).pf as number,
      count: rows.length,
    };
  }, [fullSample]);

  const periodHint = useMemo(() => {
  if (!dateFrom || !dateTo) {
    return "доступный диапазон: " + (dataRange?.min_date ?? "…") + " — " + (dataRange?.max_date ?? "…");
  }
  if (dateFrom > dateTo) return "⚠ дата «с» позже даты «до»";
  const fmtDay = (d: string) => d.split("-").reverse().join(".");
  if (dateFrom === dateTo) {
    return "1 день: " + fmtDay(dateFrom) + " 00:00:00 — " + fmtDay(dateTo) + " 23:59:59";
  }
  const days = Math.round((new Date(dateTo).getTime() - new Date(dateFrom).getTime()) / 86400000) + 1;
  return fmtDay(dateFrom) + " — " + fmtDay(dateTo) + " (" + days + " дн.)";
}, [dateFrom, dateTo, dataRange]);
const progressPct =
    (job.tickers_total ?? 0) > 0
      ? Math.round(((job.tickers_done ?? 0) / (job.tickers_total as number)) * 100)
      : 0;

    type Trade = {
      ticker: string;
      created_at: string | null;
      entry_ts: string;
      exit_ts: string;
      entry_price: number;
      exit_price: number;
      exit_reason: string;
      bars_held: number;
      net_return_pct: number;
    };
    const fmtTs = (s: string) => {
      const p = String(s).replace("T", " ").split(" ");
      const d = (p[0] || "").split("-");
      return (d.length === 3 ? d[2] + "." + d[1] : p[0]) + " " + (p[1] || "").slice(0, 5);
    };
    const tradesData = useMemo(() => {
      const rows: Array<{ ticker: string; trades: Trade[] }> = [];
      for (const r of fullSample) {
        const m = r.metrics as (FullSampleMetrics & { trades?: Array<Omit<Trade, "ticker" | "created_at">> }) | null;
        if (m && Array.isArray(m.trades) && m.trades.length > 0) {
          rows.push({ ticker: r.ticker, trades: m.trades.map((t) => ({ ...t, ticker: r.ticker, created_at: r.created_at })) });
        }
      }
      return rows;
    }, [fullSample]);
    const allTrades = useMemo(() => tradesData.flatMap((td) => td.trades), [tradesData]);
    const tickerOptions = useMemo(() => {
      const seen = new Set<string>();
      const out: string[] = [];
      for (const t of [...bigTickers, ...tickers]) {
        if (!seen.has(t)) { seen.add(t); out.push(t); }
      }
      return out;
    }, [bigTickers, tickers]);
  const [labFilters, setLabFilters] = useState<FilterState>({});
  const [tradeStats, setTradeStats] = useState({ wins: 0, losses: 0, totalNet: 0 });

  const btColumns = useMemo<ColumnDef<BacktestResultRow>[]>(() => [
    {
      key: "ticker",
      label: "Тикер",
      accessor: (r) => r.ticker,
      render: (r) => <span className="font-mono font-medium text-slate-200">{r.ticker}</span>,
      filter: { kind: "select" },
    },
    {
      key: "n",
      label: "n",
      numeric: true,
      accessor: (r) => ((r.metrics as FullSampleMetrics | null)?.n ?? null),
      render: (r) => <span className="text-slate-400">{(r.metrics as FullSampleMetrics).n ?? "—"}</span>,
      filter: { kind: "range" },
    },
    {
      key: "pf",
      label: "PF",
      numeric: true,
      accessor: (r) => ((r.metrics as FullSampleMetrics | null)?.pf ?? null),
      render: (r) => <span className={pfTone((r.metrics as FullSampleMetrics).pf)}>{fmt((r.metrics as FullSampleMetrics).pf)}</span>,
      filter: { kind: "range", presets: [{ label: "PF ≥ 1", min: 1 }, { label: "PF ≥ 2", min: 2 }] },
    },
    {
      key: "exp_pct",
      label: "Exp %",
      numeric: true,
      accessor: (r) => ((r.metrics as FullSampleMetrics | null)?.exp_pct ?? null),
      render: (r) => {
        const v = (r.metrics as FullSampleMetrics).exp_pct ?? 0;
        return <span className={v >= 0 ? "text-emerald-300" : "text-rose-400"}>{(v >= 0 ? "+" : "") + fmt(v, 3)}</span>;
      },
      filter: { kind: "range", presets: [{ label: "Exp ≥ 0", min: 0 }, { label: "Exp < 0", max: -0.000001 }] },
    },
    {
      key: "wr",
      label: "WR",
      numeric: true,
      accessor: (r) => ((r.metrics as FullSampleMetrics | null)?.wr ?? null),
      render: (r) => <span className="text-slate-300">{fmt((r.metrics as FullSampleMetrics).wr, 1)}</span>,
      filter: { kind: "range", presets: [{ label: "WR ≥ 50", min: 50 }] },
    },
    {
      key: "maxdd_pct",
      label: "MaxDD %",
      numeric: true,
      accessor: (r) => ((r.metrics as FullSampleMetrics | null)?.maxdd_pct ?? null),
      render: (r) => <span className="text-amber-300/90">{fmt((r.metrics as FullSampleMetrics).maxdd_pct, 1)}</span>,
      filter: { kind: "range", presets: [{ label: "DD ≤ 10%", max: 10 }] },
    },
  ], []);

  const tradeColumns = useMemo<ColumnDef<Trade>[]>(() => [
    {
      key: "ticker",
      label: "Тикер",
      accessor: (t) => t.ticker,
      render: (t) => <span className="font-mono font-medium text-slate-200">{t.ticker}</span>,
      filter: { kind: "select" },
    },
    {
      key: "created_at",
      label: "Создана",
      accessor: (t) => t.created_at ?? "",
      render: (t) => <span className="font-mono text-[11px] text-slate-500">{t.created_at ? fmtTs(t.created_at) : "—"}</span>,
    },
    {
      key: "entry_ts",
      label: "Вход",
      accessor: (t) => t.entry_ts,
      render: (t) => <span className="font-mono text-[11px] text-slate-400">{fmtTs(t.entry_ts)}</span>,
    },
    {
      key: "entry_price",
      label: "Цена вх.",
      numeric: true,
      accessor: (t) => t.entry_price,
      render: (t) => <span className="text-slate-300">{Number(t.entry_price).toFixed(2)}</span>,
    },
    {
      key: "exit_ts",
      label: "Выход",
      accessor: (t) => t.exit_ts,
      render: (t) => <span className="font-mono text-[11px] text-slate-400">{fmtTs(t.exit_ts)}</span>,
    },
    {
      key: "exit_price",
      label: "Цена вых.",
      numeric: true,
      accessor: (t) => t.exit_price,
      render: (t) => <span className="text-slate-300">{Number(t.exit_price).toFixed(2)}</span>,
    },
    {
      key: "exit_reason",
      label: "Причина",
      accessor: (t) => t.exit_reason,
      render: (t) => (
        <span className={"inline-block rounded px-1.5 py-0.5 font-mono text-[10px] " + (t.exit_reason === "take" ? "bg-emerald-500/15 text-emerald-300" : "bg-rose-500/15 text-rose-300")}>
          {t.exit_reason === "take" ? "тейк" : "стоп"}
        </span>
      ),
      filter: { kind: "select", options: ["take", "stop"], optionLabel: (v) => (v === "take" ? "тейк" : "стоп") },
    },
    {
      key: "bars_held",
      label: "Баров",
      numeric: true,
      accessor: (t) => t.bars_held,
      render: (t) => <span className="text-slate-400">{t.bars_held}</span>,
    },
    {
      key: "net_return_pct",
      label: "Net %",
      numeric: true,
      accessor: (t) => t.net_return_pct,
      render: (t) => (
        <span className={"font-semibold " + (t.net_return_pct > 0 ? "text-emerald-300" : "text-rose-300")}>
          {(t.net_return_pct > 0 ? "+" : "") + Number(t.net_return_pct).toFixed(3)}
        </span>
      ),
      filter: { kind: "range", presets: [{ label: "Прибыль > 0", min: 0.000001 }, { label: "Убыток < 0", max: -0.000001 }] },
    },
  ], []);

  const handleTradesVisible = useCallback((rows: Trade[]) => {
    const wins = rows.filter((t) => t.net_return_pct > 0).length;
    setTradeStats({
      wins,
      losses: rows.length - wins,
      totalNet: rows.reduce((a, t) => a + t.net_return_pct, 0),
    });
  }, []);

  const tradesCsvName = useMemo(() => {
    const f = labFilters["ticker"];
    return "trades_" + (f && f.kind === "select" ? f.value : "all") + ".csv";
  }, [labFilters]);

  const filterChipLabels = useMemo<Record<string, string>>(() => ({
    ticker: "ticker",
    n: "n",
    pf: "PF",
    exp_pct: "Exp%",
    wr: "WR",
    maxdd_pct: "MaxDD%",
    exit_reason: "причина",
    net_return_pct: "Net%",
  }), []);

  const filterChipValue = useCallback((key: string, v: FilterValue): string => {
    if (key === "exit_reason" && v.kind === "select") return v.value === "take" ? "тейк" : "стоп";
    return formatFilterValue(v);
  }, []);
  const wfColumns = useMemo<ColumnDef<BacktestResultRow>[]>(() => [
    {
      key: "ticker",
      label: "Тикер",
      accessor: (r) => r.ticker,
      render: (r) => <span className="font-mono font-medium text-slate-200">{r.ticker}</span>,
      filter: { kind: "select" },
    },
    ...WF_PERIODS.map((p): ColumnDef<BacktestResultRow> => ({
      key: "wf_" + p,
      label: p,
      numeric: true,
      accessor: (r) => ((r.metrics as WalkforwardMetrics | null)?.periods?.[p]?.pf ?? null),
      render: (r) => {
        const pf = (r.metrics as WalkforwardMetrics | null)?.periods?.[p]?.pf ?? null;
        return (
          <span className={"inline-block min-w-[3.2rem] rounded px-1.5 py-0.5 text-center font-mono text-[11px] tabular-nums " + pfHeat(pf)}>
            {fmt(pf)}
          </span>
        );
      },
    })),
    {
      key: "pf_gt1",
      label: "PF>1",
      numeric: true,
      accessor: (r) => ((r.metrics as WalkforwardMetrics | null)?.pf_gt1 ?? null),
      render: (r) => <span className="text-slate-300">{(r.metrics as WalkforwardMetrics).pf_gt1 ?? "—"}</span>,
    },
    {
      key: "min_pf",
      label: "min",
      numeric: true,
      accessor: (r) => ((r.metrics as WalkforwardMetrics | null)?.min_pf ?? null),
      render: (r) => <span className={pfTone((r.metrics as WalkforwardMetrics).min_pf)}>{fmt((r.metrics as WalkforwardMetrics).min_pf)}</span>,
    },
    {
      key: "avg_pf",
      label: "avg",
      numeric: true,
      accessor: (r) => ((r.metrics as WalkforwardMetrics | null)?.avg_pf ?? null),
      render: (r) => <span className={pfTone((r.metrics as WalkforwardMetrics).avg_pf)}>{fmt((r.metrics as WalkforwardMetrics).avg_pf)}</span>,
    },
  ], []);

  const numInput =
    "w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-200 outline-none transition focus:border-sky-500 disabled:cursor-not-allowed";

  return (
    <div className="flex min-h-0 flex-1 gap-4">
      <style>{`
        @keyframes sl-shimmer { 0% { transform: translateX(-100%) } 100% { transform: translateX(260%) } }
        @keyframes sl-fade { from { opacity:0; transform: translateY(6px) } to { opacity:1; transform: translateY(0) } }
        @keyframes sl-glow { 0%,100% { box-shadow: 0 0 6px rgba(245,158,11,.25) } 50% { box-shadow: 0 0 14px rgba(245,158,11,.5) } }
      `}</style>

      {/* ================= CONFIG RAIL ================= */}
      <aside className="w-[330px] shrink-0 space-y-3 overflow-y-auto pb-4 pr-1">
        {/* Locked (paper trading) banner */}
        {isLocked && (
          <div
            className="rounded-lg border border-amber-600/60 bg-amber-500/10 p-3"
            style={{ animation: "sl-fade .2s ease-out, sl-glow 2.6s ease-in-out infinite" }}
          >
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2 shrink-0">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-400" />
              </span>
              <span className="font-display text-[11px] font-semibold uppercase tracking-[0.12em] text-amber-200">
                В paper trading · параметры заблокированы
              </span>
            </div>
            <p className="mt-1.5 text-[10px] leading-relaxed text-amber-200/70">
              Идёт тестовый период — конфигурация неизменяема, чтобы не исказить результаты теста.
              Чтобы поэкспериментировать, скопируйте в новую стратегию.
            </p>
            <button
              type="button"
              onClick={copyToNew}
              className="mt-2 w-full rounded border border-amber-600/70 bg-amber-500/20 px-2 py-1.5 font-mono text-[11px] font-medium text-amber-200 transition hover:bg-amber-500/30 active:scale-[0.98]"
            >
              ⧉ Скопировать в новую стратегию
            </button>
          </div>
        )}

        {/* Config sections: read-only when locked */}
        <div className={isLocked ? "space-y-3 select-none opacity-50" : "space-y-3"} aria-disabled={isLocked}>
          <Section title="Стратегия" badge={flash ? "сохранено ✓" : isLocked ? "🔒 только чтение" : undefined}>
            <input
              value={name}
              readOnly={isLocked}
              onChange={(e) => setName(e.target.value)}
              placeholder="name_in_english"
              className={
                "w-full rounded border bg-slate-950 px-2.5 py-1.5 font-mono text-sm text-slate-100 outline-none transition placeholder:text-slate-600 disabled:cursor-not-allowed " +
                (name && !/^[A-Za-z0-9_-]{0,64}$/.test(name)
                  ? "border-rose-600 focus:border-rose-500"
                  : "border-slate-700 focus:border-sky-500")
              }
            />
            <p className="mt-1 text-[10px] text-slate-600">латиница, цифры, «_», «-»</p>
            <label className="mt-2 block">
              <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">стратегия (плагин)</span>
              <select
                value={strategyName}
                disabled={isLocked}
                onChange={(e) => setStrategyName(e.target.value)}
                className="w-full rounded border border-slate-700 bg-slate-950 px-2.5 py-1.5 font-mono text-sm text-slate-100 outline-none transition focus:border-sky-500 disabled:cursor-not-allowed"
              >
                {(availablePlugins.length > 0 ? availablePlugins : ["levels_reversal"]).map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </label>
            {selectedStrategy?.description && (
              <p className="mt-1.5 rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5 text-[10px] leading-relaxed text-slate-400">
                {selectedStrategy.description}
              </p>
            )}
          </Section>

          <Section title="Паттерны" badge={enabledIds.length > 1 ? "AND" : String(enabledIds.length)}>
            <div className="space-y-3">
              {patternGroups.map((group) => (
                <div key={group.category} className="space-y-1.5">
                  <h4 className="font-display text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                    {group.label}
                  </h4>
                  {group.patterns.map((p) => {
                const on = p.id in patternConfigs;
                const tuned = on && isTuned(p.id);
                return (
                  <div
                    key={p.id}
                    className={
                      "flex items-center gap-1 rounded border px-2 py-1.5 transition-all duration-150 " +
                      (on
                        ? "border-sky-500/70 bg-sky-500/15"
                        : "border-slate-800 bg-slate-950/60 hover:border-slate-600")
                    }
                  >
                    <button
                      type="button"
                      disabled={isLocked}
                      onClick={() => togglePattern(p.id)}
                      className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:cursor-not-allowed"
                    >
                      <span
                        className={
                          "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm border font-mono text-[9px] transition " +
                          (on
                            ? "border-sky-400 bg-sky-500 text-slate-950"
                            : "border-slate-600 text-transparent")
                        }
                      >
                        ✓
                      </span>
                      <span className="min-w-0">
                        <span className={"block truncate font-mono text-[11px] " + (on ? "text-sky-200" : "text-slate-300")}>
                          {p.label}
                        </span>
                        <span className="block truncate text-[10px] text-slate-600">{p.hint}</span>
                      </span>
                    </button>
                    {p.params.length > 0 && (
                      <button
                        type="button"
                        disabled={isLocked}
                        onClick={() => openSettings(p.id)}
                        title="Настройки паттерна"
                        className={
                          "relative shrink-0 rounded p-1 transition hover:bg-slate-800 disabled:cursor-not-allowed " +
                          (tuned ? "text-sky-300" : "text-slate-500 hover:text-slate-300")
                        }
                      >
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                          <circle cx="12" cy="12" r="3" />
                          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
                        </svg>
                        {tuned && (
                          <span className="absolute right-0 top-0 h-1.5 w-1.5 rounded-full bg-amber-400 shadow-[0_0_5px_rgba(245,158,11,.7)]" />
                        )}
                      </button>
                    )}
                  </div>
                    );
                  })}
                </div>
              ))}
            </div>
            {enabledIds.length > 1 && (
              <p className="mt-2 rounded border border-amber-700/40 bg-amber-500/10 px-2 py-1 text-[10px] text-amber-200">
                сигнал только при совместном срабатывании всех ({enabledIds.length})
              </p>
            )}
          </Section>


          <Section title="Издержки">
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">комиссия %</span>
                <input value={commission} readOnly={isLocked} onChange={(e) => setCommission(e.target.value)} className={numInput} />
              </label>
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">проскальз. %</span>
                <input value={slippage} readOnly={isLocked} onChange={(e) => setSlippage(e.target.value)} className={numInput} />
              </label>
            </div>
          </Section>

          <Section title="Risk / Reward" badge={rrOn ? `${rrRisk}:${rrReward}` : "off"}>
            <label className="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
              <input type="checkbox" checked={rrOn} disabled={isLocked} onChange={(e) => setRrOn(e.target.checked)} />
              фильтровать входы
            </label>
            {rrOn && (
              <div className="grid grid-cols-2 gap-2">
                <label className="block">
                  <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">risk</span>
                  <input value={rrRisk} readOnly={isLocked} onChange={(e) => setRrRisk(e.target.value)} className={numInput} />
                </label>
                <label className="block">
                  <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">reward</span>
                  <input value={rrReward} readOnly={isLocked} onChange={(e) => setRrReward(e.target.value)} className={numInput} />
                </label>
              </div>
            )}
          </Section>

          <Section title="Тест" badge={prefillFlash ? "параметры восстановлены ✓" : undefined}>
            <div className={prefillFlash ? "-m-1.5 mb-0.5 rounded-md ring-2 ring-sky-500/60 transition-shadow duration-700" : "hidden"} style={{ animation: "sl-fade .2s ease-out" }} />
            <div className="mb-2 flex flex-wrap gap-1.5">
              {METHODS.map((m) => (
                <Chip key={m.id} on={methods.includes(m.id)} disabled={isLocked || (depth === "custom" && m.id === "walkforward")} onClick={() => toggleMethod(m.id)}>
                  {m.label}
                </Chip>
              ))}
            </div>
            <div className="mb-2 grid grid-cols-3 gap-1.5">
              {DEPTHS.map((d) => (
                <button
                  key={d.id}
                  type="button"
                  disabled={isLocked}
                  onClick={() => setDepth(d.id)}
                  title={d.hint}
                  className={
                    "rounded border px-1.5 py-1 text-[10px] transition disabled:cursor-not-allowed " +
                    (depth === d.id
                      ? "border-sky-500/70 bg-sky-500/20 text-sky-200"
                      : "border-slate-700 text-slate-400 hover:border-slate-500")
                  }
                >
                  {d.label}
                </button>
              ))}
            </div>
            <button
  type="button"
  disabled={isLocked}
  onClick={() => toggleCustomPeriod()}
  className={
    "mb-2 flex w-full items-center justify-center gap-1.5 rounded border px-1.5 py-1.5 text-[10px] font-medium transition-all duration-150 disabled:cursor-not-allowed " +
    (depth === "custom"
      ? "border-amber-500/70 bg-amber-500/15 text-amber-200 shadow-[0_0_10px_rgba(245,158,11,0.15)]"
      : "border-slate-700 text-slate-400 hover:border-amber-600/50 hover:text-amber-300")
  }
>
  <span aria-hidden="true">📅</span> За период
</button>
{depth === "custom" && (
  <div className="mb-2 rounded border border-amber-700/40 bg-amber-500/5 p-2" style={{ animation: "sl-fade .2s ease-out" }}>
    <div className="grid grid-cols-2 gap-2">
      <label className="block">
        <span className="mb-1 block text-[9px] uppercase tracking-wider text-slate-500">с</span>
        <DatePicker value={dateFrom} onChange={setDateFrom} minDate={dataRange?.min_date ?? undefined} maxDate={dataRange?.max_date ?? undefined} />
      </label>
      <label className="block">
        <span className="mb-1 block text-[9px] uppercase tracking-wider text-slate-500">до</span>
        <DatePicker value={dateTo} onChange={setDateTo} minDate={dataRange?.min_date ?? undefined} maxDate={dataRange?.max_date ?? undefined} />
      </label>
    </div>
    <p className="mt-1.5 font-mono text-[9px] leading-relaxed text-slate-400">{periodHint}</p>
    <p className="mt-1 text-[9px] text-amber-300/80">для произвольного периода доступен только full-sample</p>
  </div>
)}
<div className="flex flex-wrap gap-1">
              {tickerOptions.map((t) => (
                <Chip key={t} on={tickers.includes(t)} disabled={isLocked} onClick={() => toggleTicker(t)}>
                  {t}
                </Chip>
              ))}
            </div>
            <div className="mt-1.5 flex items-center justify-between text-[10px] text-slate-600">
              <span>выбрано: {tickers.length}</span>
              <button
                type="button"
                disabled={isLocked}
                className="text-sky-400 transition hover:text-sky-300 disabled:cursor-not-allowed"
                onClick={() => setTickers(tickers.length === tickerOptions.length ? [] : [...tickerOptions])}
              >
                {tickers.length === tickerOptions.length ? "сбросить" : "все"}
              </button>
            </div>
          </Section>
        </div>

        <button
          type="button"
          onClick={() => void handleSaveRun()}
          disabled={busy || running || isLocked}
          title={isLocked ? "Стратегия в paper trading — параметры заблокированы" : undefined}
          className={
            "w-full rounded-md border px-3 py-2.5 font-display text-sm font-semibold tracking-wide transition-all duration-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 " +
            (flash
              ? "border-emerald-500 bg-emerald-500/20 text-emerald-200"
              : "border-sky-500 bg-sky-600 text-white shadow-[0_0_18px_rgba(2,132,199,0.35)] hover:bg-sky-500")
          }
        >
          {isLocked
            ? "🔒 заблокировано (paper trading)"
            : busy
              ? "сохранение…"
              : running
                ? "тест идёт…"
                : flash
                  ? "✓ сохранено"
                  : "Сохранить и запустить"}
        </button>

        {error && (
          <div className="rounded border border-rose-700 bg-rose-500/10 px-2.5 py-1.5 text-[11px] leading-relaxed text-rose-300" style={{ animation: "sl-fade .2s ease-out" }}>
            {error}
          </div>
        )}

        {strategies.length > 0 && (
          <Section title="Сохранённые" badge={String(strategies.length)}>
            <div className="space-y-1">
              {strategies.map((s) => (
                <div
                  key={s.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => loadStrategy(s)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") loadStrategy(s); }}
                  className={
                    "group flex w-full cursor-pointer items-center justify-between gap-2 rounded border px-2 py-1.5 text-left transition " +
                    (selectedId === s.id
                      ? "border-sky-500/70 bg-sky-500/15"
                      : "border-slate-800 bg-slate-950/60 hover:border-slate-600")
                  }
                >
                  <span className={"truncate font-mono text-[11px] " + (selectedId === s.id ? "text-sky-200" : "text-slate-300")}>
                    {s.name}
                  </span>
                  <span className="flex shrink-0 items-center gap-1">
                    {s.in_paper_test && (
                      <span
                        className="inline-flex items-center gap-1 rounded border border-amber-600/60 bg-amber-500/15 px-1.5 py-0.5 font-mono text-[8px] uppercase tracking-wider text-amber-300"
                        title="Тестируется в paper trading"
                      >
                        <span className="relative flex h-1 w-1">
                          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-60" />
                          <span className="relative inline-flex h-1 w-1 rounded-full bg-amber-400" />
                        </span>
                        paper
                      </span>
                    )}
                    {s.locked && (
                      <span className="rounded border border-slate-600 bg-slate-800 px-1 py-0.5 font-mono text-[9px] text-slate-400" title="Параметры заблокированы до конца теста">
                        🔒
                      </span>
                    )}
                    <span className="font-mono text-[9px] text-slate-600">#{s.id}</span>
                    {!s.locked && (
                      <button
                        type="button"
                        title="Удалить стратегию"
                        onClick={(e) => { e.stopPropagation(); setDeleteTarget(s); }}
                        className="rounded p-0.5 text-slate-600 opacity-0 transition hover:bg-rose-500/15 hover:text-rose-300 group-hover:opacity-100"
                      >
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                          <polyline points="3 6 5 6 21 6" />
                          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                        </svg>
                      </button>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </Section>
        )}
      </aside>

      {/* ================= RESULTS ================= */}
      <main
        className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pb-6 pr-1"
        style={{
          backgroundImage: "radial-gradient(circle, rgba(148,163,184,0.06) 1px, transparent 1px)",
          backgroundSize: "22px 22px",
        }}
      >
        {/* Job status */}
        {running && (
          <div className="rounded-lg border border-sky-700/50 bg-sky-500/10 p-3" style={{ animation: "sl-fade .2s ease-out" }}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400 opacity-70" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-sky-400" />
                </span>
                <span className="font-display text-xs font-semibold uppercase tracking-wider text-sky-200">
                  Бэктест выполняется
                </span>
                {job.current_ticker && (
                  <span className="font-mono text-[11px] text-sky-300">{job.current_ticker}</span>
                )}
              </div>
              <span className="font-mono text-[11px] tabular-nums text-sky-300">
                {job.tickers_done ?? 0}/{job.tickers_total ?? "—"}{elapsedFmt ? " · " + elapsedFmt : ""}
              </span>
            </div>
            <div className="relative mt-2 h-1.5 overflow-hidden rounded-full bg-slate-800">
              <div
                className="h-full rounded-full bg-gradient-to-r from-sky-600 to-sky-400 transition-[width] duration-500"
                style={{ width: `${progressPct}%` }}
              />
              <div
                className="absolute inset-y-0 w-1/4 bg-sky-300/30"
                style={{ animation: "sl-shimmer 1.2s linear infinite" }}
              />
            </div>

            {logLines.length > 0 && (
              <div className="mt-2 max-h-24 space-y-0.5 overflow-y-auto rounded border border-sky-900/40 bg-slate-950/60 px-2 py-1.5 font-mono text-[10px] leading-relaxed text-sky-200/80">
                {logLines.map((ln, i) => (
                  <div key={i} className={i === logLines.length - 1 ? "text-sky-100" : "opacity-60"}>{ln}</div>
                ))}
              </div>
            )}
          </div>
        )}
                {justFinished === "done" && !running && (
          <div className="rounded-lg border border-emerald-600/60 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300" style={{ animation: "sl-fade .2s ease-out" }}>
            ✓ Расчёт завершён — результаты обновлены
          </div>
        )}
        {job.status === "failed" && (
          <div className="rounded-lg border border-rose-700 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
            Ошибка джобы: {job.error ?? "неизвестно"}
          </div>
        )}

        {/* Summary strip */}
        {summary && (
          <div className="flex flex-wrap items-end gap-x-8 gap-y-3 rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3" style={{ animation: "sl-fade .25s ease-out" }}>
            <div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">средний PF</div>
              <div className={"font-display text-3xl font-bold tabular-nums " + pfTone(summary.avgPf)}>
                {fmt(summary.avgPf)}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">сделок</div>
              <div className="font-display text-3xl font-bold tabular-nums text-slate-100">{summary.totalN}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">лучший</div>
              <div className="font-display text-xl font-semibold text-slate-100">
                {summary.bestTicker}
                <span className={"ml-2 font-mono text-base " + pfTone(summary.bestPf)}>{fmt(summary.bestPf)}</span>
              </div>
            </div>
            <div className="ml-auto text-right">
              <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">тикеров</div>
              <div className="font-mono text-lg text-slate-300">{summary.count}</div>
            </div>
          </div>
        )}

     {/* Unified filter panel (общая для обеих таблиц) */}
     <FilterChips
       filters={labFilters}
       onChange={setLabFilters}
       labels={filterChipLabels}
       valueLabel={filterChipValue}
     />
     {/* Backtest · full-sample */}
     <DataTable
       columns={btColumns}
       rows={fullSample}
       rowKey={(r) => r.id}
       filters={labFilters}
       onFiltersChange={setLabFilters}
       title="Бэктест · full-sample"
       emptyText="нет результатов — сохраните и запустите стратегию"
     />
             {/* Trades · full-sample */}
             {tradesData.length > 0 && (
               <DataTable
                 columns={tradeColumns}
                 rows={allTrades}
                 rowKey={(t, i) => i + "_" + t.ticker + "_" + t.entry_ts}
                 filters={labFilters}
                 onFiltersChange={setLabFilters}
                 onVisibleRowsChange={handleTradesVisible}
                 defaultSort={{ key: "entry_ts", dir: "desc" }}
                 title="Сделки · full-sample"
                 csv={{ filename: tradesCsvName }}
                 scrollClass="max-h-96 overflow-y-auto"
                 rowClass={(t) => (t.net_return_pct > 0 ? "bg-emerald-500/[0.04]" : "bg-rose-500/[0.04]")}
                 emptyText="нет сделок для выбранного фильтра"
                 headerRight={
                   <span className="font-mono text-[10px] tabular-nums text-slate-400">
                     <span className="text-emerald-300">+{tradeStats.wins}</span>
                     {" / "}
                     <span className="text-rose-300">−{tradeStats.losses}</span>
                     {" · Σ "}
                     <span className={tradeStats.totalNet >= 0 ? "text-emerald-300" : "text-rose-300"}>
                       {(tradeStats.totalNet >= 0 ? "+" : "") + tradeStats.totalNet.toFixed(2)}%
                     </span>
                   </span>
                 }
               />
             )}
     {/* Walk-forward table */}
     <DataTable
       columns={wfColumns}
       rows={walkforward}
       rowKey={(r) => r.id}
       filters={labFilters}
       onFiltersChange={setLabFilters}
       title="Walk-forward · по полугодиям"
       emptyText="нет результатов walk-forward"
     />
      </main>
      {settingsDef && (
        <PatternSettingsModal
          key={settingsDef.id}
          def={settingsDef}
          values={effectiveParams(settingsDef.id)}
          locked={isLocked}
          onSave={(v) => applySettings(settingsDef.id, v)}
          onClose={() => setSettingsTarget(null)}
        />
      )}
      {deleteTarget && createPortal(
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4"
          style={{ animation: "sl-fade .18s ease-out" }}
          onClick={() => { if (!deleting) setDeleteTarget(null); }}
        >
          <div className="w-full max-w-sm rounded-lg border border-slate-700 bg-slate-900 p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center gap-2.5">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-rose-600/60 bg-rose-500/15 text-rose-300">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
              </span>
              <h3 className="font-display text-sm font-semibold uppercase tracking-[0.12em] text-slate-100">Удалить стратегию?</h3>
            </div>
            <p className="mb-1 text-[12px] leading-relaxed text-slate-300">
              Стратегия <span className="font-mono font-semibold text-sky-300">{deleteTarget.name}</span>{' '}
              <span className="font-mono text-slate-500">#{deleteTarget.id}</span> будет удалена из списка.
            </p>
            <p className="mb-4 text-[10px] leading-relaxed text-slate-500">Результаты бэктестов останутся в базе; стратегия перестанет отображаться в «Сохранённых».</p>
            <div className="flex gap-2">
              <button type="button" disabled={deleting} onClick={() => setDeleteTarget(null)} className="flex-1 rounded border border-slate-600 py-2 text-[12px] font-medium text-slate-300 transition hover:bg-slate-800 disabled:opacity-40">Отмена</button>
              <button type="button" disabled={deleting} onClick={() => void handleDelete()} className="flex-1 rounded border border-rose-600 bg-rose-600/80 py-2 text-[12px] font-semibold text-white transition hover:bg-rose-500 disabled:opacity-40">{deleting ? "удаление…" : "Удалить"}</button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
