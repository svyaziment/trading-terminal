import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { getSignals } from "../api";
import type { Signal } from "../types";
import SignalDetailModal from "./SignalDetailModal";

type SortKey =
  | "timestamp" | "ticker" | "figi" | "timeframe" | "signal"
  | "confidence" | "price" | "rsi" | "macd" | "bb_position"
  | "volume_ratio" | "atr_pct" | "total_signals" | "pattern_name";
type SortDir = "asc" | "desc";
type Kind =
  | "server-text" | "server-select" | "server-date"
  | "client-text" | "client-select" | "client-number" | "client-confidence";

interface Col { key: string; label: string; numeric?: boolean; kind: Kind; options?: string[]; }

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

const COLUMNS: Col[] = [
  { key: "timestamp", label: "Timestamp", kind: "server-date" },
  { key: "ticker", label: "Ticker", kind: "server-text" },
  { key: "figi", label: "FIGI", kind: "client-text" },
  { key: "timeframe", label: "TF", kind: "server-select", options: ["30min", "1h", "4h", "1d"] },
  { key: "signal", label: "Signal", kind: "server-select", options: ["BUY", "SELL"] },
  { key: "confidence", label: "Conf", numeric: true, kind: "client-confidence" },
  { key: "price", label: "Price", numeric: true, kind: "client-number" },
  { key: "rsi", label: "RSI", numeric: true, kind: "client-number" },
  { key: "macd", label: "MACD", numeric: true, kind: "client-number" },
  { key: "bb_position", label: "BB%", numeric: true, kind: "client-number" },
  { key: "volume_ratio", label: "VolRatio", numeric: true, kind: "client-number" },
  { key: "atr_pct", label: "ATR%", numeric: true, kind: "client-number" },
  { key: "total_signals", label: "#Patterns", numeric: true, kind: "client-number" },
  { key: "pattern_name", label: "Pattern", kind: "client-select", options: PATTERN_NAMES },
];

const fmt = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d);

function Badge({ value }: { value: string }) {
  const c = value === "BUY" ? "bg-emerald-500/20 text-emerald-300"
    : value === "SELL" ? "bg-rose-500/20 text-rose-300" : "bg-slate-500/20 text-slate-300";
  return <span className={"rounded px-2 py-0.5 text-xs font-semibold " + c}>{value}</span>;
}
function Funnel({ active }: { active: boolean }) {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
      stroke={active ? "#38bdf8" : "currentColor"} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
      <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
    </svg>
  );
}

interface ServerF { ticker: string; timeframe: string; signal: string; dateFrom: string; dateTo: string; }
type ClientF = Record<string, { text?: string; select?: string; min?: number; max?: number }>;
const EMPTY_SERVER: ServerF = { ticker: "", timeframe: "", signal: "", dateFrom: "", dateTo: "" };
const inputCls = "w-full rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm outline-none focus:border-sky-500";
const chipBtn = "rounded px-2 py-1 text-sm text-slate-300 border border-slate-700 hover:bg-slate-800";
const chipBtnOn = "rounded px-2 py-1 text-sm bg-sky-500/30 text-sky-200";

interface Draft {
  text?: string;
  select?: string;
  min?: number | "";
  max?: number | "";
  conf?: number;
  serverText?: string;
  serverSelect?: string;
  dateFrom?: string;
  dateTo?: string;
}

function initialDraft(col: Col, server: ServerF, client: ClientF): Draft {
  const c = client[col.key] ?? {};
  switch (col.kind) {
    case "server-text": return { serverText: (server[col.key as keyof ServerF] as string) ?? "" };
    case "server-select": return { serverSelect: (server[col.key as keyof ServerF] as string) ?? "" };
    case "server-date": return { dateFrom: server.dateFrom ?? "", dateTo: server.dateTo ?? "" };
    case "client-text": return { text: c.text ?? "" };
    case "client-select": return { select: c.select ?? "" };
    case "client-confidence": return { conf: c.min ?? 0 };
    case "client-number": return { min: c.min ?? "", max: c.max ?? "" };
    default: return {};
  }
}

function emptyDraft(col: Col): Draft {
  switch (col.kind) {
    case "server-text": return { serverText: "" };
    case "server-select": return { serverSelect: "" };
    case "server-date": return { dateFrom: "", dateTo: "" };
    case "client-text": return { text: "" };
    case "client-select": return { select: "" };
    case "client-confidence": return { conf: 0 };
    case "client-number": return { min: "", max: "" };
    default: return {};
  }
}

function FilterPopover({
  col,
  draft,
  setDraft,
  onApply,
  onReset,
  onClose,
}: {
  col: Col;
  draft: Draft;
  setDraft: (d: Draft) => void;
  onApply: () => void;
  onReset: () => void;
  onClose: () => void;
}) {
  return (
    <div className="rounded border border-slate-700 bg-slate-900 p-3 shadow-xl" style={{ width: 280 }}>
      <div className="mb-2 text-xs font-semibold text-slate-300">Фильтр: {col.label}</div>

      {col.kind === "server-text" && (
        <input
          autoFocus
          className={inputCls}
          placeholder="например VTBR"
          value={draft.serverText ?? ""}
          onChange={(e) => setDraft({ ...draft, serverText: e.target.value })}
          onKeyDown={(e) => { if (e.key === "Enter") onApply(); }}
        />
      )}

      {col.kind === "server-select" && (
        <div className={col.key === "timeframe" ? "grid grid-cols-2 gap-2" : "flex gap-2"}>
          {["", ...(col.options ?? [])].map((v) => (
            <button
              key={v || "all"}
              onClick={() => setDraft({ ...draft, serverSelect: v })}
              className={draft.serverSelect === v ? chipBtnOn : chipBtn}
            >
              {v || "Все"}
            </button>
          ))}
        </div>
      )}

      {col.kind === "server-date" && (
        <div className="space-y-2">
          <input type="date" className={inputCls} value={draft.dateFrom ?? ""}
            onChange={(e) => setDraft({ ...draft, dateFrom: e.target.value })} />
          <input type="date" className={inputCls} value={draft.dateTo ?? ""}
            onChange={(e) => setDraft({ ...draft, dateTo: e.target.value })} />
        </div>
      )}

      {col.kind === "client-text" && (
        <input
          autoFocus
          className={inputCls}
          placeholder="подстрока (на странице)"
          value={draft.text ?? ""}
          onChange={(e) => setDraft({ ...draft, text: e.target.value })}
          onKeyDown={(e) => { if (e.key === "Enter") onApply(); }}
        />
      )}

      {col.kind === "client-select" && (
        <div className="max-h-64 space-y-1 overflow-y-auto pr-1">
          <button onClick={() => setDraft({ ...draft, select: "" })}
            className={!draft.select ? chipBtnOn + " w-full text-left" : chipBtn + " w-full text-left"}>
            Все
          </button>
          {(col.options ?? []).map((v) => (
            <button key={v} onClick={() => setDraft({ ...draft, select: v })}
              className={(draft.select === v ? chipBtnOn : chipBtn) + " w-full text-left"}>
              {v}
            </button>
          ))}
        </div>
      )}

      {col.key === "rsi" && (
        <div className="space-y-2">
          <div className="flex gap-2">
            <button onClick={() => setDraft({ ...draft, min: "", max: 30 })} className="flex-1 rounded border border-emerald-700/60 px-2 py-1 text-xs text-emerald-300 hover:bg-emerald-500/10">Перепродан &lt;30</button>
            <button onClick={() => setDraft({ ...draft, min: 70, max: "" })} className="flex-1 rounded border border-rose-700/60 px-2 py-1 text-xs text-rose-300 hover:bg-rose-500/10">Перекуп &gt;70</button>
          </div>
          <div className="flex gap-2">
            <input type="number" step="any" className={inputCls} placeholder="min" value={draft.min ?? ""}
              onChange={(e) => setDraft({ ...draft, min: e.target.value === "" ? "" : Number(e.target.value) })} />
            <input type="number" step="any" className={inputCls} placeholder="max" value={draft.max ?? ""}
              onChange={(e) => setDraft({ ...draft, max: e.target.value === "" ? "" : Number(e.target.value) })} />
          </div>
        </div>
      )}

      {col.kind === "client-confidence" && (
        <div className="space-y-2">
          <div className="text-xs text-slate-400">Минимум confidence: {Number(draft.conf ?? 0).toFixed(2)}</div>
          <input type="range" min={0} max={1} step={0.05} className="w-full" value={draft.conf ?? 0}
            onChange={(e) => setDraft({ ...draft, conf: Number(e.target.value) })} />
        </div>
      )}

      {col.kind === "client-number" && col.key !== "rsi" && (
        <div className="flex gap-2">
          <input type="number" step="any" className={inputCls} placeholder="min" value={draft.min ?? ""}
            onChange={(e) => setDraft({ ...draft, min: e.target.value === "" ? "" : Number(e.target.value) })} />
          <input type="number" step="any" className={inputCls} placeholder="max" value={draft.max ?? ""}
            onChange={(e) => setDraft({ ...draft, max: e.target.value === "" ? "" : Number(e.target.value) })} />
        </div>
      )}

      <div className="mt-2 text-[11px] text-slate-500">
        {col.kind.startsWith("client") ? "Работает по загруженной странице." : "Работает по всей базе."}
      </div>
      <div className="mt-2 flex gap-2">
        <button onClick={onReset} className="flex-1 rounded border border-rose-700/60 py-1 text-rose-300 hover:bg-rose-500/10">Сброс</button>
        <button onClick={onApply} className="flex-1 rounded bg-sky-700 py-1 font-medium text-sky-50 transition hover:bg-sky-600">Применить</button>
      </div>
      <button onClick={onClose} className="mt-2 w-full text-center text-[11px] text-slate-500 hover:text-slate-300">закрыть без применения</button>
    </div>
  );
}

export default function SignalsPanel() {
  const [items, setItems] = useState<Signal[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Signal | null>(null);

  const [server, setServer] = useState<ServerF>(EMPTY_SERVER);
  const [client, setClient] = useState<ClientF>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sortBy, setSortBy] = useState<SortKey>("timestamp");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [auto, setAuto] = useState(false);
  const [tick, setTick] = useState(0);
  const [goto, setGoto] = useState("");

  const [openCol, setOpenCol] = useState<string | null>(null);
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const popRef = useRef<HTMLDivElement | null>(null);
  const btnRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  async function load() {
    setLoading(true); setError(null);
    try {
      const r = await getSignals({
        limit: pageSize, offset: (page - 1) * pageSize,
        ticker: server.ticker || undefined, timeframe: server.timeframe || undefined,
        signal: server.signal || undefined,
        date_from: server.dateFrom || undefined,
        date_to: server.dateTo ? `${server.dateTo} 23:59:59` : undefined,
        sort_by: sortBy, sort_dir: sortDir,
      });
      setItems(r.items); setTotal(r.total);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }

  useEffect(() => { void load(); }, [server, page, pageSize, sortBy, sortDir, tick]);

  useEffect(() => {
    if (!auto) return;
    const id = window.setInterval(() => setTick((t) => t + 1), 30000);
    return () => window.clearInterval(id);
  }, [auto]);

  // Закрытие по клику вне окна и по Escape. Скролл НЕ закрывает.
  useEffect(() => {
    if (!openCol) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (popRef.current?.contains(t)) return;
      if (btnRefs.current[openCol]?.contains(t)) return;
      setOpenCol(null);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpenCol(null); };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [openCol]);

  // Скролл/ресайз только пересчитывают позицию окна (оно «прилипает» к кнопке).
  useEffect(() => {
    if (!openCol) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      const el = btnRefs.current[openCol];
      if (!el) return;
      const r = el.getBoundingClientRect();
      setAnchor((prev) => {
        if (prev &&
          Math.abs(prev.top - r.top) < 0.5 &&
          Math.abs(prev.left - r.left) < 0.5 &&
          Math.abs(prev.bottom - r.bottom) < 0.5) return prev;
        return r;
      });
    };
    const onScrollOrResize = () => { if (!raf) raf = requestAnimationFrame(update); };
    window.addEventListener("scroll", onScrollOrResize, true);
    window.addEventListener("resize", onScrollOrResize);
    return () => {
      window.removeEventListener("scroll", onScrollOrResize, true);
      window.removeEventListener("resize", onScrollOrResize);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [openCol]);

  // Escape закрывает карточку сигнала (но не мешает открытому popover).
  useEffect(() => {
    if (!selected) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !openCol) setSelected(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, openCol]);

  const displayed = useMemo(() => items.filter((row) => {
    for (const [key, f] of Object.entries(client)) {
      const raw = (row as unknown as Record<string, unknown>)[key];
      if (f.text && !String(raw ?? "").toLowerCase().includes(f.text.toLowerCase())) return false;
      if (f.select && !splitPatterns(raw).includes(f.select)) return false;
      if (f.min !== undefined || f.max !== undefined) {
        const n = Number(raw);
        if (raw === null || raw === undefined || Number.isNaN(n)) return false;
        if (f.min !== undefined && n < f.min) return false;
        if (f.max !== undefined && n > f.max) return false;
      }
    }
    return true;
  }), [items, client]);

  function toggleSort(key: SortKey) {
    if (sortBy === key) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortBy(key); setSortDir(key === "timestamp" ? "desc" : "asc"); }
    setPage(1);
  }
  function openFilter(key: string) {
    const col = COLUMNS.find((c) => c.key === key);
    const el = btnRefs.current[key];
    if (!col || !el) return;
    setAnchor(el.getBoundingClientRect());
    setDraft(initialDraft(col, server, client));
    setOpenCol((c) => (c === key ? null : key));
  }
  function setServerField(key: string, value: string) { setServer((s) => ({ ...s, [key]: value })); setPage(1); }
  function setClientText(key: string, value: string) { setClient((c) => ({ ...c, [key]: { ...c[key], text: value || undefined } })); }
  function setClientSelect(key: string, value: string | undefined) { setClient((c) => ({ ...c, [key]: { ...c[key], select: value || undefined } })); }
  function setClientRange(key: string, min?: number, max?: number) { setClient((c) => ({ ...c, [key]: { min, max } })); }

  function applyDraft(col: Col, d: Draft) {
    switch (col.kind) {
      case "server-text": setServerField(col.key, (d.serverText ?? "").toUpperCase()); break;
      case "server-select": setServerField(col.key, d.serverSelect ?? ""); break;
      case "server-date": setServer((s) => ({ ...s, dateFrom: d.dateFrom ?? "", dateTo: d.dateTo ?? "" })); setPage(1); break;
      case "client-text": setClientText(col.key, d.text ?? ""); break;
      case "client-select": setClientSelect(col.key, d.select || undefined); break;
      case "client-confidence": setClientRange(col.key, Number(d.conf ?? 0), undefined); break;
      case "client-number": {
        const min = d.min === "" || d.min === undefined || d.min === null ? undefined : Number(d.min);
        const max = d.max === "" || d.max === undefined || d.max === null ? undefined : Number(d.max);
        setClientRange(col.key, min, max);
        break;
      }
    }
  }
  function resetFilter(col: Col) {
    if (col.kind.startsWith("server")) {
      if (col.kind === "server-date") setServer((s) => ({ ...s, dateFrom: "", dateTo: "" }));
      else setServerField(col.key, "");
      setPage(1);
    } else {
      setClient((c) => { const n = { ...c }; delete n[col.key]; return n; });
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const activeCol = COLUMNS.find((c) => c.key === openCol);

  const chips: Array<{ label: string; clear: () => void }> = [];
  if (server.ticker) chips.push({ label: `ticker=${server.ticker}`, clear: () => setServerField("ticker", "") });
  if (server.timeframe) chips.push({ label: `tf=${server.timeframe}`, clear: () => setServerField("timeframe", "") });
  if (server.signal) chips.push({ label: `signal=${server.signal}`, clear: () => setServerField("signal", "") });
  if (server.dateFrom || server.dateTo) chips.push({ label: `date ${server.dateFrom || "…"}..${server.dateTo || "…"}`, clear: () => { setServer((s) => ({ ...s, dateFrom: "", dateTo: "" })); setPage(1); } });
  for (const [k, f] of Object.entries(client)) {
    const v = f.text ?? f.select ?? `min=${f.min ?? "∞"},max=${f.max ?? "∞"}`;
    chips.push({ label: `${k}=${v} (стр.)`, clear: () => setClient((c) => { const n = { ...c }; delete n[k]; return n; }) });
  }

  const pagBtn = "rounded border border-slate-700 px-2 py-1 disabled:opacity-40 hover:bg-slate-800";
  // Класс для «замороженного» заголовка колонки.
  const thSticky = "sticky top-0 z-10 border-b border-slate-800 bg-slate-900 px-2 py-2";

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {/* Верхняя панель: не скроллится */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 text-sm text-slate-400">
        <div className="flex flex-wrap items-center gap-3">
          <div>Всего на сервере: {total}</div>
          <div>На странице: {displayed.length} из {items.length}</div>
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

      {/* Чипы активных фильтров: не скроллятся */}
      {chips.length > 0 && (
        <div className="flex shrink-0 flex-wrap gap-2">
          {chips.map((c, i) => (
            <button key={i} onClick={c.clear}
              className="flex items-center gap-1 rounded-full border border-sky-700/60 bg-sky-500/10 px-2 py-0.5 text-xs text-sky-300 hover:bg-sky-500/20">
              {c.label} <span className="text-sky-400">×</span>
            </button>
          ))}
          <button onClick={() => { setServer(EMPTY_SERVER); setClient({}); setPage(1); }}
            className="rounded-full border border-slate-700 px-2 py-0.5 text-xs text-slate-400 hover:bg-slate-800">Сбросить всё</button>
        </div>
      )}

      {/* Ошибка: не скроллится */}
      {error && (
        <div className="shrink-0 rounded border border-rose-700 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
          Ошибка: {error}
        </div>
      )}

      {/* Таблица: скроллится ТОЛЬКО эта область; thead прилипает сверху */}
      <div className="min-h-0 flex-1 overflow-auto rounded border border-slate-800">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              {COLUMNS.map((col) => {
                const isSorted = sortBy === col.key;
                const isActive = !!server[col.key as keyof ServerF] || !!client[col.key];
                return (
                  <th key={col.key} className={thSticky + " select-none " + (col.numeric ? "text-right" : "text-left")}>
                    <span className="inline-flex items-center gap-1">
                      <span className="cursor-pointer hover:text-slate-200" onClick={() => toggleSort(col.key as SortKey)}>
                        {col.label}{isSorted ? (sortDir === "asc" ? " ↑" : " ↓") : ""}
                      </span>
                      <button ref={(el) => { btnRefs.current[col.key] = el; }}
                        onClick={(e) => { e.stopPropagation(); openFilter(col.key); }}
                        title={"Фильтр по " + col.label}
                        className={"rounded p-0.5 hover:bg-slate-700 " + (isActive || openCol === col.key ? "text-sky-300" : "text-slate-500")}>
                        <Funnel active={isActive || openCol === col.key} />
                      </button>
                    </span>
                  </th>
                );
              })}
              <th className={thSticky + " text-left"}>Summary</th>
            </tr>
          </thead>
          <tbody>
            {displayed.map((item) => (
              <tr key={item.id} onClick={() => setSelected(item)} className="cursor-pointer border-t border-slate-800 hover:bg-slate-900/60">
                <td className="whitespace-nowrap px-2 py-1">{item.timestamp.replace("T", " ").slice(0, 19)}</td>
                <td className="px-2 py-1 font-medium">{item.ticker}</td>
                <td className="px-2 py-1 text-slate-400">{item.figi ?? "—"}</td>
                <td className="px-2 py-1">{item.timeframe}</td>
                <td className="px-2 py-1"><Badge value={item.signal} /></td>
                <td className="px-2 py-1 text-right">{fmt(item.confidence)}</td>
                <td className="px-2 py-1 text-right">{fmt(item.price, 4)}</td>
                <td className="px-2 py-1 text-right">{fmt(item.rsi)}</td>
                <td className="px-2 py-1 text-right">{fmt(item.macd, 4)}</td>
                <td className="px-2 py-1 text-right">{fmt(item.bb_position)}</td>
                <td className="px-2 py-1 text-right">{fmt(item.volume_ratio)}</td>
                <td className="px-2 py-1 text-right">{fmt(item.atr_pct)}</td>
                <td className="px-2 py-1 text-right">{item.total_signals ?? "—"}</td>
                <td className="max-w-xs px-2 py-1 text-slate-300">{item.pattern_name ?? "—"}</td>
                <td className="max-w-xl px-2 py-1 text-slate-300">{item.summary ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {openCol && activeCol && anchor && createPortal(
        <div
          ref={popRef}
          style={{
            position: "fixed",
            top: anchor.bottom + 4,
            left: Math.min(Math.max(anchor.left, 8), window.innerWidth - 288),
            zIndex: 50,
          }}
        >
          <FilterPopover
            col={activeCol}
            draft={draft}
            setDraft={setDraft}
            onApply={() => { applyDraft(activeCol, draft); setOpenCol(null); }}
            onReset={() => { resetFilter(activeCol); setDraft(emptyDraft(activeCol)); }}
            onClose={() => setOpenCol(null)}
          />
        </div>,
        document.body,
      )}

      {selected && <SignalDetailModal signal={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function splitPatterns(raw: unknown): string[] {
  return String(raw ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function pageWindow(cur: number, total: number): number[] {
  const out: number[] = [];
  const start = Math.max(1, cur - 2);
  const end = Math.min(total, cur + 2);
  for (let i = start; i <= end; i++) out.push(i);
  return out;
}