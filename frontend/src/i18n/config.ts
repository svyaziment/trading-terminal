/**
 * Thin i18n layer — Issue #150 (Epic #142, Block P).
 *
 * The project has a mixed-locale UI: most panels are Russian-only, but the Pattern Lab
 * (Epic #87) and Trailing Stop editor (#146) already support ru/en via `LabLocale` in
 * `patternLab.ts` and `trailingStop.ts`. This module becomes the single source of truth
 * for the locale type and resolver, so new panels (Paper, Live) can add bilingual labels
 * without duplicating the resolution logic or fighting over the type name.
 *
 * `LabLocale` (and the `resolveLabLocale` helpers in `patternLab.ts` / `trailingStop.ts`)
 * are re-exported aliases of `AppLocale` / `resolveAppLocale` — existing imports keep
 * working, and the two old definitions collapse into one.
 */

/** The two locales the application serves content in. */
export type AppLocale = "ru" | "en";

/**
 * Determine the active locale from an explicit value, `document.documentElement.lang`,
 * or the `lang` URL search-param. Falls back to `"ru"` when none matches — the default
 * language of the primary audience.
 */
export function resolveAppLocale(lang?: string): AppLocale {
  let raw = lang ?? "";
  if (!raw && typeof document !== "undefined") {
    raw = document.documentElement.lang ?? "";
  }
  if (!raw && typeof window !== "undefined") {
    raw = new URLSearchParams(window.location.search).get("lang") ?? "";
  }
  return raw.toLowerCase().startsWith("en") ? "en" : "ru";
}

/**
 * Convenience alias for backward compatibility with `patternLab.ts` / `trailingStop.ts`.
 * New code should prefer `AppLocale`; old code keeps compiling.
 */
export type LabLocale = AppLocale;