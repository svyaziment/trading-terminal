import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import DatePicker from "./DatePicker";

/* ==================================================================
   DataTable — единая таблица проекта (task-002)
   - сортировка по любому полю (клик по заголовку)
   - funnel-фильтры в заголовках: select / range (с пресетами) / text
   - управляемые фильтры: одно состояние можно шарить между таблицами
   - managed-режим для server-side страниц (Signals, Paper Trading)
   ================================================================== */

export type SortDir = "asc" | "desc";
export interface SortState { key: string; dir: SortDir }

export type FilterValue =
  | { kind: "select"; value: string }
  | { kind: "range"; min?: number; max?: number }
  | { kind: "text"; value: string }
  | { kind: "date"; from?: string; to?: string };

export type FilterState = Record<string, FilterValue>;

export interface FilterPreset { label: string; min?: number; max?: number }

export interface ColumnFilterDef {
  kind: "select" | "range" | "text" | "date";
  options?: string[];                    // select: явный список; иначе вывод из строк
  optionLabel?: (v: string) => string;   // select: отображение значения ("take" -> "тейк")
  multi?: boolean;                       // select: значение — список через запятую (вхождение)
  presets?: FilterPreset[];              // range: быстрые кнопки
  slider?: { min: number; max: number; step: number };  // range: слайдер (confidence и т.п.)
  placeholder?: string;                  // text
}

export interface ColumnDef<T> {
  key: string;
  label: string;
  numeric?: boolean;                     // правое выравнивание + mono/tabular-nums
  sortable?: boolean;                    // по умолчанию true
  accessor: (row: T) => string | number | null | undefined;  // сорт + фильтр + CSV
  render?: (row: T) => React.ReactNode;  // кастомная ячейка
  filter?: ColumnFilterDef;
  tdClass?: string;                      // доп. класс ячейки (max-w-* и т.п.)
}

export function formatFilterValue(v: FilterValue): string {
  if (v.kind === "select") return v.value;
  if (v.kind === "text") return "«" + v.value + "»";
  if (v.kind === "date") return (v.from || "…") + "…" + (v.to || "…");
  const mn = v.min === undefined ? "−∞" : String(v.min);
  const mx = v.max === undefined ? "+∞" : String(v.max);
  return mn + "…" + mx;
}

export function isFilterActive(v: FilterValue | undefined): boolean {
  if (!v) return false;
  if (v.kind === "range") return v.min !== undefined || v.max !== undefined;
  if (v.kind === "date") return v.from !== undefined || v.to !== undefined;
  return v.value !== "";
}

/* ---------- чистые функции фильтрации / сортировки ---------- */

function rowMatches<T>(row: T, columns: Array<ColumnDef<T>>, filters: FilterState): boolean {
  for (const key of Object.keys(filters)) {
    const col = columns.find((c) => c.key === key);
    const f = filters[key];
    if (!col || !col.filter || !isFilterActive(f)) continue;  // фильтр не от этой таблицы
    const raw = col.accessor(row);
    if (f.kind === "select") {
      const s = String(raw ?? "");
      if (col.filter.multi) {
        if (!s.split(",").map((x) => x.trim()).includes(f.value)) return false;
      } else if (s !== f.value) return false;
    } else if (f.kind === "text") {
      if (!String(raw ?? "").toLowerCase().includes(f.value.toLowerCase())) return false;
    } else if (f.kind === "date") {
      const day = String(raw ?? "").slice(0, 10);
      if (f.from && day < f.from) return false;
      if (f.to && day > f.to) return false;
    } else {
      if (raw === null || raw === undefined) return false;
      const n = typeof raw === "number" ? raw : Number(raw);
      if (Number.isNaN(n)) return false;
      if (f.min !== undefined && n < f.min) return false;
      if (f.max !== undefined && n > f.max) return false;
    }
  }
  return true;
}

export function applyFilters<T>(rows: T[], columns: Array<ColumnDef<T>>, filters: FilterState): T[] {
  const active = Object.keys(filters).filter((k) => isFilterActive(filters[k]));
  if (active.length === 0) return rows;
  return rows.filter((r) => rowMatches(r, columns, filters));
}

export function applySort<T>(rows: T[], columns: Array<ColumnDef<T>>, sort: SortState | null): T[] {
  if (!sort) return rows;
  const col = columns.find((c) => c.key === sort.key);
  if (!col) return rows;
  const dir = sort.dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const av = col.accessor(a);
    const bv = col.accessor(b);
    if (av === bv) return 0;
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
    return (String(av) < String(bv) ? -1 : 1) * dir;
  });
}

/* ---------- popover фильтра ---------- */

interface Draft { select?: string; text?: string; min?: string; max?: string; from?: string; to?: string }

function FilterPopover<T>(props: {
  col: ColumnDef<T>;
  draft: Draft;
  setDraft: (d: Draft) => void;
  options: string[];
  onApply: () => void;
  onReset: () => void;
  onClose: () => void;
}) {
  const { col, draft, setDraft, options, onApply, onReset, onClose } = props;
  const f = col.filter;
  if (!f) return null;
  const inputCls =
    "w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-[11px] text-slate-200 outline-none transition focus:border-sky-500";
  const optBtn = (on: boolean) =>
    "block w-full rounded px-2 py-1 text-left font-mono text-[11px] transition-colors " +
    (on ? "bg-sky-500/25 text-sky-200" : "text-slate-300 hover:bg-slate-800");
  return (
    <div className="w-64 rounded border border-slate-700 bg-slate-900 p-3 shadow-xl" style={{ animation: "dt-fade .18s ease-out" }}>
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">Фильтр: {col.label}</div>
      {f.kind === "select" && (
        <div className="max-h-64 space-y-0.5 overflow-y-auto pr-1">
          <button type="button" onClick={() => setDraft({ ...draft, select: "" })} className={optBtn(!draft.select)}>все</button>
          {options.map((v) => (
            <button key={v} type="button" onClick={() => setDraft({ ...draft, select: v })} className={optBtn(draft.select === v)}>
              {f.optionLabel ? f.optionLabel(v) : v}
            </button>
          ))}
        </div>
      )}
      {f.kind === "text" && (
        <input
          autoFocus
          className={inputCls}
          placeholder={f.placeholder ?? "подстрока"}
          value={draft.text ?? ""}
          onChange={(e) => setDraft({ ...draft, text: e.target.value })}
          onKeyDown={(e) => { if (e.key === "Enter") onApply(); }}
        />
      )}
      {f.kind === "date" && (
        <div className="space-y-2">
          <label className="block">
            <span className="mb-1 block text-[9px] uppercase tracking-wider text-slate-500">с</span>
            <DatePicker
              value={draft.from ?? ""}
              onChange={(value) => setDraft({ ...draft, from: value })}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-[9px] uppercase tracking-wider text-slate-500">до</span>
            <DatePicker
              value={draft.to ?? ""}
              onChange={(value) => setDraft({ ...draft, to: value })}
            />
          </label>
        </div>
      )}
      {f.kind === "range" && (
        <div>
          {f.presets && f.presets.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {f.presets.map((p) => {
                const pMin = p.min === undefined ? "" : String(p.min);
                const pMax = p.max === undefined ? "" : String(p.max);
                const on = (draft.min ?? "") === pMin && (draft.max ?? "") === pMax;
                return (
                  <button
                    key={p.label}
                    type="button"
                    onClick={() => setDraft({ ...draft, min: pMin, max: pMax })}
                    className={
                      "rounded border px-2 py-0.5 font-mono text-[10px] transition-all duration-150 active:scale-95 " +
                      (on
                        ? "border-sky-500/70 bg-sky-500/20 text-sky-200"
                        : "border-slate-700 text-slate-400 hover:border-sky-600/60 hover:text-sky-300")
                    }
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
          )}
          {f.slider && (
            <div className="mb-2">
              <div className="mb-1 flex justify-between font-mono text-[10px] text-slate-400">
                <span>min</span>
                <span className="text-sky-300">{draft.min === "" || draft.min === undefined ? "—" : draft.min}</span>
              </div>
              <input type="range" min={f.slider.min} max={f.slider.max} step={f.slider.step} className="w-full accent-sky-500"
                value={draft.min === "" || draft.min === undefined ? f.slider.min : Number(draft.min)}
                onChange={(e) => setDraft({ ...draft, min: e.target.value })} />
            </div>
          )}
          <div className="flex gap-2">
            <input type="number" step="any" className={inputCls} placeholder="min" value={draft.min ?? ""}
              onChange={(e) => setDraft({ ...draft, min: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter") onApply(); }} />
            <input type="number" step="any" className={inputCls} placeholder="max" value={draft.max ?? ""}
              onChange={(e) => setDraft({ ...draft, max: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter") onApply(); }} />
          </div>
        </div>
      )}
      <div className="mt-2.5 flex gap-2">
        <button type="button" onClick={onReset} className="flex-1 rounded border border-rose-700/60 py-1 text-[11px] text-rose-300 transition hover:bg-rose-500/10">Сброс</button>
        <button type="button" onClick={onApply} className="flex-1 rounded bg-sky-700 py-1 text-[11px] font-medium text-sky-50 transition hover:bg-sky-600">Применить</button>
      </div>
      <button type="button" onClick={onClose} className="mt-2 w-full text-center text-[10px] text-slate-500 transition hover:text-slate-300">закрыть без применения</button>
    </div>
  );
}

/* ---------- DataTable ---------- */

export interface DataTableProps<T> {
  columns: Array<ColumnDef<T>>;
  rows: T[];
  rowKey: (row: T, index: number) => string | number;
  /** Управляемые фильтры (можно шарить между несколькими таблицами) */
  filters?: FilterState;
  onFiltersChange?: (f: FilterState) => void;
  /** true = строки уже отфильтрованы/отсортированы (server-side), внутренняя логика выключена */
  managed?: boolean;
  sort?: SortState | null;
  onSortChange?: (s: SortState) => void;
  defaultSort?: SortState;
  title?: React.ReactNode;
  headerRight?: React.ReactNode;
  csv?: { filename: string };
  emptyText?: string;
  scrollClass?: string;
  rowClass?: (row: T) => string;
  onRowClick?: (row: T) => void;
  size?: "sm" | "xs";
  onVisibleRowsChange?: (rows: T[]) => void;
  className?: string;
}

export default function DataTable<T>(props: DataTableProps<T>) {
  const {
    columns, rows, rowKey, filters, onFiltersChange, managed,
    sort, onSortChange, defaultSort, title, headerRight, csv,
    emptyText, scrollClass, rowClass, onRowClick, size,
    onVisibleRowsChange, className,
  } = props;

  const [internalSort, setInternalSort] = useState<SortState | null>(defaultSort ?? null);
  const effectiveSort = onSortChange || sort !== undefined ? sort ?? null : internalSort;

  const [openKey, setOpenKey] = useState<string | null>(null);
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const btnRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const popRef = useRef<HTMLDivElement | null>(null);

  const sortExternal = onSortChange !== undefined || sort !== undefined;
  const visibleRows = useMemo(() => {
    const filtered = managed ? rows : applyFilters(rows, columns, filters ?? {});
    return sortExternal ? filtered : applySort(filtered, columns, effectiveSort);
  }, [managed, sortExternal, rows, columns, filters, effectiveSort]);

  useEffect(() => { onVisibleRowsChange?.(visibleRows); }, [visibleRows, onVisibleRowsChange]);

  // опции select из данных, если не заданы явно
  const derivedOptions = useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const c of columns) {
      if (c.filter?.kind === "select" && !c.filter.options) {
        const seen = new Set<string>();
        const out: string[] = [];
        for (const r of rows) {
          const v = c.accessor(r);
          if (v === null || v === undefined) continue;
          const s = String(v);
          if (!seen.has(s)) { seen.add(s); out.push(s); }
        }
        map[c.key] = out;
      }
    }
    return map;
  }, [columns, rows]);

  function toggleSort(key: string) {
    const cur = effectiveSort;
    const next: SortState =
      cur && cur.key === key
        ? { key, dir: cur.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "desc" };
    if (onSortChange) onSortChange(next);
    else setInternalSort(next);
  }

  function commitFilter(key: string, v: FilterValue | null) {
    if (!onFiltersChange) return;
    const next: FilterState = { ...(filters ?? {}) };
    if (v === null) delete next[key];
    else next[key] = v;
    onFiltersChange(next);
  }

  function openFilter(key: string) {
    const col = columns.find((c) => c.key === key);
    const el = btnRefs.current[key];
    if (!col || !el || !col.filter) return;
    setAnchor(el.getBoundingClientRect());
    const f = filters?.[key];
    if (col.filter.kind === "select") {
      setDraft({ select: f && f.kind === "select" ? f.value : "" });
    } else if (col.filter.kind === "text") {
      setDraft({ text: f && f.kind === "text" ? f.value : "" });
    } else if (col.filter.kind === "date") {
      setDraft({
        from: f && f.kind === "date" && f.from ? f.from : "",
        to: f && f.kind === "date" && f.to ? f.to : "",
      });
    } else {
      setDraft({
        min: f && f.kind === "range" && f.min !== undefined ? String(f.min) : "",
        max: f && f.kind === "range" && f.max !== undefined ? String(f.max) : "",
      });
    }
    setOpenKey((k) => (k === key ? null : key));
  }

  // закрытие поповера: клик вне + Escape
  useEffect(() => {
    if (!openKey) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (popRef.current?.contains(t)) return;
      if (btnRefs.current[openKey]?.contains(t)) return;
      setOpenKey(null);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpenKey(null); };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [openKey]);

  // поповер «прилипает» к кнопке при скролле/ресайзе
  useEffect(() => {
    if (!openKey) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      const el = btnRefs.current[openKey];
      if (!el) return;
      const r = el.getBoundingClientRect();
      setAnchor((prev) =>
        prev &&
        Math.abs(prev.top - r.top) < 0.5 &&
        Math.abs(prev.left - r.left) < 0.5 &&
        Math.abs(prev.bottom - r.bottom) < 0.5
          ? prev
          : r
      );
    };
    const on = () => { if (!raf) raf = requestAnimationFrame(update); };
    window.addEventListener("scroll", on, true);
    window.addEventListener("resize", on);
    return () => {
      window.removeEventListener("scroll", on, true);
      window.removeEventListener("resize", on);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [openKey]);

  function applyDraft() {
    const col = columns.find((c) => c.key === openKey);
    if (!col || !col.filter) return;
    if (col.filter.kind === "select") {
      commitFilter(col.key, draft.select ? { kind: "select", value: draft.select } : null);
    } else if (col.filter.kind === "text") {
      commitFilter(col.key, draft.text ? { kind: "text", value: draft.text } : null);
    } else if (col.filter.kind === "date") {
      commitFilter(col.key, draft.from || draft.to ? { kind: "date", from: draft.from || undefined, to: draft.to || undefined } : null);
    } else {
      const min = draft.min === "" || draft.min === undefined ? undefined : Number(draft.min);
      const max = draft.max === "" || draft.max === undefined ? undefined : Number(draft.max);
      const mn = min !== undefined && !Number.isNaN(min) ? min : undefined;
      const mx = max !== undefined && !Number.isNaN(max) ? max : undefined;
      commitFilter(col.key, mn === undefined && mx === undefined ? null : { kind: "range", min: mn, max: mx });
    }
    setOpenKey(null);
  }

  function resetDraftFilter() {
    const col = columns.find((c) => c.key === openKey);
    if (!col || !col.filter) return;
    commitFilter(col.key, null);
    setDraft(col.filter.kind === "select" ? { select: "" } : col.filter.kind === "text" ? { text: "" } : col.filter.kind === "date" ? { from: "", to: "" } : { min: "", max: "" });
  }

  function exportCsv() {
    if (!csv) return;
    const esc = (v: unknown) => {
      const s = v === null || v === undefined ? "" : String(v);
      return /[",;\n]/.test(s) ? '"' + s.replace(/"/g, '""') : '"' + s + '"';
    };
    const lines = [columns.map((c) => c.label).join(";")];
    for (const r of visibleRows) {
      lines.push(columns.map((c) => esc(c.accessor(r))).join(";"));
    }
    const blob = new Blob(["\uFEFF" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = csv.filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  const openCol = openKey ? columns.find((c) => c.key === openKey) ?? null : null;
  const filtered = !managed && columns.some((c) => c.filter && isFilterActive(filters?.[c.key]));
  const thBase = "sticky top-0 z-10 select-none border-b border-slate-800 bg-slate-900 px-2.5 py-2";

  return (
    <div className={"overflow-hidden rounded-lg border border-slate-800 " + (className ?? "")}>
      <style>{`@keyframes dt-fade { from { opacity:0; transform: translateY(5px) } to { opacity:1; transform: translateY(0) } }`}</style>
      {(title !== undefined || headerRight !== undefined || csv) && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 bg-slate-900 px-3 py-2">
          <span className="font-display text-xs font-semibold uppercase tracking-[0.14em] text-slate-300">{title}</span>
          <div className="flex items-center gap-2.5">
            <span className="font-mono text-[10px] tabular-nums text-slate-600">
              {visibleRows.length}{filtered ? " из " + rows.length : ""}
            </span>
            {headerRight}
            {csv && (
              <button
                type="button"
                onClick={exportCsv}
                title="Скачать CSV (видимые строки)"
                className="rounded border border-slate-700 px-2 py-0.5 font-mono text-[10px] text-slate-300 transition-all duration-150 hover:border-sky-500 hover:text-sky-200 active:scale-95"
              >
                ⬇ CSV
              </button>
            )}
          </div>
        </div>
      )}
      <div className={scrollClass ?? "overflow-x-auto"}>
        <table className={"min-w-full " + (size === "xs" ? "text-xs" : "text-sm")}>
          <thead className="bg-slate-900 text-[10px] uppercase tracking-wider text-slate-500">
            <tr>
              {columns.map((col) => {
                const sortable = col.sortable !== false;
                const isSorted = effectiveSort?.key === col.key;
                const active = isFilterActive(filters?.[col.key]);
                return (
                  <th key={col.key} className={thBase + " " + (col.numeric ? "text-right" : "text-left")}>
                    <span className="inline-flex items-center gap-1">
                      {sortable ? (
                        <button
                          type="button"
                          onClick={() => toggleSort(col.key)}
                          title={"Сортировка: " + col.label}
                          className={"transition-colors hover:text-slate-200 " + (isSorted ? "text-sky-300" : "")}
                        >
                          {col.label}{isSorted ? (effectiveSort?.dir === "asc" ? " ↑" : " ↓") : ""}
                        </button>
                      ) : (
                        <span>{col.label}</span>
                      )}
                      {col.filter && (
                        <button
                          type="button"
                          ref={(el) => { btnRefs.current[col.key] = el; }}
                          onClick={(e) => { e.stopPropagation(); openFilter(col.key); }}
                          title={"Фильтр: " + col.label}
                          className={
                            "rounded p-0.5 transition-colors hover:bg-slate-700 " +
                            (active || openKey === col.key ? "text-sky-300" : "text-slate-500")
                          }
                        >
                          <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
                            stroke={active || openKey === col.key ? "#38bdf8" : "currentColor"}
                            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                            <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
                          </svg>
                        </button>
                      )}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((r, i) => (
              <tr
                key={rowKey(r, i)}
                onClick={onRowClick ? () => onRowClick(r) : undefined}
                className={
                  "border-t border-slate-800/70 transition-colors hover:bg-slate-900/70 " +
                  (rowClass ? rowClass(r) + " " : "") +
                  (onRowClick ? "cursor-pointer " : "")
                }
              >
                {columns.map((col) => (
                  <td key={col.key} className={"px-2.5 py-1.5 " + (col.tdClass ? col.tdClass + " " : "") + (col.numeric ? "text-right font-mono tabular-nums" : "")}>
                    {col.render ? col.render(r) : String(col.accessor(r) ?? "—")}
                  </td>
                ))}
              </tr>
            ))}
            {visibleRows.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="px-3 py-6 text-center text-xs text-slate-600">
                  {emptyText ?? "нет данных"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {openCol && openCol.filter && anchor && createPortal(
        <div
          ref={popRef}
          style={{
            position: "fixed",
            top: anchor.bottom + 4,
            left: Math.min(Math.max(anchor.left, 8), window.innerWidth - 272),
            zIndex: 50,
          }}
        >
          <FilterPopover
            col={openCol}
            draft={draft}
            setDraft={setDraft}
            options={openCol.filter.options ?? derivedOptions[openCol.key] ?? []}
            onApply={applyDraft}
            onReset={resetDraftFilter}
            onClose={() => setOpenKey(null)}
          />
        </div>,
        document.body
      )}
    </div>
  );
}
