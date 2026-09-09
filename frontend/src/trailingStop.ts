/**
 * Stepped trailing-stop editor logic — Issue #146 (Epic #142, Block W).
 *
 * Pure and side-effect free: no fetch, no React, no JSX. `TrailingStopSection.tsx` is a
 * renderer over these functions, which is what lets #146 keep its promise of "zero
 * hardcoded trailing fields or numbers in TSX" — and this module upholds the same rule:
 * every bound, default and step value it reasons about is *passed in* as a
 * `TrailingStopSchema` read from GET /api/strategies/trailing-schema. The only literals
 * here are structural (empty strings, zero as a numeric identity, array indices).
 *
 * `validateLadder` mirrors `trading_config.validate_trailing_steps` (Issue #144) step for
 * step, including the order reasons are produced in and the de-duplication rules, so the
 * browser and the server reach the same verdict on the same ladder. The server stays the
 * source of truth: this is a pre-flight that stops the operator from saving a config #144
 * would refuse, not a replacement for it.
 */
import type {
  TrailingStopConfig,
  TrailingStopSchema,
  TrailingStopStep,
} from "./types";

/** One ladder row while it is being typed. `""` (untouched) is distinct from `"0"` (#146). */
export interface TrailingRow {
  trigger: string;
  stop: string;
}

/** Stable reason codes of Issue #144. Names, not values — never renamed on the fly. */
export const TRAILING_REASON = {
  disabled: "trailing_disabled",
  stepInvalid: "trailing_step_invalid",
  notMonotonic: "trailing_not_monotonic",
  tooManySteps: "trailing_too_many_steps",
} as const;

export type LabLocale = "ru" | "en";

export function blankRow(): TrailingRow {
  return { trigger: "", stop: "" };
}

/** A ladder with one empty row, so an unused editor still shows where to type. */
export function emptyLadder(): TrailingRow[] {
  return [blankRow()];
}

/** Render a contract number without inventing a false zero or leaking float noise. */
export function numToText(value: unknown): string {
  if (value === null || value === undefined) return "";
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? String(n) : "";
}

/**
 * Rows to edit a stored ladder with. An absent or empty ladder still renders one blank
 * row: the editor must be usable before the first step exists.
 */
export function rowsFromLadder(config: TrailingStopConfig | null | undefined): TrailingRow[] {
  const steps = config?.steps;
  if (!Array.isArray(steps) || steps.length === 0) return emptyLadder();
  return steps.map((step) => ({
    trigger: numToText(step?.trigger),
    stop: numToText(step?.stop),
  }));
}

/** Parse one typed cell. Only a finite number counts; `""`, `"-"`, `NaN`, `Infinity` do not. */
export function parseR(value: string): number | null {
  if (typeof value !== "string" || value.trim() === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** The approved default ladder, copied out of the contract — never restated here. */
export function defaultLadder(schema: TrailingStopSchema): TrailingRow[] {
  return rowsFromLadder({ enabled: schema.enabled, steps: schema.steps });
}

/** Complete rows only; a half-typed row is not a step and must never be sent. */
export function stepsFromRows(rows: TrailingRow[]): TrailingStopStep[] {
  const steps: TrailingStopStep[] = [];
  for (const row of rows) {
    const trigger = parseR(row.trigger);
    const stop = parseR(row.stop);
    if (trigger === null || stop === null) continue;
    steps.push({ trigger, stop });
  }
  return steps;
}

function inBounds(step: TrailingStopStep, schema: TrailingStopSchema): boolean {
  // Same shape as _trailing_step_in_bounds(): min_trigger is exclusive, the rest are
  // inclusive, and a stop can never sit at or above its own trigger.
  return (
    step.trigger > schema.min_trigger &&
    step.trigger <= schema.max_trigger &&
    step.stop >= schema.min_stop &&
    step.stop <= schema.max_stop &&
    step.stop < step.trigger
  );
}
export interface TrailingValidation {
  /** Stable #144 codes, sorted and de-duplicated — exactly what the server would answer. */
  reasons: string[];
  /** Per-row codes so the editor can mark the offending rung, not just the whole ladder. */
  rowCodes: (string[] | undefined)[];
}

/**
 * Client-side pre-flight for one ladder, mirroring #144's `validate_trailing_steps`.
 *
 * `enabled` only decides whether an empty ladder is a rejection: a stored-but-switched-off
 * ladder is still validated, because that is the ladder the engine arms the moment the
 * flag is flipped (#144: bounds are checked even when `enabled` is False).
 */
export function validateLadder(
  rows: TrailingRow[],
  schema: TrailingStopSchema,
  enabled: boolean,
): TrailingValidation {
  const reasons: string[] = [];
  const rowCodes: (string[] | undefined)[] = rows.map(() => undefined);

  const mark = (index: number, code: string) => {
    if (!reasons.includes(code)) reasons.push(code);
    const existing = rowCodes[index];
    rowCodes[index] = existing && existing.includes(code) ? existing : [...(existing ?? []), code];
  };

  const typed = rows
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => row.trigger.trim() !== "" || row.stop.trim() !== "");

  if (typed.length === 0) {
    return { reasons: enabled ? [TRAILING_REASON.disabled] : [], rowCodes };
  }

  const parsed: { step: TrailingStopStep; index: number }[] = [];
  for (const { row, index } of typed) {
    const trigger = parseR(row.trigger);
    const stop = parseR(row.stop);
    const step = trigger === null || stop === null ? null : { trigger, stop };
    if (step === null || !inBounds(step, schema)) {
      mark(index, TRAILING_REASON.stepInvalid);
      continue;
    }
    parsed.push({ step, index });
  }

  parsed.sort((a, b) =>
    a.step.trigger !== b.step.trigger
      ? a.step.trigger - b.step.trigger
      : a.step.stop - b.step.stop,
  );

  const deduped: { step: TrailingStopStep; index: number }[] = [];
  for (const entry of parsed) {
    const last = deduped[deduped.length - 1];
    if (last) {
      if (last.step.trigger === entry.step.trigger && last.step.stop === entry.step.stop) {
        continue; // exact duplicate — normalization collapses it, it is not an error
      }
      if (last.step.trigger === entry.step.trigger) {
        // Same trigger twice with two different stops: no defined order.
        mark(entry.index, TRAILING_REASON.notMonotonic);
      }
    }
    deduped.push(entry);
  }

  if (deduped.length > schema.max_steps) {
    for (const entry of deduped.slice(schema.max_steps)) {
      mark(entry.index, TRAILING_REASON.tooManySteps);
    }
  }

  let highestStop = Number.NEGATIVE_INFINITY;
  let flagged = false;
  for (const entry of deduped) {
    if (entry.step.stop < highestStop && !flagged) {
      mark(entry.index, TRAILING_REASON.notMonotonic);
      flagged = true; // #144 emits this code once; keep the verdict identical
    }
    highestStop = Math.max(highestStop, entry.step.stop);
  }

  return { reasons: reasons.slice().sort(), rowCodes };
}

/**
 * The `config.trailing_stop` block to persist.
 *
 * Returns `null` for "leave the config exactly as it is": an untouched editor on a strategy
 * that never carried the key must not start sending one built out of UI defaults (#146).
 * `enabled: false` is still a real answer once the operator has touched the block — it says
 * "this strategy opts out", which is not the same as "no opinion".
 */
export function buildTrailingStopPayload(args: {
  touched: boolean;
  stored: TrailingStopConfig | null | undefined;
  enabled: boolean;
  rows: TrailingRow[];
  schema: TrailingStopSchema | null;
}): TrailingStopConfig | null {
  const { touched, stored, enabled, rows, schema } = args;
  if (schema === null) return null;
  if (!touched) {
    // Echo whatever was stored; never synthesise a block from UI defaults.
    if (!stored) return null;
    return { enabled: stored.enabled, steps: stepsFromRows(rowsFromLadder(stored)) };
  }
  return { enabled, steps: stepsFromRows(rows) };
}

/** True when the ladder on screen already equals the contract's default one. */
export function matchesDefaultGrid(rows: TrailingRow[], schema: TrailingStopSchema): boolean {
  const current = stepsFromRows(rows);
  const wanted = schema.steps;
  if (current.length !== wanted.length) return false;
  return current.every(
    (step, i) => step.trigger === wanted[i].trigger && step.stop === wanted[i].stop,
  );
}


const REASON_TEXT: Record<string, Record<LabLocale, string>> = {
  [TRAILING_REASON.disabled]: {
    ru: "трейлинг включён, но лестница пуста — добавьте хотя бы одну ступень",
    en: "trailing is on but the ladder is empty — add at least one step",
  },
  [TRAILING_REASON.stepInvalid]: {
    ru: "ступень недопустима: не оба поля заполнены, значение вне границ схемы, или stop ≥ trigger",
    en: "invalid step: a field is empty, out of the schema bounds, or stop ≥ trigger",
  },
  [TRAILING_REASON.notMonotonic]: {
    ru: "ступени не монотонны: stop не должен падать при росте trigger; два stop на одном trigger недопустимы",
    en: "ladder is not monotonic: stop must not fall as trigger rises; two stops on one trigger are not allowed",
  },
  [TRAILING_REASON.tooManySteps]: {
    ru: "лестница длиннее разрешённого максимума из схемы",
    en: "the ladder is longer than the maximum the schema allows",
  },
};

/** Operator-facing text for a reason code. Falls back to the code itself, never guesses. */
export function reasonText(code: string, locale: LabLocale): string {
  const entry = REASON_TEXT[code];
  if (entry) return entry[locale];
  return code;
}

/** Short chip text: `trailing_not_monotonic` → `not_monotonic`. */
export function reasonCodeShort(code: string): string {
  return code.startsWith("trailing_") ? code.slice("trailing_".length) : code;
}

/** `+2R→+1.9R, …` for the section badge. Built from the ladder, never stored. */
export function describeLadder(rows: TrailingRow[]): string {
  return stepsFromRows(rows)
    .map((step) => `+${step.trigger}R→+${step.stop}R`)
    .join(", ");
}

/**
 * Where a rung sits relative to the strategy's take-profit, as a fraction of that run.
 *
 * With `risk_reward` = {risk, reward} the take lies at `reward / risk` R above entry, so a
 * rung at `trigger` R is `trigger / (reward / risk)` of the way there. This is the derived
 * figure the editor can honestly show from config alone — an absolute ₽ price would need a
 * live entry quote, which the config rail does not have.
 */
export function shareOfTake(
  triggerR: number,
  riskReward: { risk: number; reward: number } | null | undefined,
): number | null {
  if (!riskReward) return null;
  const risk = Number(riskReward.risk);
  const reward = Number(riskReward.reward);
  if (!Number.isFinite(risk) || !Number.isFinite(reward) || risk <= 0) return null;
  const takeR = reward / risk;
  if (takeR <= 0) return null;
  return triggerR / takeR;
}

