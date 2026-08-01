import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createChart, ColorType, LineStyle } from "lightweight-charts";
import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";
import {
  getPaperOverview,
  getPaperPositions,
  getPaperDynamics,
  type FactorFilters,
} from "../api";
import type { PaperOverview, PaperPosition, DynamicsPoint } from "../types";
import DataTable, {
  type ColumnDef,
  type FilterState,
  type FilterValue,
  type SortState,
  formatFilterValue,
} from "./ui/DataTable";
import FilterChips from "./ui/FilterChips";

/* ==================================================================
   PaperTradingPanel — A/B мониторинг paper trading
   task-005: таблица позиций переведена на единый DataTable
   - серверные фильтры: ticker (text), status (select), 4 A/B-фактора (select)
   - клиентские: entry_price, exit_price, pnl_rub, pnl_pct (по странице)
   - факторные чипы и funnel-фильтры пишут в одно FilterState (синхронно)
   ================================================================== */

const TF_OPTIONS: Array<{ id: "1h" | "1d" | "1w"; label: string }> = [
  { id: "1h", label: "1ч" },
  { id: "1d", label: "1д" },
  { id: "1w", label: "1н" },
];

const FACTOR_LABELS: Array<{ key: keyof FactorFilters; label: string }> = [
  { key: "signal_source", label: "Источник" },
  { key: "window_mode", label: "Окно" },
  { key: "rr_mode", label: "RR" },
  { key: "entry_mode", label: "Вход" },
];

const FACTOR_KEYS = new Set<string>(["signal_source", "window_mode", "rr_mode", "entry_mode"]);
const CLIENT_KEYS = new Set<string>(["entry_price", "exit_price", "pnl_rub", "pnl_pct"]);
const STATUS_OPTIONS = ["open", "pending", "closed_take", "closed_stop", "cancelled"];

const fmtNum = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d);

const fmtTs = (ts: string | null | undefined) =>
  ts ? String(ts).replace("T", " ").slice(0, 16) : "—";

function statusBadge(status: string): string {
  switch (status) {
    case "open": return "border-sky-600/60 bg-sky-500/15 text-sky-300";
    case "pending": return "border-amber-600/60 bg-amber-500/15 text-amber-300";
    case "closed_take": return "border-emerald-600/60 bg-emerald-500/15 text-emerald-300";
    case "closed_stop": return "border-rose-600/60 bg-rose-500/15 text-rose-300";
    default: return "border-slate-600 bg-slate-800/60 text-slate-400";
  }
}
function statusLabel(status: string): string {
  switch (status) {
    case "open": return "открыта";
    case "pending": return "ожидает";
    case "closed_take": return "тейк";
    case "closed_stop": return "стоп";
    case "cancelled": return "отменена";
    default: return status;
  }
}
function pnlCls(v: number | null | undefined): string {
  if (v === null || v === undefined) return "text-slate-600";
  return v >= 0 ? "text-emerald-400" : "text-rose-400";
}
function chipCls(on: boolean): string {
  return (
    "rounded border px-2 py-0.5 font-mono text-[10px] transition-all duration-150 active:scale-95 " +
    (on
      ? "border-sky-500/70 bg-sky-500/20 text-sky-200 shadow-[0_0_8px_rgba(56,189,248,0.15)]"
      : "border-slate-700 bg-slate-900 text-slate-400 hover:border-slate-500 hover:text-slate-200")
  );
}
function toChartTime(ts: string, timeframe: string): Time {
  if (timeframe === "1h") {
    const d = new Date(ts.replace(" ", "T") + "Z");
    return Math.floor(d.getTime() / 1000) as Time;
  }
  return ts.slice(0, 10) as Time;
}
function pageWindow(cur: number, total: number): number[] {
  const out: number[] = [];
  const start = Math.max(1, cur - 2);
  const end = Math.min(total, cur + 2);
  for (let i = start; i <= end; i++) out.push(i);
  return out;
}

const pagBtn = "rounded border border-slate-700 px-2 py-1 disabled:opacity-40 hover:bg-slate-800";

export default function PaperTradingPanel() {
  const [overview, setOverview] = useState<PaperOverview | null>(null);
  const [positions, setPositions] = useState<PaperPosition[]>([]);
  const [total, setTotal] = useState(0);
  const [dynamics, setDynamics] = useState<DynamicsPoint[]>([]);
  const [cumPnl, setCumPnl] = useState(0);
  const [timeframe, setTimeframe] = useState<"1h" | "1d" | "1w">("1d");
  const [filters, setFilters] = useState<FilterState>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [goto, setGoto] = useState("");
  const [sortBy, setSortBy] = useState("created_at");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [now, setNow] = useState(Date.now());
  const [reloadToken, setReloadToken] = useState(0);
  const [infoOpen, setInfoOpen] = useState(false);
  const [visibleCount, setVisibleCount] = useState(0);

  const chartBoxRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const el = chartBoxRef.current;
    if (!el) return;
    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#64748b",
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
        fontSize: 10,
      },
      grid: {
        vertLines: { color: "rgba(148,163,184,0.05)" },
        horzLines: { color: "rgba(148,163,184,0.08)" },
      },
      rightPriceScale: { borderColor: "rgba(148,163,184,0.15)" },
      timeScale: { borderColor: "rgba(148,163,184,0.15)", timeVisible: true, secondsVisible: false },
      crosshair: {
        vertLine: { color: "rgba(56,189,248,0.45)", width: 1, style: LineStyle.Dashed, labelBackgroundColor: "#0ea5e9" },
        horzLine: { color: "rgba(56,189,248,0.45)", width: 1, style: LineStyle.Dashed, labelBackgroundColor: "#0ea5e9" },
      },
      height: 280,
    });
    const series = chart.addAreaSeries({
      lineColor: "#34d399", topColor: "rgba(52,211,153,0.30)", bottomColor: "rgba(52,211,153,0.02)",
      lineWidth: 2, priceLineVisible: false,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    const onResize = () => chart.applyOptions({ width: el.clientWidth });
    onResize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  // --- производные параметры API из единого FilterState ---
  const selVal = (k: string): string | undefined => {
    const f = filters[k];
    return f && f.kind === "select" && f.value ? f.value : undefined;
  };
  const txtVal = (k: string): string | undefined => {
    const f = filters[k];
    return f && f.kind === "text" && f.value ? f.value.toUpperCase() : undefined;
  };
  const factorParams = useMemo(() => {
    const out: FactorFilters = {};
    for (const k of ["signal_source", "window_mode", "rr_mode", "entry_mode"] as Array<keyof FactorFilters>) {
      const f = filters[k];
      if (f && f.kind === "select" && f.value) out[k] = f.value;
    }
    return out;
  }, [filters]);

  useEffect(() => {
    let alive = true;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [ov, pos, dyn] = await Promise.all([
          getPaperOverview(factorParams),
          getPaperPositions({
            ...factorParams,
            ticker: txtVal("ticker"),
            status: selVal("status"),
            limit: pageSize,
            offset: (page - 1) * pageSize,
            sort_by: sortBy,
            sort_dir: sortDir,
          }),
          getPaperDynamics({ ...factorParams, timeframe }),
        ]);
        if (!alive) return;
        setOverview(ov);
        setPositions(pos.items);
        setTotal(pos.total);
        setDynamics(dyn.points);
        setCumPnl(dyn.cum_pnl_rub);
        setLastUpdated(new Date());
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    }
    void load();
    return () => { alive = false; };
  }, [filters, page, pageSize, sortBy, sortDir, timeframe, reloadToken]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = window.setInterval(() => setReloadToken((t) => t + 1), 30000);
    return () => window.clearInterval(id);
  }, [autoRefresh]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    series.setData(dynamics.map((p) => ({ time: toChartTime(p.ts, timeframe), value: p.cum_pnl_rub })));
    const pos = cumPnl >= 0;
    series.applyOptions({
      lineColor: pos ? "#34d399" : "#fb7185",
      topColor: pos ? "rgba(52,211,153,0.30)" : "rgba(251,113,133,0.30)",
      bottomColor: pos ? "rgba(52,211,153,0.02)" : "rgba(251,113,133,0.02)",
    });
    chart.timeScale().fitContent();
  }, [dynamics, timeframe, cumPnl]);

  const columns = useMemo<ColumnDef<PaperPosition>[]>(() => {
    const factorOpts = (k: keyof FactorFilters): string[] => overview?.factors?.[k] ?? [];
    return [
      {
        key: "ticker", label: "Тикер",
        accessor: (p) => p.ticker,
        render: (p) => <span className="font-mono font-semibold text-slate-200">{p.ticker}</span>,
        filter: { kind: "text", placeholder: "например RUAL" },
      },
      { key: "created_at", label: "Создана", accessor: (p) => p.created_at ?? "", render: (p) => <span className="font-mono text-slate-400">{fmtTs(p.created_at)}</span> },
      { key: "entry_ts", label: "Вход", accessor: (p) => p.entry_ts ?? "", render: (p) => <span className="font-mono text-slate-400">{fmtTs(p.entry_ts)}</span> },
      { key: "entry_price", label: "Цена вх.", numeric: true, accessor: (p) => p.entry_price, render: (p) => <span className="text-slate-300">{fmtNum(p.entry_price, 3)}</span>, filter: { kind: "range" } },
      { key: "exit_price", label: "Цена вых.", numeric: true, accessor: (p) => p.exit_price, render: (p) => <span className="text-slate-400">{fmtNum(p.exit_price, 3)}</span>, filter: { kind: "range" } },
      {
        key: "status", label: "Статус",
        accessor: (p) => p.status,
        render: (p) => (
          <span className={"inline-block rounded border px-1.5 py-0.5 font-mono text-[9px] " + statusBadge(p.status)}>
            {statusLabel(p.status)}
          </span>
        ),
        filter: { kind: "select", options: STATUS_OPTIONS, optionLabel: statusLabel },
      },
      { key: "signal_source", label: "Источник", accessor: (p) => p.signal_source ?? "", render: (p) => <span className="font-mono text-[10px] text-sky-300/90">{p.signal_source ?? "—"}</span>, filter: { kind: "select", options: factorOpts("signal_source") } },
      { key: "window_mode", label: "Окно", accessor: (p) => p.window_mode ?? "", render: (p) => <span className="font-mono text-[10px] text-slate-400">{p.window_mode ?? "—"}</span>, filter: { kind: "select", options: factorOpts("window_mode") } },
      { key: "rr_mode", label: "RR", accessor: (p) => p.rr_mode ?? "", render: (p) => <span className="font-mono text-[10px] text-slate-400">{p.rr_mode ?? "—"}</span>, filter: { kind: "select", options: factorOpts("rr_mode") } },
      { key: "entry_mode", label: "Вход", accessor: (p) => p.entry_mode ?? "", render: (p) => <span className="font-mono text-[10px] text-slate-400">{p.entry_mode ?? "—"}</span>, filter: { kind: "select", options: factorOpts("entry_mode") } },
      {
        key: "pnl_rub", label: "PnL ₽", numeric: true,
        accessor: (p) => p.pnl_rub,
        render: (p) => (
          <span className={"font-semibold " + pnlCls(p.pnl_rub)}>
            {p.pnl_rub === null || p.pnl_rub === undefined ? "—" : (p.pnl_rub >= 0 ? "+" : "") + fmtNum(p.pnl_rub, 0)}
          </span>
        ),
        filter: { kind: "range" },
      },
      {
        key: "pnl_pct", label: "PnL %", numeric: true,
        accessor: (p) => p.pnl_pct,
        render: (p) => (
          <span className={pnlCls(p.pnl_pct)}>
            {p.pnl_pct === null || p.pnl_pct === undefined ? "—" : (p.pnl_pct >= 0 ? "+" : "") + fmtNum(p.pnl_pct, 2) + "%"}
          </span>
        ),
        filter: { kind: "range" },
      },
    ];
  }, [overview]);

  function setFactor(key: keyof FactorFilters, value: string | undefined) {
    setFilters((f) => {
      const next = { ...f };
      if (value) next[key] = { kind: "select", value };
      else delete next[key];
      return next;
    });
    setPage(1);
  }

  function handleTableFilters(f: FilterState) {
    setFilters(f);
    setPage(1);
  }

  function handleSort(st: SortState) {
    setSortBy(st.key);
    setSortDir(st.dir);
    setPage(1);
  }

  const handleVisible = useCallback((rows: PaperPosition[]) => {
    setVisibleCount(rows.length);
  }, []);

  // чипы: только не-факторные фильтры (факторы видны в A/B-панели)
  const chipFilters = useMemo(() => {
    const out: FilterState = {};
    for (const [k, v] of Object.entries(filters)) if (!FACTOR_KEYS.has(k)) out[k] = v;
    return out;
  }, [filters]);

  function handleChipsChange(f: FilterState) {
    const next: FilterState = {};
    for (const [k, v] of Object.entries(filters)) if (FACTOR_KEYS.has(k)) next[k] = v;
    for (const [k, v] of Object.entries(f)) next[k] = v;
    setFilters(next);
    setPage(1);
  }

  const chipLabels = useMemo<Record<string, string>>(() => ({
    ticker: "ticker", status: "статус",
    entry_price: "цена вх.", exit_price: "цена вых.",
    pnl_rub: "PnL₽", pnl_pct: "PnL%",
  }), []);

  const chipValue = useCallback((key: string, v: FilterValue): string => {
    if (key === "status" && v.kind === "select") return statusLabel(v.value);
    const base = formatFilterValue(v);
    return CLIENT_KEYS.has(key) ? base + " (стр.)" : base;
  }, []);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const s = overview?.summary;
  const pnlPos = (s?.realized_pnl_rub ?? 0) >= 0;
  const agoSec = lastUpdated ? Math.max(0, Math.floor((now - lastUpdated.getTime()) / 1000)) : null;
  const activeFilterCount = Object.keys(filters).length;

  const metricCell = "flex flex-col items-end";
  const metricLabel = "text-[10px] uppercase tracking-[0.16em] text-slate-500";

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <style>{`
 @keyframes ptr-fade { from { opacity:0; transform: translateY(6px) } to { opacity:1; transform: translateY(0) } }
 @keyframes ptr-glow { 0%,100% { box-shadow: 0 0 10px rgba(52,211,153,.10) } 50% { box-shadow: 0 0 24px rgba(52,211,153,.25) } }
`}</style>

      {/* ===== HEADER: metrics left, strategy name + info right ===== */}
      <div className="relative rounded-lg border border-slate-800 bg-slate-900/60 px-5 py-4" style={{ animation: "ptr-fade .25s ease-out" }}>
        <div className="pointer-events-none absolute inset-0 rounded-lg" style={{
          backgroundImage: "radial-gradient(circle at 12% 0%, rgba(56,189,248,0.07), transparent 55%), radial-gradient(circle, rgba(148,163,184,0.05) 1px, transparent 1px)",
          backgroundSize: "auto, 20px 20px",
        }} />
        <div className="relative flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
          {/* metrics (left), numbers right-aligned in their cells */}
          <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
            <div className={metricCell}>
              <div className={metricLabel}>Реализованный PnL</div>
              <div
                className={"font-display text-4xl font-bold tabular-nums " + (pnlPos ? "text-emerald-400" : "text-rose-400")}
                style={pnlPos ? { animation: "ptr-glow 3s ease-in-out infinite" } : undefined}
              >
                {pnlPos ? "+" : ""}{fmtNum(s?.realized_pnl_rub, 0)} ₽
              </div>
            </div>
            <div className={metricCell}>
              <div className={metricLabel}>Win rate</div>
              <div className="font-display text-2xl font-bold tabular-nums text-slate-100">
                {s?.win_rate !== null && s?.win_rate !== undefined ? fmtNum(s.win_rate, 1) + "%" : "—"}
              </div>
              <div className="font-mono text-[10px]">
                <span className="text-emerald-400">{s?.wins ?? 0}W</span>
                <span className="text-slate-600"> · </span>
                <span className="text-rose-400">{s?.losses ?? 0}L</span>
              </div>
            </div>
            <div className={metricCell}>
              <div className={metricLabel}>Открыто</div>
              <div className="font-display text-2xl font-bold tabular-nums text-sky-300">{s?.open ?? 0}</div>
            </div>
            <div className={metricCell}>
              <div className={metricLabel}>Ожидают</div>
              <div className="font-display text-2xl font-bold tabular-nums text-amber-300">{s?.pending ?? 0}</div>
            </div>
            <div className={metricCell}>
              <div className={metricLabel}>Закрыто</div>
              <div className="font-display text-2xl font-bold tabular-nums text-slate-200">{s?.closed ?? 0}</div>
            </div>
          </div>

          {/* strategy name + badge + info (right) */}
          <div className="relative">
            <div className="flex items-center gap-2.5">
              <span className="relative flex h-2.5 w-2.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
                <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-400" />
              </span>
              <h2 className="font-display text-xl font-bold tracking-tight text-slate-100">
                {overview?.strategy_name ?? "…"}
              </h2>
              <span className="rounded border border-emerald-600/60 bg-emerald-500/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest text-emerald-300">
                paper trading
              </span>
              <button
                type="button"
                onMouseEnter={() => setInfoOpen(true)}
                onMouseLeave={() => setInfoOpen(false)}
                onClick={() => setInfoOpen((v) => !v)}
                aria-label="О стратегии"
                className="flex h-5 w-5 items-center justify-center rounded-full border border-slate-600 font-serif text-[11px] italic text-slate-400 transition hover:border-sky-500 hover:text-sky-300"
              >
                i
              </button>
            </div>
            {infoOpen && (
              <div
                className="absolute right-0 top-full z-30 mt-2 w-80 rounded-lg border border-slate-700 bg-slate-900 p-3 text-left shadow-2xl"
                style={{ animation: "ptr-fade .18s ease-out" }}
              >
                <div className="mb-1 text-[10px] uppercase tracking-[0.14em] text-slate-500">О стратегии</div>
                <p className="text-[11px] leading-relaxed text-slate-300">
                  {overview?.strategy_description ?? "Описание отсутствует."}
                </p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ===== FACTOR FILTERS (A/B arms) ===== */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/50 px-4 py-3" style={{ animation: "ptr-fade .3s ease-out" }}>
        <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
          {FACTOR_LABELS.map(({ key, label }) => {
            const options = overview?.factors?.[key] ?? [];
            const ff = filters[key];
            const active = ff && ff.kind === "select" ? ff.value : undefined;
            return (
              <div key={key} className="flex items-center gap-2">
                <span className="text-[10px] uppercase tracking-[0.14em] text-slate-500">{label}</span>
                <div className="flex flex-wrap gap-1">
                  <button onClick={() => setFactor(key, undefined)} className={chipCls(!active)}>все</button>
                  {options.map((v) => (
                    <button key={v} onClick={() => setFactor(key, v)} className={chipCls(active === v)}>{v}</button>
                  ))}
                </div>
              </div>
            );
          })}
          <div className="ml-auto flex items-center gap-3 text-[10px] text-slate-500">
            {activeFilterCount > 0 && (
              <button
                onClick={() => { setFilters({}); setPage(1); }}
                className="rounded border border-slate-700 px-2 py-0.5 text-slate-400 transition hover:border-rose-600/60 hover:text-rose-300"
              >
                сбросить ({activeFilterCount})
              </button>
            )}
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
              авто 30с
            </label>
            {agoSec !== null && (
              <span className="font-mono tabular-nums text-slate-600">обновлено {agoSec}с назад</span>
            )}
          </div>
        </div>
      </div>

      {error && (
        <div className="rounded border border-rose-700 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">Ошибка: {error}</div>
      )}

      {/* ===== CHART ===== */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4" style={{ animation: "ptr-fade .35s ease-out" }}>
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="font-display text-xs font-semibold uppercase tracking-[0.14em] text-slate-300">Накопленный PnL</span>
            <span className={"font-mono text-sm font-semibold tabular-nums " + (cumPnl >= 0 ? "text-emerald-400" : "text-rose-400")}>
              {cumPnl >= 0 ? "+" : ""}{fmtNum(cumPnl, 0)} ₽
            </span>
          </div>
          <div className="flex gap-1">
            {TF_OPTIONS.map((t) => (
              <button key={t.id} onClick={() => setTimeframe(t.id)}
                className={"rounded border px-2.5 py-1 font-mono text-[11px] transition-all duration-150 " +
                  (timeframe === t.id ? "border-sky-500/70 bg-sky-500/20 text-sky-200" : "border-slate-700 text-slate-400 hover:border-slate-500 hover:text-slate-200")}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <div ref={chartBoxRef} className={"w-full transition-opacity duration-300 " + (loading ? "opacity-60" : "opacity-100")} />
        {dynamics.length === 0 && !loading && (
          <div className="py-6 text-center text-xs text-slate-600">Нет закрытых позиций для выбранного фильтра</div>
        )}
      </div>

      {/* чипы фильтров таблицы (не-факторные) */}
      <FilterChips filters={chipFilters} onChange={handleChipsChange} labels={chipLabels} valueLabel={chipValue} className="shrink-0" />

      {/* ===== POSITIONS TABLE ===== */}
      <div className="flex min-h-0 flex-1 flex-col">
        <DataTable
          columns={columns}
          rows={positions}
          rowKey={(p) => p.id}
          filters={filters}
          onFiltersChange={handleTableFilters}
          sort={{ key: sortBy, dir: sortDir }}
          onSortChange={handleSort}
          onVisibleRowsChange={handleVisible}
          size="xs"
          title="Позиции"
          headerRight={
            <span className="font-mono text-[10px] text-slate-500">{total} всего · на странице {visibleCount}</span>
          }
          className="flex min-h-0 flex-1 flex-col rounded-b-none border-b-0"
          scrollClass="min-h-0 flex-1 overflow-auto"
          emptyText={loading ? "загрузка…" : "Нет позиций для выбранного фильтра"}
        />
        {/* pagination */}
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1 rounded-b-lg border border-slate-800 bg-slate-900 px-4 py-2">
          <button disabled={page <= 1 || loading} onClick={() => setPage(1)} className={pagBtn}>«</button>
          <button disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)} className={pagBtn}>‹</button>
          {pageWindow(page, totalPages).map((p) => (
            <button key={p} disabled={loading} onClick={() => setPage(p)}
              className={"rounded border px-2 py-1 " + (p === page ? "border-sky-500 bg-sky-500/20 text-sky-300" : "border-slate-700 hover:bg-slate-800")}>{p}</button>
          ))}
          <button disabled={page >= totalPages || loading} onClick={() => setPage((p) => p + 1)} className={pagBtn}>›</button>
          <button disabled={page >= totalPages || loading} onClick={() => setPage(totalPages)} className={pagBtn}>»</button>
          <span className="mx-1 text-slate-500">из {totalPages}</span>
          <input value={goto} onChange={(e) => setGoto(e.target.value.replace(/\D/g, ""))}
            onKeyDown={(e) => { if (e.key === "Enter") { const n = Number(goto); if (n >= 1 && n <= totalPages) setPage(n); setGoto(""); } }}
            placeholder="стр." className="w-16 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-center outline-none focus:border-sky-500" />
          <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 outline-none focus:border-sky-500">
            <option value={20}>20</option><option value={50}>50</option><option value={100}>100</option>
          </select>
        </div>
      </div>
    </div>
  );
}
