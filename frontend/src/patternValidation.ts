import type { PatternDef, PatternParam } from "./types";
import { effectivePatternParams } from "./patternLab";

function rangeMessage(param: PatternParam): string {
  const min = param.min;
  const max = param.max;
  if (min !== undefined && max !== undefined) return `допустимо от ${min} до ${max}`;
  if (min !== undefined) return `должно быть ≥ ${min}`;
  if (max !== undefined) return `должно быть ≤ ${max}`;
  return "недопустимое значение";
}

export function validateParam(param: PatternParam, value: unknown): string | null {
  if (param.type === "number") {
    if (value === "" || value === null || value === undefined) return "обязательное поле";
    const n = typeof value === "number" ? value : Number(value);
    if (!Number.isFinite(n)) return "обязательное поле";
    if (param.min !== undefined && n < param.min) return rangeMessage(param);
    if (param.max !== undefined && n > param.max) return rangeMessage(param);
    if (param.step === 1 && !Number.isInteger(n)) return "должно быть целым числом";
    return null;
  }
  if (param.type === "select") {
    if (value === undefined || value === null || value === "") return "обязательное поле";
    const opts = param.options ?? [];
    if (opts.length > 0 && !opts.some((o) => String(o) === String(value))) {
      return "выберите значение из списка";
    }
    return null;
  }
  if (param.type === "multiselect") {
    const arr = Array.isArray(value) ? value : [];
    if (arr.length === 0) return "выберите хотя бы одно значение";
    return null;
  }
  if (param.type === "boolean") return null;
  if (value === undefined || value === null || String(value).trim() === "") return "обязательное поле";
  return null;
}

export function validatePatternValues(
  def: PatternDef,
  values: Record<string, unknown>,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const param of def.params) {
    const err = validateParam(param, values[param.key]);
    if (err) errors[param.key] = err;
  }
  return errors;
}

export function firstPatternValidationError(
  registry: PatternDef[],
  configs: Record<string, Record<string, unknown>>,
): string | null {
  const byId = new Map(registry.map((d) => [d.id, d]));
  for (const id of Object.keys(configs)) {
    const def = byId.get(id);
    if (!def) continue;
    const errors = validatePatternValues(def, effectivePatternParams(def, configs[id]));
    const first = Object.entries(errors)[0];
    if (first) return `${def.label}: ${first[1]}`;
  }
  return null;
}
