/**
 * Exit-reason vocabulary for the Lab's trade table — Issue #146 (Epic #142, Block W).
 *
 * The engine may emit only the closed set in `backtest_models.VALID_EXIT_REASONS`:
 * stop | take | holding | signal | session, plus `trailing` since #144/#145
 * (`EXIT_UNTRADEABLE` is a skip marker, not a trade exit). Until #146 the Lab rendered
 * this column as a binary take/stop, so a trailing exit — the whole point of the ladder —
 * was silently labelled «стоп» and could not be told apart from the initial stop.
 *
 * The keys here are the engine's strings; renaming one server-side is a contract break,
 * adding one lands in the fallback branch below and still renders readably.
 */
import type { LabLocale } from "./trailingStop";

export const EXIT_REASON_ORDER = [
  "stop",
  "take",
  "trailing",
  "holding",
  "signal",
  "session",
] as const;

const LABELS: Record<string, Record<LabLocale, string>> = {
  stop: { ru: "стоп", en: "stop" },
  take: { ru: "тейк", en: "take" },
  trailing: { ru: "трейлинг", en: "trailing" },
  holding: { ru: "холдинг", en: "holding" },
  signal: { ru: "сигнал", en: "signal" },
  session: { ru: "сессия", en: "session" },
};

export function exitReasonLabel(reason: string, locale: LabLocale): string {
  const entry = LABELS[reason];
  if (entry) return entry[locale];
  return reason || "—";
}

/**
 * Tone by economic meaning, not by sign: the ladder raising the stop is a win over the
 * initial stop, so it must not inherit the red of a stopped-out trade.
 */
export function exitReasonTone(reason: string): string {
  switch (reason) {
    case "take":
      return "bg-emerald-500/15 text-emerald-300";
    case "trailing":
      return "bg-sky-500/15 text-sky-300";
    case "stop":
      return "bg-rose-500/15 text-rose-300";
    case "holding":
    case "signal":
      return "bg-amber-500/10 text-amber-300";
    default:
      return "bg-slate-700/40 text-slate-400";
  }
}
