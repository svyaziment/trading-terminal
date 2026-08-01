import { formatFilterValue, type FilterState, type FilterValue } from "./DataTable";

/* ==================================================================
   FilterChips — единая панель активных фильтров (task-002)
   Стиль чипов — как в SignalsPanel: rounded-full, "×", "Сбросить всё".
   Используется над таблицами; состояние фильтров — снаружи.
   ================================================================== */

export interface FilterChipsProps {
  filters: FilterState;
  onChange: (f: FilterState) => void;
  /** key колонки -> человекочитаемое имя ("PF", "причина") */
  labels?: Record<string, string>;
  /** кастомное отображение значения (например exit_reason: "take" -> "тейк") */
  valueLabel?: (key: string, v: FilterValue) => string;
  className?: string;
}

export default function FilterChips(props: FilterChipsProps) {
  const { filters, onChange, labels, valueLabel, className } = props;
  const entries = Object.entries(filters);
  if (entries.length === 0) return null;

  function clear(key: string) {
    const next = { ...filters };
    delete next[key];
    onChange(next);
  }

  return (
    <div
      className={"flex flex-wrap items-center gap-2 " + (className ?? "")}
      style={{ animation: "dt-fade .18s ease-out" }}
    >
      <style>{`@keyframes dt-fade { from { opacity:0; transform: translateY(5px) } to { opacity:1; transform: translateY(0) } }`}</style>
      <span className="font-display text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">
        Фильтры
      </span>
      {entries.map(([key, v]) => (
        <button
          key={key}
          type="button"
          onClick={() => clear(key)}
          title="Убрать фильтр"
          className="flex items-center gap-1 rounded-full border border-sky-700/60 bg-sky-500/10 px-2 py-0.5 font-mono text-xs text-sky-300 transition-all duration-150 hover:border-sky-500/80 hover:bg-sky-500/20 active:scale-95"
        >
          <span className="text-sky-500/90">{labels?.[key] ?? key}=</span>
          <span>{valueLabel ? valueLabel(key, v) : formatFilterValue(v)}</span>
          <span className="text-sky-400">×</span>
        </button>
      ))}
      <button
        type="button"
        onClick={() => onChange({})}
        className="rounded-full border border-slate-700 px-2 py-0.5 text-xs text-slate-400 transition-all duration-150 hover:border-rose-600/60 hover:bg-slate-800 hover:text-rose-300 active:scale-95"
      >
        Сбросить всё
      </button>
    </div>
  );
}
