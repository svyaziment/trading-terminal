import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { postPatternPreview } from "../api";
import type { PatternDef, PatternPreviewResponse } from "../types";
import CandleChart from "./CandleChart";

export interface PatternPreviewModalProps {
  def: PatternDef;
  draftParams: Record<string, unknown>;
  tickers: string[];
  dateFrom: string;
  dateTo: string;
  onClose: () => void;
}

function fmtPeriod(from: string, to: string): string {
  const fmt = (d: string) => d.split("-").reverse().join(".");
  return `${fmt(from)} — ${fmt(to)}`;
}

function statusBanner(preview: PatternPreviewResponse | null, fetchError: string | null) {
  if (fetchError) {
    return { tone: "error" as const, text: fetchError };
  }
  if (!preview) {
    return null;
  }
  if (preview.status === "empty") {
    return {
      tone: "warn" as const,
      text: preview.error ?? "Нет свечей для выбранного окна",
    };
  }
  if (preview.status === "error") {
    return {
      tone: "error" as const,
      text: preview.error ?? "Ошибка превью паттерна",
    };
  }
  if (preview.status === "unsupported") {
    return {
      tone: "info" as const,
      text:
        preview.error ??
        "Оверлеи для этого паттерна пока не реализованы — показаны только свечи",
    };
  }
  return null;
}

export default function PatternPreviewModal({
  def,
  draftParams,
  tickers,
  dateFrom,
  dateTo,
  onClose,
}: PatternPreviewModalProps) {
  const [ticker, setTicker] = useState(() => tickers[0] ?? "");
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PatternPreviewResponse | null>(null);

  useEffect(() => {
    if (tickers.length > 0 && !tickers.includes(ticker)) {
      setTicker(tickers[0]);
    }
  }, [tickers, ticker]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  useEffect(() => {
    if (!ticker) {
      setPreview(null);
      setFetchError("Выберите тикер в конструкторе Lab");
      return;
    }

    let cancelled = false;

    async function load() {
      setLoading(true);
      setFetchError(null);

      try {
        const result = await postPatternPreview({
          ticker,
          pattern_id: def.id,
          params: draftParams,
          date_from: dateFrom,
          date_to: dateTo,
        });
        if (!cancelled) {
          setPreview(result);
        }
      } catch (e) {
        if (!cancelled) {
          setPreview(null);
          setFetchError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [ticker, def.id, draftParams, dateFrom, dateTo]);

  const banner = useMemo(
    () => statusBanner(preview, fetchError),
    [preview, fetchError]
  );

  const candles = preview?.candles ?? [];
  const overlays = preview?.overlays ?? [];
  const metaLine = preview
    ? [
        preview.timeframe ? `TF ${preview.timeframe}` : null,
        fmtPeriod(dateFrom, dateTo),
        preview.status !== "ok" ? preview.status : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : fmtPeriod(dateFrom, dateTo);

  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/75 p-4"
      style={{ animation: "ppm-fade .18s ease-out" }}
      onClick={onClose}
    >
      <style>{`
 @keyframes ppm-fade { from { opacity:0 } to { opacity:1 } }
 @keyframes ppm-pop { from { opacity:0; transform: translateY(10px) scale(.97) } to { opacity:1; transform: translateY(0) scale(1) } }
`}</style>
      <div
        className="flex w-full max-w-4xl flex-col overflow-hidden rounded-lg border border-slate-700 bg-slate-900 shadow-2xl"
        style={{ animation: "ppm-pop .22s cubic-bezier(.2,.9,.3,1.15)", maxHeight: "92vh" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-slate-800 bg-slate-950/50 px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded border border-violet-700/50 bg-violet-500/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-widest text-violet-300">
                  preview
                </span>
                <h3 className="font-display text-sm font-semibold uppercase tracking-[0.12em] text-slate-100">
                  {def.label}
                </h3>
              </div>
              <p className="mt-1 font-mono text-[10px] text-slate-500">{metaLine}</p>
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

        <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 bg-slate-950/30 px-4 py-2.5">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Тикер</span>
          {tickers.length <= 8 ? (
            <div className="flex flex-wrap gap-1.5">
              {tickers.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setTicker(t)}
                  className={
                    "rounded border px-2 py-0.5 font-mono text-[11px] transition " +
                    (ticker === t
                      ? "border-sky-500/70 bg-sky-500/20 text-sky-200"
                      : "border-slate-700 bg-slate-900 text-slate-400 hover:border-slate-500")
                  }
                >
                  {t}
                </button>
              ))}
            </div>
          ) : (
            <select
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              className="rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-[11px] text-slate-200 outline-none focus:border-sky-500"
            >
              {tickers.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          )}
          {loading && (
            <span className="ml-auto text-[10px] text-slate-500">загрузка…</span>
          )}
        </div>

        {banner && (
          <div
            className={
              "border-b px-4 py-2 text-[11px] leading-relaxed " +
              (banner.tone === "error"
                ? "border-rose-800/60 bg-rose-500/10 text-rose-200"
                : banner.tone === "warn"
                  ? "border-amber-800/60 bg-amber-500/10 text-amber-200"
                  : "border-sky-800/60 bg-sky-500/10 text-sky-200")
            }
          >
            {banner.text}
          </div>
        )}

        <div className="min-h-[360px] flex-1 overflow-y-auto px-4 py-3">
          {!loading && candles.length === 0 ? (
            <div className="flex h-64 items-center justify-center text-sm text-slate-500">
              {fetchError ? "График недоступен" : "Нет свечей для отображения"}
            </div>
          ) : (
            <CandleChart candles={candles} overlays={overlays} height={400} />
          )}
        </div>

        <div className="flex justify-end border-t border-slate-800 bg-slate-950/50 px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-slate-600 px-3 py-1.5 text-[11px] font-medium text-slate-300 transition hover:bg-slate-800"
          >
            Закрыть
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
