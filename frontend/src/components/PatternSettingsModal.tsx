import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import type { PatternDef, PatternParam } from "../types";
import { paramHint, paramLabel, patternLabel, resolveLabLocale, type LabLocale } from "../patternLab";
import { validateParam, validatePatternValues } from "../patternValidation";

/* ==================================================================
   PatternSettingsModal — универсальная модалка настроек паттерна (issue #15)
   Поля рендерятся по схеме из реестра (GET /api/patterns -> PatternDef.params).
   Draft-семантика: «Применить» коммитит значения, «Отмена»/Escape/фон — нет.
   «Сбросить дефолты» возвращает значения из schema.default.
   locked=true → режим только чтения (стратегия в paper trading).
   Issue #109: min/max показываются ошибкой (красный бордер), без тихого clamp при вводе.
   ================================================================== */

export interface PatternSettingsModalProps {
  def: PatternDef;
  /** эффективные значения (дефолты реестра + сохранённые оверрайды) */
  values: Record<string, unknown>;
  locked?: boolean;
  onSave: (values: Record<string, unknown>) => void;
  onClose: () => void;
  /** Открыть превью на графике с текущим draft (Issue #90) */
  onShowChart?: (draft: Record<string, unknown>) => void;
  /** Ошибка окна превью (период Lab) — показывается в футере */
  previewError?: string | null;
  locale?: LabLocale;
}

function clone(v: unknown): unknown {
  return v === undefined ? undefined : JSON.parse(JSON.stringify(v));
}

export function defaultsOf(def: PatternDef): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of def.params) {
    if (p.default !== undefined) out[p.key] = clone(p.default);
  }
  return out;
}

function roundToStep(v: number, step?: number): number {
  if (!step || step <= 0) return v;
  const inv = 1 / step;
  return Math.round(v * inv) / inv;
}

function clampNum(v: number, p: PatternParam): number {
  let out = v;
  if (p.min !== undefined && out < p.min) out = p.min;
  if (p.max !== undefined && out > p.max) out = p.max;
  return roundToStep(out, p.step);
}

function toggleMulti(arr: unknown[], opt: string | number): unknown[] {
  const has = arr.some((x) => String(x) === String(opt));
  const next = has ? arr.filter((x) => String(x) !== String(opt)) : [...arr, opt];
  if (next.length > 0 && next.every((x) => typeof x === "number")) {
    return [...next].sort((a, b) => (a as number) - (b as number));
  }
  return next;
}

function fieldClass(error: boolean): string {
  return error
    ? "border-rose-500 bg-slate-950 focus:border-rose-400"
    : "border-slate-700 bg-slate-950 focus:border-sky-500";
}

/* ---------- поле одного параметра ---------- */

function ParamField(props: {
  param: PatternParam;
  value: unknown;
  onChange: (v: unknown) => void;
  locked: boolean;
  locale: LabLocale;
}) {
  const { param, value, onChange, locked, locale } = props;
  const label = paramLabel(param, locale);
  const hint = paramHint(param, locale);
  const error = validateParam(param, value);

  if (param.type === "number") {
    const empty = value === "" || value === undefined || value === null;
    const num = typeof value === "number" && Number.isFinite(value) ? value : Number(value);
    const sliderValue = Number.isFinite(num) ? num : (param.min ?? 0);
    return (
      <div>
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <label htmlFor={"param-" + param.key} className="text-[10px] uppercase tracking-wider text-slate-500" title={hint || undefined}>
            {label}
          </label>
          <input
            id={"param-" + param.key}
            type="number"
            step={param.step ?? "any"}
            min={param.min}
            max={param.max}
            disabled={locked}
            aria-invalid={Boolean(error)}
            aria-label={label}
            value={empty || !Number.isFinite(num) ? "" : num}
            onChange={(e) => {
              const t = e.target.value;
              if (t === "") onChange("");
              else {
                const n = Number(t);
                onChange(Number.isNaN(n) ? t : n);
              }
            }}
            className={
              "w-20 rounded border px-1.5 py-0.5 text-right font-mono text-[11px] tabular-nums text-sky-200 outline-none transition disabled:cursor-not-allowed " +
              fieldClass(Boolean(error))
            }
          />
        </div>
        <input
          type="range"
          disabled={locked}
          value={sliderValue}
          min={param.min ?? 0}
          max={param.max ?? 100}
          step={param.step ?? 1}
          onChange={(e) => onChange(clampNum(Number(e.target.value), param))}
          className="w-full accent-sky-500 disabled:cursor-not-allowed"
        />
        <div className="mt-0.5 flex justify-between font-mono text-[9px] tabular-nums text-slate-600">
          <span>{param.min ?? "—"}</span>
          <span>шаг {param.step ?? 1}</span>
          <span>{param.max ?? "—"}</span>
        </div>
        {error && (
          <p className="mt-1 text-[10px] text-rose-400" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  }

  if (param.type === "select" || param.type === "multiselect") {
    const multi = param.type === "multiselect";
    const arr = Array.isArray(value) ? (value as unknown[]) : [];
    const opts = param.options ?? [];
    const isOn = (o: string | number) =>
      multi ? arr.some((x) => String(x) === String(o)) : String(value ?? "") === String(o);
    const pick = (o: string | number) => {
      if (multi) onChange(toggleMulti(arr, o));
      else onChange(o);
    };
    return (
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-[10px] uppercase tracking-wider text-slate-500" title={hint || undefined}>
            {label}
          </span>
          {multi && <span className="font-mono text-[9px] text-slate-600">выбрано: {arr.length}</span>}
        </div>
        <div
          role="group"
          aria-label={label}
          aria-invalid={Boolean(error)}
          className={"flex flex-wrap gap-1.5 rounded " + (error ? "ring-1 ring-rose-500" : "")}
        >
          {opts.map((o: string | number) => (
            <button
              key={String(o)}
              type="button"
              disabled={locked}
              onClick={() => pick(o)}
              className={
                "rounded border px-2 py-1 font-mono text-[11px] transition-all duration-150 active:scale-95 disabled:cursor-not-allowed " +
                (isOn(o)
                  ? "border-sky-500/70 bg-sky-500/20 text-sky-200 shadow-[0_0_10px_rgba(56,189,248,0.15)]"
                  : "border-slate-700 bg-slate-900 text-slate-400 hover:border-slate-500 hover:text-slate-200")
              }
            >
              {String(o)}
            </button>
          ))}
        </div>
        {error && (
          <p className="mt-1 text-[10px] text-rose-400" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  }

  if (param.type === "boolean") {
    return (
      <label className="flex items-center justify-between gap-2 rounded border border-slate-800 bg-slate-950/60 px-2.5 py-2" title={hint || undefined}>
        <span className="text-[10px] uppercase tracking-wider text-slate-500">{label}</span>
        <input
          type="checkbox"
          disabled={locked}
          aria-label={label}
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
          className="accent-sky-500 disabled:cursor-not-allowed"
        />
      </label>
    );
  }

  return (
    <div>
      <label htmlFor={"param-" + param.key} className="mb-1.5 block text-[10px] uppercase tracking-wider text-slate-500" title={hint || undefined}>
        {label}
      </label>
      <input
        id={"param-" + param.key}
        disabled={locked}
        aria-label={label}
        aria-invalid={Boolean(error)}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        className={"w-full rounded border px-2 py-1 font-mono text-xs text-slate-200 outline-none transition disabled:cursor-not-allowed " + fieldClass(Boolean(error))}
      />
      {error && (
        <p className="mt-1 text-[10px] text-rose-400" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/* ---------- модалка ---------- */

export default function PatternSettingsModal(props: PatternSettingsModalProps) {
  const { def, values, locked, onSave, onClose, onShowChart, previewError } = props;
  const isLocked = locked === true;
  const locale = props.locale ?? resolveLabLocale();

  const [draft, setDraft] = useState<Record<string, unknown>>(() => {
    const base = defaultsOf(def);
    for (const [k, v] of Object.entries(values)) base[k] = clone(v);
    return base;
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const tuned = useMemo(() => {
    const defs = defaultsOf(def);
    return Object.keys(draft).some(
      (k) => JSON.stringify(draft[k] ?? null) !== JSON.stringify(defs[k] ?? null)
    );
  }, [draft, def]);

  const errors = useMemo(() => validatePatternValues(def, draft), [def, draft]);
  const hasErrors = Object.keys(errors).length > 0;

  function resetDefaults() {
    setDraft(defaultsOf(def));
  }

  function apply() {
    if (hasErrors) return;
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(draft)) out[k] = clone(v);
    onSave(out);
  }

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4"
      style={{ animation: "psm-fade .18s ease-out" }}
      onClick={() => { if (!isLocked) onClose(); }}
    >
      <style>{`
 @keyframes psm-fade { from { opacity:0 } to { opacity:1 } }
 @keyframes psm-pop { from { opacity:0; transform: translateY(10px) scale(.97) } to { opacity:1; transform: translateY(0) scale(1) } }
`}</style>
      <div
        className="w-full max-w-md overflow-hidden rounded-lg border border-slate-700 bg-slate-900 shadow-2xl"
        style={{ animation: "psm-pop .22s cubic-bezier(.2,.9,.3,1.15)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="relative border-b border-slate-800 bg-slate-950/50 px-4 py-3">
          <div
            className="pointer-events-none absolute inset-0"
            style={{
              backgroundImage: "radial-gradient(circle, rgba(148,163,184,0.07) 1px, transparent 1px)",
              backgroundSize: "16px 16px",
            }}
          />
          <div className="relative flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                {def.category && (
                  <span className="rounded border border-sky-700/50 bg-sky-500/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-widest text-sky-300">
                    {def.category}
                  </span>
                )}
                <h3 className="font-display text-sm font-semibold uppercase tracking-[0.12em] text-slate-100">
                  {patternLabel(def, locale)}
                </h3>
              </div>
              {(def.label_en && locale === "ru") && (
                <p className="mt-0.5 font-mono text-[10px] text-slate-500">{def.label_en}</p>
              )}
              {def.hint && <p className="mt-1 text-[11px] leading-relaxed text-slate-500">{locale === "en" && def.hint_en ? def.hint_en : def.hint}</p>}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Закрыть"
              className="shrink-0 rounded p-1 text-slate-500 transition hover:bg-slate-800 hover:text-slate-200"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </div>

        <div className="max-h-[55vh] space-y-4 overflow-y-auto px-4 py-4">
          {isLocked && (
            <div className="rounded border border-amber-600/60 bg-amber-500/10 px-2.5 py-1.5 text-[10px] text-amber-200">
              🔒 Стратегия в paper trading — параметры только для просмотра.
            </div>
          )}
          {def.params.length === 0 && (
            <div className="rounded border border-slate-800 bg-slate-950/60 px-3 py-4 text-center text-[11px] text-slate-500">
              Паттерн без параметров — сигнал формируется фиксированной логикой.
            </div>
          )}
          {def.params.map((p: PatternParam) => (
            <ParamField
              key={p.key}
              param={p}
              value={draft[p.key]}
              locked={isLocked}
              locale={locale}
              onChange={(v) => setDraft((d) => ({ ...d, [p.key]: v }))}
            />
          ))}
        </div>

        <div className="border-t border-slate-800 bg-slate-950/50 px-4 py-3">
          {previewError && (
            <div className="mb-2 rounded border border-amber-700/50 bg-amber-500/10 px-2.5 py-1.5 text-[10px] text-amber-200">
              {previewError}
            </div>
          )}
          <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={resetDefaults}
            disabled={isLocked || !tuned}
            className="relative rounded border border-rose-700/60 px-2.5 py-1.5 text-[11px] text-rose-300 transition hover:bg-rose-500/10 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Сбросить дефолты
            {tuned && !isLocked && (
              <span className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-amber-400 shadow-[0_0_6px_rgba(245,158,11,.6)]" />
            )}
          </button>
          {onShowChart && (
            <button
              type="button"
              onClick={() => onShowChart({ ...draft })}
              className="rounded border border-violet-700/60 px-2.5 py-1.5 text-[11px] text-violet-200 transition hover:bg-violet-500/10"
            >
              Показать на графике
            </button>
          )}
          <div className="flex-1" />
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-slate-600 px-3 py-1.5 text-[11px] font-medium text-slate-300 transition hover:bg-slate-800"
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={apply}
            disabled={isLocked || hasErrors}
            className="rounded bg-sky-700 px-3.5 py-1.5 text-[11px] font-semibold text-sky-50 shadow-[0_0_14px_rgba(2,132,199,0.35)] transition hover:bg-sky-600 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40"
          >
            Применить
          </button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
