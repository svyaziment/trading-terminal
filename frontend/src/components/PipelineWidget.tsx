import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

type OpKey = "refresh" | "regenerate";

interface JobSnap {
  status?: string;
  stage?: string;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  tickers_total?: number;
  tickers_done?: number;
  tickers_count?: number;
  raw_inserted?: number;
  total_signals_saved?: number;
  total_candles_analyzed?: number;
  days?: number;
  errors_count?: number;
  current_ticker?: string | null;
}

type JobsMap = Partial<Record<OpKey, JobSnap>>;

interface Toast { id: number; kind: "done" | "failed" | "warn"; text: string; }

const REFRESH_STAGES: Array<{ key: string; label: string }> = [
  { key: "loading", label: "Загрузка свечей" },
  { key: "aggregating", label: "Агрегация" },
  { key: "indicators", label: "Индикаторы" },
  { key: "signals", label: "Сигналы" },
  { key: "done", label: "Готово" },
];

function stageIndex(stage?: string): number {
  if (!stage) return -1;
  if (stage === "starting") return 0;
  const i = REFRESH_STAGES.findIndex((s) => s.key === stage);
  return i < 0 ? -1 : i;
}

function statusTone(status?: string): string {
  switch (status) {
    case "running": return "text-sky-300 border-sky-600/60 bg-sky-500/10";
    case "done": return "text-emerald-300 border-emerald-600/60 bg-emerald-500/10";
    case "failed": return "text-rose-300 border-rose-600/60 bg-rose-500/10";
    default: return "text-slate-400 border-slate-700 bg-slate-800/40";
  }
}

export default function PipelineWidget() {
  const [jobs, setJobs] = useState<JobsMap>({});
  const [expanded, setExpanded] = useState(false);
  const [days, setDays] = useState(24);
  const [busy, setBusy] = useState<Record<OpKey, boolean>>({ refresh: false, regenerate: false });
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [now, setNow] = useState(() => Date.now());

  const prevStatuses = useRef<Partial<Record<OpKey, string>>>({});
  const toastId = useRef(0);

  const refresh = jobs.refresh;
  const regenerate = jobs.regenerate;
  const anyRunning = refresh?.status === "running" || regenerate?.status === "running";

  // Live clock — ticks only while something runs, so idle UI stays still.
  useEffect(() => {
    if (!anyRunning) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [anyRunning]);

  // Poll the shared jobs view. Faster while busy = more responsive feedback.
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await fetch("/api/jobs/status");
        if (r.ok && alive) setJobs((await r.json()) as JobsMap);
      } catch { /* transient */ }
    };
    void tick();
    const interval = anyRunning ? 1800 : 4000;
    const id = window.setInterval(tick, interval);
    return () => { alive = false; window.clearInterval(id); };
  }, [anyRunning]);

  function addToast(kind: Toast["kind"], text: string) {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, kind, text }].slice(-3));
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 7000);
  }

  // Detect terminal transitions -> toast with the final numbers.
  useEffect(() => {
    (Object.keys(jobs) as OpKey[]).forEach((k) => {
      const cur = jobs[k]?.status;
      const prev = prevStatuses.current[k];
      if (prev === "running" && cur === "done") {
        const j = jobs[k]!;
        const n = j.total_signals_saved ?? 0;
        const label = k === "refresh" ? "Обновление данных" : "Пересчёт сигналов";
        addToast("done", `${label}: готово, сигналов ${n}`);
      } else if (prev === "running" && cur === "failed") {
        const label = k === "refresh" ? "Обновление данных" : "Пересчёт сигналов";
        addToast("failed", `${label}: ошибка — ${jobs[k]?.error ?? "неизвестно"}`);
      }
      prevStatuses.current[k] = cur;
    });
  }, [jobs]);

  function elapsedSec(started?: string | null): number {
    if (!started) return 0;
    const t = Date.parse(started);
    if (Number.isNaN(t)) return 0;
    return Math.max(0, Math.floor((now - t) / 1000));
  }

  async function startRefresh() {
    setBusy((b) => ({ ...b, refresh: true }));
    try {
      const r = await fetch(`/api/data/refresh?days=${days}`, { method: "POST" });
      if (r.status === 409) addToast("warn", "Система занята другой операцией.");
      else if (!r.ok) addToast("failed", `Не удалось запустить: HTTP ${r.status}`);
    } catch (e) {
      addToast("failed", `Сеть: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy((b) => ({ ...b, refresh: false }));
    }
  }

  async function startRegenerate() {
    setBusy((b) => ({ ...b, regenerate: true }));
    try {
      const r = await fetch("/api/signals/regenerate", { method: "POST" });
      if (r.status === 409) addToast("warn", "Система занята другой операцией.");
      else if (!r.ok) addToast("failed", `Не удалось запустить: HTTP ${r.status}`);
    } catch (e) {
      addToast("failed", `Сеть: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy((b) => ({ ...b, regenerate: false }));
    }
  }

  const activeStageLabel = useMemo(() => {
    if (refresh?.status === "running") {
      const i = stageIndex(refresh.stage);
      return i >= 0 ? REFRESH_STAGES[i].label : "старт";
    }
    if (regenerate?.status === "running") return "генерация сигналов";
    return "";
  }, [refresh, regenerate]);

  const pillElapsed = useMemo(() => {
    if (refresh?.status === "running") return elapsedSec(refresh.started_at);
    if (regenerate?.status === "running") return elapsedSec(regenerate.started_at);
    return 0;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, regenerate, now]);

  const refreshActive = stageIndex(refresh?.stage);
  const refreshPct = refresh?.status === "done"
    ? 100
    : (refresh?.tickers_total ?? 0) > 0
      ? Math.round(((refresh?.tickers_done ?? 0) / (refresh?.tickers_total ?? 1)) * 100)
      : 0;

  return createPortal(
    <>
      <style>{`
        @keyframes pt-glow { 0%,100% { opacity:.35 } 50% { opacity:.7 } }
        @keyframes pt-shimmer { 0% { transform: translateX(-100%) } 100% { transform: translateX(220%) } }
        @keyframes pt-fade { from { opacity:0; transform: translateY(8px) } to { opacity:1; transform: translateY(0) } }
        @keyframes pt-pop { from { opacity:0; transform: scale(.96) } to { opacity:1; transform: scale(1) } }
      `}</style>

      {/* Toast stack */}
      <div className="fixed bottom-24 right-6 z-50 flex w-80 flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            style={{ animation: "pt-fade .25s ease-out" }}
            className={
              "rounded-lg border px-3 py-2 text-sm shadow-2xl " +
              (t.kind === "done" ? "border-emerald-600/60 bg-emerald-950/90 text-emerald-200"
                : t.kind === "warn" ? "border-amber-600/60 bg-amber-950/90 text-amber-200"
                : "border-rose-600/60 bg-rose-950/90 text-rose-200")
            }
            role="status"
          >
            <span className="mr-1 font-mono">{t.kind === "done" ? "✓" : t.kind === "warn" ? "!" : "✕"}</span>
            {t.text}
          </div>
        ))}
      </div>

      {/* Collapsed pill */}
      {!expanded && (
        <button
          onClick={() => setExpanded(true)}
          className="group fixed bottom-6 right-6 z-40 flex items-center gap-2 rounded-full border border-slate-700 bg-slate-900/90 px-4 py-2.5 text-sm shadow-xl backdrop-blur-md transition-all duration-200 hover:border-sky-600 hover:bg-slate-800 active:scale-95"
        >
          {anyRunning && (
            <span className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400 opacity-70" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-sky-400" />
            </span>
          )}
          {!anyRunning && (
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-slate-400 transition-colors group-hover:text-sky-300">
              <path d="M3 12a9 9 0 0 1 9-9 9 9 0 0 1 6.7 3M21 12a9 9 0 0 1-9 9 9 9 0 0 1-6.7-3" />
              <path d="M21 3v5h-5M3 21v-5h5" />
            </svg>
          )}
          <span className="font-medium text-slate-200">
            {anyRunning ? `Пайплайн · ${activeStageLabel}` : "Пайплайн данных"}
          </span>
          {anyRunning && (
            <span className="font-mono text-xs tabular-nums text-sky-300">{pillElapsed}s</span>
          )}
        </button>
      )}

      {/* Expanded panel */}
      {expanded && (
        <div
          style={{ animation: "pt-pop .18s ease-out" }}
          className="fixed bottom-6 right-6 z-40 w-[380px] overflow-hidden rounded-xl border border-slate-700 bg-slate-900/85 shadow-2xl backdrop-blur-md"
        >
          <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
            <div>
              <div className="text-sm font-semibold text-slate-100">Пайплайн данных</div>
              <div className="text-[11px] uppercase tracking-wider text-slate-500">фоновые операции</div>
            </div>
            <button onClick={() => setExpanded(false)} className="rounded p-1 text-slate-500 transition hover:bg-slate-800 hover:text-slate-200" aria-label="Свернуть">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 9l6 6 6-6" /></svg>
            </button>
          </div>

          {anyRunning && (
            <div className="flex items-center gap-2 border-b border-amber-700/40 bg-amber-500/10 px-4 py-2 text-[11px] text-amber-200">
              <span className="font-mono">⚠</span> Выполняется операция — вторая кнопка заблокирована общим локом.
            </div>
          )}

          <div className="max-h-[70vh] space-y-3 overflow-y-auto p-3">
            {/* Refresh from market */}
            <OpCard
              title="Обновить данные с рынка"
              snap={refresh}
              now={now}
              elapsed={elapsedSec(refresh?.started_at)}
              glow={refresh?.status === "running"}
              controls={
                <label className="flex items-center gap-2 text-[11px] text-slate-400">
                  <span className="uppercase tracking-wider">дней</span>
                  <input
                    type="number" min={1} max={365} value={days}
                    disabled={anyRunning}
                    onChange={(e) => setDays(Math.min(365, Math.max(1, Number(e.target.value) || 1)))}
                    className="w-16 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-center font-mono text-xs text-slate-200 outline-none focus:border-sky-500 disabled:opacity-40"
                  />
                </label>
              }
              stages={REFRESH_STAGES.map((s, i) => ({
                label: s.label,
                state: refresh?.status === "done" ? "done"
                  : i < refreshActive ? "done"
                  : i === refreshActive && refresh?.status === "running" ? "active" : "idle",
              }))}
              progress={refresh?.status === "running" ? refreshPct : refresh?.status === "done" ? 100 : 0}
              indeterminate={false}
              metrics={`${refresh?.tickers_done ?? 0}/${refresh?.tickers_total ?? 0} тикеров · raw ${refresh?.raw_inserted ?? 0} · сигналов ${refresh?.total_signals_saved ?? 0}`}
              onStart={startRefresh}
              disabled={anyRunning || busy.refresh}
              busy={busy.refresh}
            />

            {/* Regenerate signals */}
            <OpCard
              title="Пересчитать сигналы"
              snap={regenerate}
              now={now}
              elapsed={elapsedSec(regenerate?.started_at)}
              glow={regenerate?.status === "running"}
              stages={[{
                label: "Генерация сигналов",
                state: regenerate?.status === "done" ? "done"
                  : regenerate?.status === "running" ? "active" : "idle",
              }]}
              progress={regenerate?.status === "done" ? 100 : 0}
              indeterminate={regenerate?.status === "running"}
              metrics={`тикеров ${regenerate?.tickers_count ?? 0} · свечей ${regenerate?.total_candles_analyzed ?? 0} · сигналов ${regenerate?.total_signals_saved ?? 0}`}
              onStart={startRegenerate}
              disabled={anyRunning || busy.regenerate}
              busy={busy.regenerate}
            />
          </div>

          <div className="border-t border-slate-800 px-4 py-2 text-[10px] text-slate-600">
            «Обновить» в таблице сигналов по-прежнему читает БД мгновенно и независимо от этого пайплайна.
          </div>
        </div>
      )}
    </>,
    document.body,
  );
}

interface StageView { label: string; state: "done" | "active" | "idle"; }

function OpCard(props: {
  title: string;
  snap?: JobSnap;
  now: number;
  elapsed: number;
  glow: boolean;
  controls?: React.ReactNode;
  stages: StageView[];
  progress: number;
  indeterminate: boolean;
  metrics: string;
  onStart: () => void;
  disabled: boolean;
  busy: boolean;
}) {
  const { title, snap, elapsed, glow, controls, stages, progress, indeterminate, metrics, onStart, disabled, busy } = props;
  const status = snap?.status ?? "idle";
  const running = status === "running";

  return (
    <div className="relative overflow-hidden rounded-lg border border-slate-800 bg-slate-950/60 p-3">
      {glow && (
        <div
          className="pointer-events-none absolute -inset-px rounded-lg"
          style={{ animation: "pt-glow 2.4s ease-in-out infinite", background: "radial-gradient(120% 80% at 50% 0%, rgba(56,189,248,.18), transparent 70%)" }}
        />
      )}
      <div className="relative flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[13px] font-medium text-slate-100">{title}</div>
          {running && snap?.current_ticker && (
            <div className="truncate font-mono text-[10px] text-slate-500">{snap.current_ticker}</div>
          )}
        </div>
        <span className={"shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider " + statusTone(status)}>
          {running ? (snap?.stage ?? "run") : status}
        </span>
      </div>

      {/* Stage rail */}
      <div className="relative mt-3 flex items-center gap-1">
        {stages.map((s, i) => (
          <div key={i} className="flex min-w-0 flex-1 flex-col items-center gap-1">
            <div className={"h-1 w-full rounded-full transition-colors duration-300 " + (
              s.state === "done" ? "bg-emerald-500/70"
                : s.state === "active" ? "bg-sky-500/70" : "bg-slate-800"
            )} />
            <span className={"truncate text-[9px] uppercase tracking-wide " + (
              s.state === "active" ? "text-sky-300" : s.state === "done" ? "text-emerald-400/80" : "text-slate-600"
            )}>{s.label}</span>
          </div>
        ))}
      </div>

      {/* Progress bar */}
      <div className="relative mt-2 h-1.5 overflow-hidden rounded-full bg-slate-800">
        {indeterminate ? (
          <div className="absolute inset-y-0 w-1/3 rounded-full bg-sky-500/70" style={{ animation: "pt-shimmer 1.1s linear infinite" }} />
        ) : (
          <div className="h-full rounded-full bg-gradient-to-r from-sky-600 to-sky-400 transition-[width] duration-500" style={{ width: `${progress}%` }} />
        )}
      </div>

      {/* Metrics + timer */}
      <div className="relative mt-2 flex items-center justify-between gap-2">
        <span className="truncate font-mono text-[10px] tabular-nums text-slate-500">{metrics}</span>
        {running && <span className="shrink-0 font-mono text-[11px] tabular-nums text-sky-300">{elapsed}s</span>}
      </div>

      {/* Controls row */}
      <div className="relative mt-3 flex items-center justify-between gap-2">
        <div>{controls ?? <span />}</div>
        <button
          onClick={onStart}
          disabled={disabled}
          className={
            "rounded-md px-3 py-1.5 text-xs font-medium transition-all duration-200 active:scale-95 disabled:cursor-not-allowed disabled:opacity-40 " +
            (running
              ? "border border-sky-600/60 bg-sky-900/60 text-sky-200"
              : "border border-sky-600 bg-sky-700 text-sky-50 hover:bg-sky-600")
          }
        >
          {busy ? "запуск…" : running ? "идёт…" : "запустить"}
        </button>
      </div>
    </div>
  );
}
