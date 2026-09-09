import type { TrailingStopSchema } from "../types";
import {
  matchesDefaultGrid,
  reasonCodeShort,
  reasonText,
  shareOfTake,
  type LabLocale,
  type TrailingRow,
  type TrailingValidation,
} from "../trailingStop";

/* ==================================================================
   TrailingStopFields — редактор ступенчатого трейлинг-стопа (issue #146).

   Top-level `config.trailing_stop` block (#144), NOT a pattern parameter: it is an
   exit-policy section rendered next to «Издержки» / «Risk / Reward», not a field of
   levels_reversal. Everything it shows — the toggle, the bounds, the +/− limits, the
   keystroke resolution, the default grid's name and values, the reason codes — arrives
   through `schema` from GET /api/strategies/trailing-schema. There is deliberately no
   trailing number in this file: hardcoding one is the red line #146 names twice
   («нулевой хардкод полей/чисел трейлинга в TSX»).

   The ladder, the touched flag and the validation live in StrategyLab, so the saved
   `config` memo and the save guard see exactly what the operator sees.
   ================================================================== */

const cell =
  "w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-200 outline-none transition focus:border-sky-500 disabled:cursor-not-allowed";
const cellBad =
  "w-full rounded border border-rose-500 bg-slate-950 px-2 py-1 font-mono text-xs text-rose-200 outline-none transition focus:border-rose-400 disabled:cursor-not-allowed";

type Copy = {
  trigger: string; stop: string; ofTake: string; add: string; remove: string;
  applyDefault: string; hint: string; none: string; ladder: string;
};

const TITLES: Record<LabLocale, Copy> = {
  ru: {
    trigger: "триггер, R",
    stop: "стоп, R",
    ofTake: "до тейка",
    add: "Добавить ступень",
    remove: "Убрать ступень",
    applyDefault: "Применить дефолтную сетку",
    hint: "Ступени — в R от цены входа: при +trigger R плавающего профиля стоп переносится на +stop R.",
    none: "Лестница пуста — трейлинг не вооружён.",
    ladder: "Лестница",
  },
  en: {
    trigger: "trigger, R",
    stop: "stop, R",
    ofTake: "of take",
    add: "Add step",
    remove: "Remove step",
    applyDefault: "Apply default grid",
    hint: "Steps are R multiples from entry: at +trigger R of floating profit the stop moves to +stop R.",
    none: "Ladder is empty — trailing stays unarmed.",
    ladder: "Ladder",
  },
};

export interface TrailingStopFieldsProps {
  schema: TrailingStopSchema;
  enabled: boolean;
  rows: TrailingRow[];
  validation: TrailingValidation;
  locked: boolean;
  riskReward: { risk: number; reward: number } | null;
  locale: LabLocale;
  onToggle: (next: boolean) => void;
  onEditRow: (index: number, field: "trigger" | "stop", value: string) => void;
  onAddRow: () => void;
  onRemoveRow: (index: number) => void;
  onApplyDefault: () => void;
}

export default function TrailingStopFields(props: TrailingStopFieldsProps) {
  const { schema, enabled, rows, validation, locked, riskReward, locale } = props;
  const t = TITLES[locale];
  const atMax = rows.length >= schema.max_steps;
  const isDefault = matchesDefaultGrid(rows, schema);
  const stepAttr = String(schema.input_step);
  return (
    <div>
      <label className="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
        <input
          type="checkbox"
          checked={enabled}
          disabled={locked}
          onChange={(e) => props.onToggle(e.target.checked)}
        />
        {locale === "en" ? "stepped trailing stop" : "ступенчатый трейлинг-стоп"}
      </label>

      <p className="mb-2 text-[10px] leading-snug text-slate-500">{t.hint}</p>

      {rows.length === 0 && (
        <p className="mb-2 text-[10px] text-slate-600">{t.none}</p>
      )}

      <div className="mb-1.5 grid grid-cols-[1fr_1fr_3.5rem_1.5rem] gap-1.5">
        <span className="text-[10px] uppercase tracking-wider text-slate-500">{t.trigger}</span>
        <span className="text-[10px] uppercase tracking-wider text-slate-500">{t.stop}</span>
        <span className="text-right text-[10px] uppercase tracking-wider text-slate-500">{t.ofTake}</span>
        <span />
      </div>

      <div className="space-y-1.5">
        {rows.map((row, i) => {
          const codes = validation.rowCodes[i] ?? [];
          const bad = codes.length > 0;
          const trigger = Number(row.trigger);
          const shown = row.trigger.trim() !== "" && Number.isFinite(trigger);
          const share = shown ? shareOfTake(trigger, riskReward) : null;
          return (
            <div key={i} className="grid grid-cols-[1fr_1fr_3.5rem_1.5rem] items-center gap-1.5">
              <input
                type="number"
                inputMode="decimal"
                step={stepAttr}
                min={String(schema.min_trigger)}
                max={String(schema.max_trigger)}
                value={row.trigger}
                readOnly={locked}
                aria-label={`${t.trigger} ${i + 1}`}
                onChange={(e) => props.onEditRow(i, "trigger", e.target.value)}
                className={bad ? cellBad : cell}
              />
              <input
                type="number"
                inputMode="decimal"
                step={stepAttr}
                min={String(schema.min_stop)}
                max={String(schema.max_stop)}
                value={row.stop}
                readOnly={locked}
                aria-label={`${t.stop} ${i + 1}`}
                onChange={(e) => props.onEditRow(i, "stop", e.target.value)}
                className={bad ? cellBad : cell}
              />
              <span className="text-right font-mono text-[10px] text-slate-500">
                {share === null ? "—" : `${Math.round(share * 100)}%`}
              </span>
              <button
                type="button"
                disabled={locked}
                title={t.remove}
                aria-label={t.remove}
                onClick={() => props.onRemoveRow(i)}
                className="w-6 rounded p-1 text-slate-500 transition hover:bg-slate-800 hover:text-rose-300 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
              </button>
            </div>
          );
        })}
      </div>

      {validation.reasons.length > 0 && (
        <ul className="mt-2 space-y-1">
          {validation.reasons.map((code) => (
            <li
              key={code}
              className="rounded border border-rose-700/50 bg-rose-500/10 px-2 py-1 text-[10px] text-rose-200"
            >
              <span className="mr-1.5 rounded bg-rose-500/20 px-1 font-mono text-[9px] text-rose-300">
                {reasonCodeShort(code)}
              </span>
              {reasonText(code, locale)}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          disabled={locked || atMax}
          title={atMax ? `${t.add}: ${schema.max_steps}` : t.add}
          onClick={props.onAddRow}
          className="rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition hover:border-slate-500 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
        >
          + {t.add}
        </button>
        <button
          type="button"
          disabled={locked || isDefault}
          title={`${t.applyDefault}: ${schema.default_grid}`}
          onClick={props.onApplyDefault}
          className="rounded border border-sky-700/60 bg-sky-500/10 px-2 py-1 font-mono text-[11px] text-sky-200 transition hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-40"
        >
          ⤺ {t.applyDefault} · {schema.default_grid}
        </button>
      </div>
    </div>
  );
}

