import { useEffect, useRef, useState } from "react";

type JobStatus = "idle" | "running" | "done" | "failed";

interface Job {
  status: JobStatus;
  started_at: string | null;
  finished_at: string | null;
  elapsed_sec: number;
  tickers_count: number;
  total_signals_saved: number;
  total_candles_analyzed: number;
  errors_count: number;
  error: string | null;
}

const IDLE_JOB: Job = {
  status: "idle",
  started_at: null,
  finished_at: null,
  elapsed_sec: 0,
  tickers_count: 0,
  total_signals_saved: 0,
  total_candles_analyzed: 0,
  errors_count: 0,
  error: null,
};

function Spinner() {
  return (
    <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z" />
    </svg>
  );
}

function RecalcIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 12a9 9 0 1 1-2.64-6.36" />
      <path d="M21 3v6h-6" />
    </svg>
  );
}

export default function RegenerateWidget() {
  const [job, setJob] = useState<Job>(IDLE_JOB);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ kind: "done" | "failed"; text: string } | null>(null);
  const prevStatus = useRef<JobStatus>("idle");

  // Poll status while a job is running.
  useEffect(() => {
    if (job.status !== "running") return;
    const id = window.setInterval(async () => {
      try {
        const r = await fetch("/api/signals/regenerate/status");
        if (r.ok) setJob((await r.json()) as Job);
      } catch {
        /* ignore transient poll errors */
      }
    }, 2500);
    return () => window.clearInterval(id);
  }, [job.status]);

  // React to terminal transitions: show a toast, auto-dismiss it.
  useEffect(() => {
    const was = prevStatus.current;
    prevStatus.current = job.status;
    if (was === "running" && job.status === "done") {
      setToast({
        kind: "done",
        text: `Готово: ${job.total_signals_saved} сигналов за ${job.elapsed_sec}s. Нажмите «Обновить».`,
      });
    } else if (was === "running" && job.status === "failed") {
      setToast({ kind: "failed", text: `Ошибка пересчёта: ${job.error ?? "неизвестно"}` });
    }
  }, [job.status, job.total_signals_saved, job.elapsed_sec, job.error]);

  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(() => setToast(null), 7000);
    return () => window.clearTimeout(id);
  }, [toast]);

  async function start() {
    setBusy(true);
    setToast(null);
    try {
      const r = await fetch("/api/signals/regenerate", { method: "POST" });
      const data = await r.json().catch(() => ({}));
      if (r.status === 409) {
        setJob((data.job as Job) ?? { ...IDLE_JOB, status: "running" });
        setToast({ kind: "failed", text: "Пересчёт уже идёт." });
      } else if (!r.ok) {
        setToast({ kind: "failed", text: `Не удалось запустить: HTTP ${r.status}` });
      } else {
        setJob((data.job as Job) ?? { ...IDLE_JOB, status: "running" });
      }
    } catch (e) {
      setToast({ kind: "failed", text: `Сеть: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setBusy(false);
    }
  }

  const running = job.status === "running";
  const secs = Math.round(job.elapsed_sec || 0);

  return (
    <>
      {/* Result / error toast */}
      {toast && (
        <div
          className={
            "fixed bottom-20 right-6 z-50 max-w-sm rounded-lg border px-4 py-3 text-sm shadow-2xl " +
            "transition-all duration-300 animate-[fadeIn_.25s_ease-out] " +
            (toast.kind === "done"
              ? "border-emerald-600/60 bg-emerald-950/90 text-emerald-200"
              : "border-rose-600/60 bg-rose-950/90 text-rose-200")
          }
          role="status"
        >
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-base leading-none">{toast.kind === "done" ? "✓" : "!"}</span>
            <span>{toast.text}</span>
            <button onClick={() => setToast(null)} className="ml-2 opacity-60 hover:opacity-100" aria-label="Закрыть">×</button>
          </div>
        </div>
      )}

      {/* Floating control */}
      <div className="fixed bottom-6 right-6 z-40 flex flex-col items-end gap-2">
        <button
          onClick={start}
          disabled={running || busy}
          title="Пересчитать сигналы по паттернам в фоне (долго). «Обновить» в таблице показывает результат."
          className={
            "group relative flex items-center gap-2 rounded-full px-4 py-2.5 text-sm font-medium shadow-lg " +
            "transition-all duration-200 active:scale-95 disabled:cursor-not-allowed " +
            (running
              ? "border border-sky-500/60 bg-sky-900/80 text-sky-100"
              : "border border-sky-600 bg-sky-700 text-sky-50 hover:bg-sky-600 hover:shadow-sky-900/50")
          }
        >
          {/* pulse ring while running */}
          {running && (
            <span className="pointer-events-none absolute inset-0 rounded-full ring-2 ring-sky-400/50 animate-ping" />
          )}
          <span className="relative flex items-center gap-2">
            {running ? <Spinner /> : <RecalcIcon />}
            {running ? `Пересчёт… ${secs}s` : busy ? "Запуск…" : "Пересчитать сигналы"}
          </span>
        </button>

        {running && job.tickers_count > 0 && (
          <div className="rounded-md border border-slate-700 bg-slate-900/90 px-3 py-1 text-[11px] text-slate-400 shadow">
            Тикеров в очереди: {job.tickers_count} · таймфреймы 30m/1h/4h/1d
          </div>
        )}
      </div>

      <style>{`@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}`}</style>
    </>
  );
}
