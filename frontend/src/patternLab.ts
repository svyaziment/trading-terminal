import type { PatternDef, PatternParam } from "./types";

export type LabLocale = "ru" | "en";

export const PATTERN_CATEGORY_ORDER = [
  "levels",
  "signal",
  "trend",
  "price_action",
  "volume",
  "mean_reversion",
  "breakout",
] as const;

export const PATTERN_CATEGORY_LABELS_RU: Record<string, string> = {
  levels: "Уровни",
  signal: "Сигнал",
  trend: "Тренд",
  price_action: "Ценовое действие",
  volume: "Объём",
  mean_reversion: "Возврат к среднему",
  breakout: "Пробой",
  other: "Другие",
};

export const PATTERN_CATEGORY_LABELS_EN: Record<string, string> = {
  levels: "Levels",
  signal: "Signal",
  trend: "Trend",
  price_action: "Price action",
  volume: "Volume",
  mean_reversion: "Mean reversion",
  breakout: "Breakout",
  other: "Other",
};

export type PatternGroup = { category: string; label: string; patterns: PatternDef[] };

export function resolveLabLocale(lang?: string): LabLocale {
  const raw =
    lang ??
    (typeof document !== "undefined" ? document.documentElement.lang : "") ??
    "";
  return raw.toLowerCase().startsWith("en") ? "en" : "ru";
}

export function categoryLabel(category: string, locale: LabLocale): string {
  const table = locale === "en" ? PATTERN_CATEGORY_LABELS_EN : PATTERN_CATEGORY_LABELS_RU;
  return table[category] ?? category;
}

export function groupPatternsByCategory(defs: PatternDef[], locale: LabLocale = "ru"): PatternGroup[] {
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
      label: categoryLabel(category, locale),
      patterns,
    });
    seen.add(category);
  }
  for (const [category, patterns] of buckets) {
    if (seen.has(category) || !patterns.length) continue;
    groups.push({
      category,
      label: categoryLabel(category, locale),
      patterns,
    });
  }
  return groups;
}

export function patternLabel(def: PatternDef, locale: LabLocale): string {
  if (locale === "en" && def.label_en) return def.label_en;
  return def.label;
}

export function patternHint(def: PatternDef, locale: LabLocale): string {
  if (locale === "en" && def.hint_en) return def.hint_en;
  return def.hint ?? "";
}

export function paramLabel(param: PatternParam, locale: LabLocale): string {
  if (locale === "en" && param.label_en) return param.label_en;
  return param.label;
}

export function paramHint(param: PatternParam, locale: LabLocale): string {
  if (locale === "en" && param.hint_en) return param.hint_en;
  return param.hint ?? "";
}

/** Hover tooltip: EN name (when the UI is RU) plus the role-reversal hint. */
export function patternTooltip(def: PatternDef, locale: LabLocale): string {
  const hint = patternHint(def, locale);
  if (locale === "ru" && def.label_en && hint) return `${def.label_en}. ${hint}`;
  if (locale === "ru" && def.label_en) return def.label_en;
  return hint;
}

export function registryDefaults(def: PatternDef | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (!def) return out;
  for (const p of def.params) {
    if (p.default !== undefined) out[p.key] = p.default;
  }
  return out;
}

export function effectivePatternParams(
  def: PatternDef | undefined,
  saved: Record<string, unknown> | undefined,
): Record<string, unknown> {
  return { ...registryDefaults(def), ...(saved ?? {}) };
}
