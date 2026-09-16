/**
 * Trailing stop UI helpers for Paper / Live monitoring panels — Issue #150 (Epic #142).
 *
 * Pure and side-effect free: no fetch, no React, no JSX. Maps trailing-related server
 * values (exit reasons, trailing_enabled, current_stop_price, step_reached, risk_r) to
 * operator-facing labels, tone classes, and summary card data. Both panels consume this
 * module identically; neither panel hardcodes a trailing-specific label or number.
 *
 * The exit-reason vocabulary stays in `exitReasons.ts` (Issue #146). This module covers
 * the *trailing-specific* projection: active-stop chips, step-reached badges, and the
 * summary-card aggregates that appear above the table.
 */
import type { AppLocale } from "./i18n/config";

/* ──────────────────────────────────────────────────────────────────────
   Labels
   ────────────────────────────────────────────────────────────────────── */

const TRAILING_LABELS: Record<string, Record<AppLocale, string>> = {
  trailing_enabled: { ru: "Трейлинг", en: "Trailing" },
  trailing_disabled: { ru: "Без трейлинга", en: "No trailing" },
  active_stop: { ru: "Активный стоп", en: "Active stop" },
  no_stop: { ru: "Нет стопа", en: "No stop" },
  step_reached: { ru: "Ступень", en: "Step" },
  risk_r: { ru: "Риск R", en: "Risk R" },
  trailing_closed: { ru: "Закрыто трейлингом", en: "Closed by trailing" },
  trailing_closed_pnl: { ru: "PnL трейлинга", en: "Trailing PnL" },
  trailing_open: { ru: "Открыто с трейлингом", en: "Open with trailing" },
  active_stop_count: { ru: "Активных стопов", en: "Active stops" },
};

/** Operator-facing label for a trailing UI concept. Falls back to the key, never guesses. */
export function trailingLabel(key: string, locale: AppLocale): string {
  const entry = TRAILING_LABELS[key];
  return entry ? entry[locale] : key;
}

/* ──────────────────────────────────────────────────────────────────────
   Badges & chips
   ────────────────────────────────────────────────────────────────────── */

/** Tone class for a trailing-enabled/disabled chip in the positions table. */
export function trailingEnabledTone(enabled: boolean | null | undefined): string {
  if (enabled === true) return "bg-sky-500/15 text-sky-300";
  return "bg-slate-700/40 text-slate-400";
}

/** Tone class for an active-stop chip (open positions with current_stop_price set). */
export function activeStopTone(hasStop: boolean): string {
  return hasStop
    ? "bg-amber-500/10 text-amber-300"
    : "bg-slate-700/40 text-slate-400";
}

/** Tone class for a step-reached badge (closed positions). */
export function stepReachedTone(stepReached: number | null | undefined): string {
  if (stepReached === null || stepReached === undefined) return "bg-slate-700/40 text-slate-400";
  return stepReached > 0
    ? "bg-emerald-500/15 text-emerald-300"
    : "bg-slate-700/40 text-slate-400";
}

/* ──────────────────────────────────────────────────────────────────────
   Formatting helpers
   ────────────────────────────────────────────────────────────────────── */

/** Render a finite number to `digits` decimals, or `"—"` for null/NaN/undefined. */
export function fmtTrailingNum(value: unknown, digits = 2): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}

/** Render risk_r with a leading `+` when positive (it's R above entry). */
export function fmtRiskR(value: unknown): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "—";
  const sign = n >= 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}R`;
}

/** Render current_stop_price or `"—"` when absent (position has no trailing stop yet). */
export function fmtStopPrice(value: unknown): string {
  return fmtTrailingNum(value, 3);
}

/** Render step_reached as an integer ordinal (1-based), or `"—"` when null/zero. */
export function fmtStepReached(value: unknown): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n) || n <= 0) return "—";
  return String(Math.round(n));
}

/* ──────────────────────────────────────────────────────────────────────
   Summary cards
   ────────────────────────────────────────────────────────────────────── */

/** Trailing-specific summary card data derived from the overview response. */
export interface TrailingSummaryCard {
  key: string;
  label: string;
  value: string;
  tone: string;
}

/** Build trailing summary cards from the overview summary object. */
export function buildTrailingSummaryCards(
  summary: {
    trailing_closed?: number | null;
    trailing_closed_pnl_rub?: number | null;
    trailing_open?: number | null;
    active_stop_count?: number | null;
  } | null | undefined,
  locale: AppLocale,
): TrailingSummaryCard[] {
  if (!summary) return [];
  const t = (key: string) => trailingLabel(key, locale);
  const tc = summary.trailing_closed ?? 0;
  const tpnl = summary.trailing_closed_pnl_rub ?? 0;
  const to = summary.trailing_open ?? 0;
  const asc = summary.active_stop_count ?? 0;
  return [
    {
      key: "trailing_closed",
      label: t("trailing_closed"),
      value: String(tc),
      tone: tc > 0 ? "text-sky-300" : "text-slate-400",
    },
    {
      key: "trailing_closed_pnl",
      label: t("trailing_closed_pnl"),
      value: `${tpnl >= 0 ? "+" : ""}${fmtTrailingNum(tpnl, 0)} ₽`,
      tone: tpnl >= 0 ? "text-emerald-400" : "text-rose-400",
    },
    {
      key: "trailing_open",
      label: t("trailing_open"),
      value: String(to),
      tone: to > 0 ? "text-sky-300" : "text-slate-400",
    },
    {
      key: "active_stop_count",
      label: t("active_stop_count"),
      value: String(asc),
      tone: asc > 0 ? "text-amber-300" : "text-slate-400",
    },
  ];
}