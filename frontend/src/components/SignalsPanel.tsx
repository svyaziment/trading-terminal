import { useCallback, useEffect, useMemo, useState } from "react";
import { getSignals } from "../api";
import type { Signal } from "../types";
import SignalDetailModal from "./SignalDetailModal";
import DataTable, {
  type ColumnDef,
  type FilterState,
  type FilterValue,
  type SortState,
  formatFilterValue,
} from "./ui/DataTable";
import FilterChips from "./ui/FilterChips";

/* ==================================================================
   SignalsPanel — таблица сигналов (task-004: миграция на DataTable)
   - серверные фильтры: timestamp (date), ticker (text), timeframe, signal
   - клиентские: figi, confidence, price, rsi, macd, bb_position,
     volume_ratio, atr_pct, total_signals, pattern_name (по странице)
   - сортировка серверная (controlled sort), пагинация снаружи DataTable
   ================================================================== */

type SortKey =
  | "timestamp" | "ticker" | "figi" | "timeframe" | "signal"
  | "confidence" | "price" | "rsi" | "macd" | "bb_position"
  | "volume_ratio" | "atr_pct" | "total_signals" | "pattern_name";

const PATTERN_NAMES = [
  "Trend_SMA_Alignment",
  "MR_RSI_Reversal",
  "BO_BB_Squeeze",
  "VOL_Spike",
  "VOL_Low_Pullback",
  "PA_Hammer",
  "PA_HangingMan",
  "PA_Engulfing",
  "PA_ThreeWhiteSoldiers",
  "PA_ThreeBlackCrows",
];

// серверные ключи фильтров; остальные — клиентские (по загруженной странице)
const SERVER_KEYS = new Set(["timestamp", "ticker", "timeframe", "signal"]);

const fmt = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d);

function Badge({ value }: { value: string }) {
  const c = value === "BUY" ? "bg-emerald-500/20 text-emerald-300"
    : value === "SELL" ? "bg-rose-500/20 text-rose-300" : "bg-slate-500/20 text-slate-300";
  return <span className={"rounded px-2 py-0.5 text-xs font-semibold " + c}>{value}</span>;
}

function pageWindow(cur: number, total: number): number[] {
  const out: number[] = [];
  const start = Math.max(1, cur - 2);
  const end = Math.min(total, cur + 2);
  for (let i = start; i <= end; i++) out.push(i);
  return out;
}

export default function SignalsPanel() {
  const [items, setItems] = useState<Signal[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Signal | null>(null);
  const [filters, setFilters] = useState<FilterState>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sortBy, setSortBy] = useState<SortKey>("timestamp");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [auto, setAuto] = useState(false);
  const [tick, setTick] = useState(0);
  const [goto, setGoto] = useState("");
  const [visibleCount, setVisibleCount] = useState(0);

  const selVal = (k: string): string | undefined => {
    const f = filters[k];
    return f && f.kind === "select" && f.value ? f.value : undefined;
  };
  const txtVal = (k: string): string | undefined => {
    const f = filters[k];
    return f && f.kind === "text" && f.value ? f.value.toUpperCase() : undefined;
  };
  const dateF = filters["timestamp"];
  const dateFrom = dateF && dateF.kind === "date" ? dateF.from || undefined : undefined;
  const dateTo = dateF && dateF.kind === "date" ? dateF.to || undefined : undefined;

  async function load() {
    setLoading(true); setError(null);
    try {
      const r = await getSignals({
        limit: pageSize, offset: (page - 1) * pageSize,
        ticker: txtVal("ticker"), timeframe: selVal("timeframe"), signal: selVal("signal"),
        date_from: dateFrom,
        date_to: dateTo ? `${dateTo} 23:59:59` : undefined,
        sort_by: sortBy, sort_dir: sortDir,
      });
      setItems(r.items); setTotal(r.total);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }

  useEffect(() => { void load(); }, [filters, page, pageSize, sortBy, sortDir, tick]);

  useEffect(() => {
    if (!auto) return;
    const id = window.setInterval(() => setTick((t) => t + 1), 30000);
    return () => window.clearInterval(id);
  }, [auto]);

  // Escape закрывает карточку сигнала
  useEffect(() => {
    if (!selected) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setSelected(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected]);

  const columns = useMemo<ColumnDef<Signal>[]>(() => [
    {
      key: "timestamp", label: "Timestamp",
      accessor: (s) => s.timestamp,
      render: (s) => <span className="whitespace-nowrap">{s.timestamp.replace("T", " ").slice(0, 19)}</span>,
      filter: { kind: "date" },
    },
    {
      key: "ticker", label: "Ticker",
      accessor: (s) => s.ticker,
      render: (s) => <span className="font-medium">{s.ticker}</span>,
      filter: { kind: "text", placeholder: "например VTBR" },
    },
    {
      key: "figi", label: "FIGI",
      accessor: (s) => s.figi ?? "",
      render: (s) => <span className="text-slate-400">{s.figi ?? "—"}</span>,
      filter: { kind: "text", placeholder: "подстрока (на странице)" },
    },
    {
      key: "timeframe", label: "TF",
      accessor: (s) => s.timeframe,
      filter: { kind: "select", options: ["30min", "1h", "4h", "1d"] },
    },
    {
      key: "signal", label: "Signal",
      accessor: (s) => s.signal,
      render: (s) => <Badge value={s.signal} />,
      filter: { kind: "select", options: ["BUY", "SELL"] },
    },
    {
      key: "confidence", label: "Conf", numeric: true,
      accessor: (s) => s.confidence,
      render: (s) => <span>{fmt(s.confidence)}</span>,
      filter: {
        kind: "range",
        slider: { min: 0, max: 1, step: 0.05 },
        presets: [{ label: "≥ 0.5", min: 0.5 }, { label: "≥ 0.8", min: 0.8 }],
      },
    },
    { key: "price", label: "Price", numeric: true, accessor: (s) => s.price, render: (s) => <span>{fmt(s.price, 4)}</span>, filter: { kind: "range" } },
    { key: "rsi", label: "RSI", numeric: true, accessor: (s) => s.rsi, render: (s) => <span>{fmt(s.rsi)}</span>, filter: { kind: "range", presets: [{ label: "Перепродан <30", max: 30 }, { label: "Перекуп >70", min: 70 }] } },
    { key: "macd", label: "MACD", numeric: true, accessor: (s) => s.macd, render: (s) => <span>{fmt(s.macd, 4)}</span>, filter: { kind: "range" } },
    { key: "bb_position", label: "BB%", numeric: true, accessor: (s) => s.bb_position, render: (s) => <span>{fmt(s.bb_position)}</span>, filter: { kind: "range" } },
    { key: "volume_ratio", label: "VolRatio", numeric: true, accessor: (s) => s.volume_ratio, render: (s) => <span>{fmt(s.volume_ratio)}</span>, filter: { kind: "range" } },
    { key: "atr_pct", label: "ATR%", numeric: true, accessor: (s) => s.atr_pct, render: (s) => <span>{fmt(s.atr_pct)}</span>, filter: { kind: "range" } },
    { key: "total_signals", label: "#Patterns", numeric: true, accessor: (s) => s.total_signals, render: (s) => <span>{s.total_signals ?? "—"}</span>, filter: { kind: "range" } },
    {
      key: "pattern_name", label: "Pattern",
      accessor: (s) => s.pattern_name ?? "",
      render: (s) => <span className="text-slate-300">{s.pattern_name ?? "—"}</span>,
      tdClass: "max-w-xs",
      filter: { kind: "select", options: PATTERN_NAMES, multi: true },
    },
    {
      key: "summary", label: "Summary", sortable: false,
      accessor: (s) => s.summary ?? "",
      render: (s) => <span className="text-slate-300">{s.summary ?? "—"}</span>,
      tdClass: "max-w-xl",
    },
  ], []);

  function handleFiltersChange(f: FilterState) {
    setFilters(f);
    setPage(1);
  }

  function handleSort(st: SortState) {
    // новое поле: timestamp -> desc, остальные -> asc (как до миграции)
    const dir = st.key === sortBy ? st.dir : st.key === "timestamp" ? "desc" : "asc";
    setSortBy(st.key as SortKey);
    setSortDir(dir);
    setPage(1);
  }

  const handleVisible = useCallback((rows: Signal[]) => {
    setVisibleCount(rows.length);
  }, []);

  const chipLabels = useMemo<Record<string, string>>(() => ({
    timestamp: "date", ticker: "ticker", timeframe: "tf", signal: "signal",
    figi: "figi", confidence: "confidence", price: "price", rsi: "rsi",
    macd: "macd", bb_position: "bb_position", volume_ratio: "volume_ratio",
    atr_pct: "atr_pct", total_signals: "total_signals", pattern_name: "pattern",
  }), []);

  const chipValue = useCallback((key: string, v: FilterValue): string => {
    const base = formatFilterValue(v);
    return SERVER_KEYS.has(key) ? base : base + " (стр.)";
  }, []);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const pagBtn = "rounded border border-slate-700 px-2 py-1 disabled:opacity-40 hover:bg-slate-800";

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {/* верхняя панель */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 text-sm text-slate-400">
        <div className="flex flex-wrap items-center gap-3">
          <div>Всего на сервере: {total}</div>
          <div>На странице: {visibleCount} из {items.length}</div>
          <button onClick={() => void load()} disabled={loading}
            className="rounded border border-sky-600 bg-sky-700 px-3 py-1.5 font-medium text-sky-50 transition hover:bg-sky-600 disabled:opacity-50">
            {loading ? "Загрузка..." : "Обновить"}
          </button>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Автообновление 30 сек
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <button disabled={page <= 1 || loading} onClick={() => setPage(1)} className={pagBtn}>«</button>
          <button disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)} className={pagBtn}>‹</button>
          {pageWindow(page, totalPages).map((p) => (
            <button key={p} disabled={loading} onClick={() => setPage(p)}
              className={"rounded border px-2 py-1 " + (p === page ? "border-sky-500 bg-sky-500/20 text-sky-300" : "border-slate-700 hover:bg-slate-800")}>{p}</button>
          ))}
          <button disabled={page >= totalPages || loading} onClick={() => setPage((p) => p + 1)} className={pagBtn}>›</button>
          <button disabled={page >= totalPages || loading} onClick={() => setPage(totalPages)} className={pagBtn}>»</button>
          <span className="mx-1">из {totalPages}</span>
          <input value={goto} onChange={(e) => setGoto(e.target.value.replace(/\D/g, ""))}
            onKeyDown={(e) => { if (e.key === "Enter") { const n = Number(goto); if (n >= 1 && n <= totalPages) setPage(n); setGoto(""); } }}
            placeholder="стр." className="w-16 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-center outline-none focus:border-sky-500" />
          <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 outline-none focus:border-sky-500">
            <option value={20}>20</option><option value={50}>50</option><option value={100}>100</option>
          </select>
        </div>
      </div>

      {/* чипы активных фильтров (серверные + клиентские) */}
      <FilterChips filters={filters} onChange={handleFiltersChange} labels={chipLabels} valueLabel={chipValue} />

      {error && (
        <div className="shrink-0 rounded border border-rose-700 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
          Ошибка: {error}
        </div>
      )}

      {/* таблица: скроллится только эта область, thead прилипает */}
      <DataTable
        columns={columns}
        rows={items}
        rowKey={(s) => s.id}
        filters={filters}
        onFiltersChange={handleFiltersChange}
        sort={{ key: sortBy, dir: sortDir }}
        onSortChange={handleSort}
        onVisibleRowsChange={handleVisible}
        onRowClick={(s) => setSelected(s)}
        className="flex min-h-0 flex-1 flex-col"
        scrollClass="min-h-0 flex-1 overflow-auto"
        emptyText={loading ? "загрузка…" : "нет сигналов для выбранного фильтра"}
      />

      {selected && <SignalDetailModal signal={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
