import { useEffect, useMemo, useRef, useState } from "react";
import {
  saveStrategy,
  listStrategies,
  runStrategy,
  strategyResults,
  strategyRunStatus,
  getBigTickers,
} from "../api";
import type {
  Strategy,
  StrategyConfig,
  BacktestResultRow,
  FullSampleMetrics,
  WalkforwardMetrics,
} from "../types";

const PATTERNS = [
  { id: "levels_reversal", label: "Levels Reversal", hint: "цена в зоне 4h + подтверждение" },
  { id: "signal_4h_buy", label: "4h BUY signal", hint: "активный BUY из trading.signals" },
  { id: "rsi_oversold", label: "RSI oversold", hint: "RSI-14 < 30" },
  { id: "macd_bullish", label: "MACD bullish", hint: "гистограмма > 0" },
  { id: "bb_lower", label: "BB lower", hint: "close ниже нижней полосы" },
];
const WINDOWS = [1, 5, 10, 15, 20, 25, 30, 60, 90, 120];
const DEPTHS = [
  { id: "express", label: "Экспресс", hint: "6 мес · 3 тикера" },
  { id: "serious", label: "Серьёзный", hint: "6 мес · 15 · WF" },
  { id: "very_serious", label: "Полный", hint: "2 года · все · WF" },
];
const METHODS = [
  { id: "full_sample", label: "Full-sample" },
  { id: "walkforward", label: "Walk-forward" },
];
const WF_PERIODS = ["2024-H2", "2025-H1", "2025-H2", "2026-H1"];

const fmt = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d);

function pfTone(pf: number | null | undefined): string {
  if (pf === null || pf === undefined || Number.isNaN(pf)) return "text-slate-500";
  if (pf < 1.0) return "text-rose-400";
  if (pf < 1.3) return "text-amber-300";
  if (pf < 2.0) return "text-emerald-300";
  return "text-emerald-400 font-semibold";
}
function pfHeat(pf: number | null | undefined): string {
  if (pf === null || pf === undefined || Number.isNaN(pf))
    return "bg-slate-800/40 text-slate-500";
  if (pf < 1.0) return "bg-rose-500/15 text-rose-300";
  if (pf < 1.5) return "bg-amber-500/10 text-amber-300";
  if (pf < 2.5) return "bg-emerald-500/10 text-emerald-300";
  return "bg-emerald-500/20 text-emerald-200 font-semibold";
}

function Section(props: { title: string; badge?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
          {props.title}
        </h3>
        {props.badge && (
          <span className="rounded border border-sky-700/50 bg-sky-500/10 px-1.5 py-0.5 font-mono text-[10px] text-sky-300">
            {props.badge}
          </span>
        )}
      </div>
      {props.children}
    </section>
  );
}

function Chip(props: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
  title?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      title={props.title}
      disabled={props.disabled}
      onClick={props.onClick}
      className={
        "rounded border px-2 py-1 font-mono text-[11px] transition-all duration-150 active:scale-95 disabled:cursor-not-allowed " +
        (props.on
          ? "border-sky-500/70 bg-sky-500/20 text-sky-200 shadow-[0_0_10px_rgba(56,189,248,0.15)]"
          : "border-slate-700 bg-slate-900 text-slate-400 hover:border-slate-500 hover:text-slate-200")
      }
    >
      {props.children}
    </button>
  );
}

export default function StrategyLab() {
  const [name, setName] = useState("");
  const [patterns, setPatterns] = useState<string[]>(["levels_reversal", "signal_4h_buy"]);
  const [windows, setWindows] = useState<number[]>([10]);
  const [commission, setCommission] = useState("0.06");
  const [slippage, setSlippage] = useState("0");
  const [rrOn, setRrOn] = useState(true);
  const [rrRisk, setRrRisk] = useState("1");
  const [rrReward, setRrReward] = useState("2");
  const [methods, setMethods] = useState<string[]>(["full_sample", "walkforward"]);
  const [depth, setDepth] = useState("express");
  const [tickers, setTickers] = useState<string[]>(["RUAL", "GMKN", "PIKK"]);
  const [bigTickers, setBigTickers] = useState<string[]>([]);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [results, setResults] = useState<BacktestResultRow[]>([]);
  const [job, setJob] = useState<{ status?: string; tickers_total?: number; tickers_done?: number; current_ticker?: string | null; error?: string | null }>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState(false);
  const prevStatus = useRef<string | undefined>(undefined);

  const config: StrategyConfig = useMemo(
    () => ({
      patterns,
      confirm_windows: windows,
      commission_pct: parseFloat(commission) || 0,
      slippage_pct: parseFloat(slippage) || 0,
      risk_reward: rrOn
        ? { risk: parseFloat(rrRisk) || 1, reward: parseFloat(rrReward) || 2 }
        : null,
      n_runs: 1,
    }),
    [patterns, windows, commission, slippage, rrOn, rrRisk, rrReward]
  );

  const selectedStrategy = strategies.find((s) => s.id === selectedId) ?? null;
  const isLocked = selectedStrategy?.locked === true;

  async function reloadStrategies() {
    try {
      const r = await listStrategies();
      setStrategies(r.strategies);
    } catch { /* transient */ }
  }
  async function loadResults(id: number) {
    try {
      const r = await strategyResults(id);
      setResults(r.results);
    } catch { /* transient */ }
  }

  useEffect(() => {
    void (async () => {
      try {
        const bt = await getBigTickers();
        setBigTickers(bt.tickers);
      } catch { /* transient */ }
      await reloadStrategies();
    })();
  }, []);

  const running = job.status === "running";
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const s = await strategyRunStatus();
        if (alive) setJob(s);
      } catch { /* transient */ }
    };
    void tick();
    const id = window.setInterval(tick, running ? 2000 : 6000);
    return () => { alive = false; window.clearInterval(id); };
  }, [running]);

  useEffect(() => {
    if (prevStatus.current === "running" && job.status === "done" && selectedId !== null) {
      void loadResults(selectedId);
    }
    prevStatus.current = job.status;
  }, [job.status, selectedId]);

  function togglePattern(id: string) {
    setPatterns((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  }
  function toggleWindow(w: number) {
    setWindows((p) => (p.includes(w) ? p.filter((x) => x !== w) : [...p, w].sort((a, b) => a - b)));
  }
  function toggleMethod(id: string) {
    setMethods((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  }
  function toggleTicker(t: string) {
    setTickers((p) => (p.includes(t) ? p.filter((x) => x !== t) : [...p, t]));
  }

  function loadStrategy(s: Strategy) {
    setSelectedId(s.id);
    setName(s.name);
    const c = s.config;
    setPatterns(c.patterns ?? []);
    setWindows(c.confirm_windows ?? []);
    setCommission(String(c.commission_pct ?? 0.06));
    setSlippage(String(c.slippage_pct ?? 0));
    setRrOn(c.risk_reward !== null && c.risk_reward !== undefined);
    setRrRisk(String(c.risk_reward?.risk ?? 1));
    setRrReward(String(c.risk_reward?.reward ?? 2));
    setError(null);
    void loadResults(s.id);
  }

  function copyToNew() {
    if (!selectedStrategy) return;
    setName(selectedStrategy.name + "_v2");
    setSelectedId(null);
    setError(null);
  }

  async function handleSaveRun() {
    const nm = name.trim();
    if (!nm) { setError("Укажите имя стратегии"); return; }
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(nm)) { setError("Имя: английские буквы, цифры, _ и - (1-64)"); return; }
    if (patterns.length === 0) { setError("Выберите хотя бы один паттерн"); return; }
    if (windows.length === 0) { setError("Выберите хотя бы одно окно подтверждения"); return; }
    if (tickers.length === 0) { setError("Выберите хотя бы один тикер"); return; }
    if (methods.length === 0) { setError("Выберите хотя бы один метод теста"); return; }
    setBusy(true); setError(null);
    try {
      const saved = await saveStrategy({ name: nm, config });
      setSelectedId(saved.id);
      setFlash(true);
      window.setTimeout(() => setFlash(false), 1400);
      await runStrategy(saved.id, { tickers, test_types: methods, depth });
      await reloadStrategies();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const fullSample = useMemo(() => results.filter((r) => r.test_type === "full_sample"), [results]);
  const walkforward = useMemo(() => results.filter((r) => r.test_type === "walkforward"), [results]);

  const summary = useMemo(() => {
    const rows = fullSample.filter((r) => {
      const m = r.metrics as FullSampleMetrics | null;
      return m && m.pf !== null && m.pf !== undefined;
    });
    if (rows.length === 0) return null;
    const pfs = rows.map((r) => (r.metrics as FullSampleMetrics).pf as number);
    const totalN = rows.reduce((a, r) => a + ((r.metrics as FullSampleMetrics).n || 0), 0);
    const best = rows.reduce((a, b) =>
      ((a.metrics as FullSampleMetrics).pf as number) >= ((b.metrics as FullSampleMetrics).pf as number) ? a : b
    );
    return {
      avgPf: pfs.reduce((a, b) => a + b, 0) / pfs.length,
      totalN,
      bestTicker: best.ticker,
      bestPf: (best.metrics as FullSampleMetrics).pf as number,
      count: rows.length,
    };
  }, [fullSample]);

  const progressPct =
    (job.tickers_total ?? 0) > 0
      ? Math.round(((job.tickers_done ?? 0) / (job.tickers_total as number)) * 100)
      : 0;

  const numInput =
    "w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-xs text-slate-200 outline-none transition focus:border-sky-500 disabled:cursor-not-allowed";

  return (
    <div className="flex min-h-0 flex-1 gap-4">
      <style>{`
        @keyframes sl-shimmer { 0% { transform: translateX(-100%) } 100% { transform: translateX(260%) } }
        @keyframes sl-fade { from { opacity:0; transform: translateY(6px) } to { opacity:1; transform: translateY(0) } }
        @keyframes sl-glow { 0%,100% { box-shadow: 0 0 6px rgba(245,158,11,.25) } 50% { box-shadow: 0 0 14px rgba(245,158,11,.5) } }
      `}</style>

      {/* ================= CONFIG RAIL ================= */}
      <aside className="w-[330px] shrink-0 space-y-3 overflow-y-auto pb-4 pr-1">
        {/* Locked (paper trading) banner */}
        {isLocked && (
          <div
            className="rounded-lg border border-amber-600/60 bg-amber-500/10 p-3"
            style={{ animation: "sl-fade .2s ease-out, sl-glow 2.6s ease-in-out infinite" }}
          >
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2 shrink-0">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-400" />
              </span>
              <span className="font-display text-[11px] font-semibold uppercase tracking-[0.12em] text-amber-200">
                В paper trading · параметры заблокированы
              </span>
            </div>
            <p className="mt-1.5 text-[10px] leading-relaxed text-amber-200/70">
              Идёт тестовый период — конфигурация неизменяема, чтобы не исказить результаты теста.
              Чтобы поэкспериментировать, скопируйте в новую стратегию.
            </p>
            <button
              type="button"
              onClick={copyToNew}
              className="mt-2 w-full rounded border border-amber-600/70 bg-amber-500/20 px-2 py-1.5 font-mono text-[11px] font-medium text-amber-200 transition hover:bg-amber-500/30 active:scale-[0.98]"
            >
              ⧉ Скопировать в новую стратегию
            </button>
          </div>
        )}

        {/* Config sections: read-only when locked */}
        <div className={isLocked ? "space-y-3 select-none opacity-50" : "space-y-3"} aria-disabled={isLocked}>
          <Section title="Стратегия" badge={flash ? "сохранено ✓" : isLocked ? "🔒 только чтение" : undefined}>
            <input
              value={name}
              readOnly={isLocked}
              onChange={(e) => setName(e.target.value)}
              placeholder="name_in_english"
              className={
                "w-full rounded border bg-slate-950 px-2.5 py-1.5 font-mono text-sm text-slate-100 outline-none transition placeholder:text-slate-600 disabled:cursor-not-allowed " +
                (name && !/^[A-Za-z0-9_-]{0,64}$/.test(name)
                  ? "border-rose-600 focus:border-rose-500"
                  : "border-slate-700 focus:border-sky-500")
              }
            />
            <p className="mt-1 text-[10px] text-slate-600">латиница, цифры, «_», «-»</p>
            {selectedStrategy?.description && (
              <p className="mt-1.5 rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5 text-[10px] leading-relaxed text-slate-400">
                {selectedStrategy.description}
              </p>
            )}
          </Section>

          <Section title="Паттерны" badge={patterns.length > 1 ? "AND" : String(patterns.length)}>
            <div className="space-y-1.5">
              {PATTERNS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  disabled={isLocked}
                  onClick={() => togglePattern(p.id)}
                  className={
                    "flex w-full items-center gap-2 rounded border px-2 py-1.5 text-left transition-all duration-150 disabled:cursor-not-allowed " +
                    (patterns.includes(p.id)
                      ? "border-sky-500/70 bg-sky-500/15"
                      : "border-slate-800 bg-slate-950/60 hover:border-slate-600")
                  }
                >
                  <span
                    className={
                      "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm border font-mono text-[9px] transition " +
                      (patterns.includes(p.id)
                        ? "border-sky-400 bg-sky-500 text-slate-950"
                        : "border-slate-600 text-transparent")
                    }
                  >
                    ✓
                  </span>
                  <span className="min-w-0">
                    <span className={"block truncate font-mono text-[11px] " + (patterns.includes(p.id) ? "text-sky-200" : "text-slate-300")}>
                      {p.label}
                    </span>
                    <span className="block truncate text-[10px] text-slate-600">{p.hint}</span>
                  </span>
                </button>
              ))}
            </div>
            {patterns.length > 1 && (
              <p className="mt-2 rounded border border-amber-700/40 bg-amber-500/10 px-2 py-1 text-[10px] text-amber-200">
                сигнал только при совместном срабатывании всех ({patterns.length})
              </p>
            )}
          </Section>

          <Section title="Окна подтверждения" badge={windows.length > 1 ? "AND" : String(windows.length)}>
            <div className="flex flex-wrap gap-1.5">
              {WINDOWS.map((w) => (
                <Chip key={w} on={windows.includes(w)} disabled={isLocked} onClick={() => toggleWindow(w)} title={`${w} мин`}>
                  {w}
                </Chip>
              ))}
            </div>
            <p className="mt-2 text-[10px] text-slate-600">минуты · закрытая свеча выше зоны</p>
          </Section>

          <Section title="Издержки">
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">комиссия %</span>
                <input value={commission} readOnly={isLocked} onChange={(e) => setCommission(e.target.value)} className={numInput} />
              </label>
              <label className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">проскальз. %</span>
                <input value={slippage} readOnly={isLocked} onChange={(e) => setSlippage(e.target.value)} className={numInput} />
              </label>
            </div>
          </Section>

          <Section title="Risk / Reward" badge={rrOn ? `${rrRisk}:${rrReward}` : "off"}>
            <label className="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
              <input type="checkbox" checked={rrOn} disabled={isLocked} onChange={(e) => setRrOn(e.target.checked)} />
              фильтровать входы
            </label>
            {rrOn && (
              <div className="grid grid-cols-2 gap-2">
                <label className="block">
                  <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">risk</span>
                  <input value={rrRisk} readOnly={isLocked} onChange={(e) => setRrRisk(e.target.value)} className={numInput} />
                </label>
                <label className="block">
                  <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">reward</span>
                  <input value={rrReward} readOnly={isLocked} onChange={(e) => setRrReward(e.target.value)} className={numInput} />
                </label>
              </div>
            )}
          </Section>

          <Section title="Тест">
            <div className="mb-2 flex flex-wrap gap-1.5">
              {METHODS.map((m) => (
                <Chip key={m.id} on={methods.includes(m.id)} disabled={isLocked} onClick={() => toggleMethod(m.id)}>
                  {m.label}
                </Chip>
              ))}
            </div>
            <div className="mb-2 grid grid-cols-3 gap-1.5">
              {DEPTHS.map((d) => (
                <button
                  key={d.id}
                  type="button"
                  disabled={isLocked}
                  onClick={() => setDepth(d.id)}
                  title={d.hint}
                  className={
                    "rounded border px-1.5 py-1 text-[10px] transition disabled:cursor-not-allowed " +
                    (depth === d.id
                      ? "border-sky-500/70 bg-sky-500/20 text-sky-200"
                      : "border-slate-700 text-slate-400 hover:border-slate-500")
                  }
                >
                  {d.label}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap gap-1">
              {bigTickers.map((t) => (
                <Chip key={t} on={tickers.includes(t)} disabled={isLocked} onClick={() => toggleTicker(t)}>
                  {t}
                </Chip>
              ))}
            </div>
            <div className="mt-1.5 flex items-center justify-between text-[10px] text-slate-600">
              <span>выбрано: {tickers.length}</span>
              <button
                type="button"
                disabled={isLocked}
                className="text-sky-400 transition hover:text-sky-300 disabled:cursor-not-allowed"
                onClick={() => setTickers(tickers.length === bigTickers.length ? [] : [...bigTickers])}
              >
                {tickers.length === bigTickers.length ? "сбросить" : "все"}
              </button>
            </div>
          </Section>
        </div>

        <button
          type="button"
          onClick={() => void handleSaveRun()}
          disabled={busy || running || isLocked}
          title={isLocked ? "Стратегия в paper trading — параметры заблокированы" : undefined}
          className={
            "w-full rounded-md border px-3 py-2.5 font-display text-sm font-semibold tracking-wide transition-all duration-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 " +
            (flash
              ? "border-emerald-500 bg-emerald-500/20 text-emerald-200"
              : "border-sky-500 bg-sky-600 text-white shadow-[0_0_18px_rgba(2,132,199,0.35)] hover:bg-sky-500")
          }
        >
          {isLocked
            ? "🔒 заблокировано (paper trading)"
            : busy
              ? "сохранение…"
              : running
                ? "тест идёт…"
                : flash
                  ? "✓ сохранено"
                  : "Сохранить и запустить"}
        </button>

        {error && (
          <div className="rounded border border-rose-700 bg-rose-500/10 px-2.5 py-1.5 text-[11px] leading-relaxed text-rose-300" style={{ animation: "sl-fade .2s ease-out" }}>
            {error}
          </div>
        )}

        {strategies.length > 0 && (
          <Section title="Сохранённые" badge={String(strategies.length)}>
            <div className="space-y-1">
              {strategies.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => loadStrategy(s)}
                  className={
                    "flex w-full items-center justify-between gap-2 rounded border px-2 py-1.5 text-left transition " +
                    (selectedId === s.id
                      ? "border-sky-500/70 bg-sky-500/15"
                      : "border-slate-800 bg-slate-950/60 hover:border-slate-600")
                  }
                >
                  <span className={"truncate font-mono text-[11px] " + (selectedId === s.id ? "text-sky-200" : "text-slate-300")}>
                    {s.name}
                  </span>
                  <span className="flex shrink-0 items-center gap-1">
                    {s.in_paper_test && (
                      <span
                        className="inline-flex items-center gap-1 rounded border border-amber-600/60 bg-amber-500/15 px-1.5 py-0.5 font-mono text-[8px] uppercase tracking-wider text-amber-300"
                        title="Тестируется в paper trading"
                      >
                        <span className="relative flex h-1 w-1">
                          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-60" />
                          <span className="relative inline-flex h-1 w-1 rounded-full bg-amber-400" />
                        </span>
                        paper
                      </span>
                    )}
                    {s.locked && (
                      <span className="rounded border border-slate-600 bg-slate-800 px-1 py-0.5 font-mono text-[9px] text-slate-400" title="Параметры заблокированы до конца теста">
                        🔒
                      </span>
                    )}
                    <span className="font-mono text-[9px] text-slate-600">#{s.id}</span>
                  </span>
                </button>
              ))}
            </div>
          </Section>
        )}
      </aside>

      {/* ================= RESULTS ================= */}
      <main
        className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pb-6 pr-1"
        style={{
          backgroundImage: "radial-gradient(circle, rgba(148,163,184,0.06) 1px, transparent 1px)",
          backgroundSize: "22px 22px",
        }}
      >
        {/* Job status */}
        {running && (
          <div className="rounded-lg border border-sky-700/50 bg-sky-500/10 p-3" style={{ animation: "sl-fade .2s ease-out" }}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400 opacity-70" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-sky-400" />
                </span>
                <span className="font-display text-xs font-semibold uppercase tracking-wider text-sky-200">
                  Бэктест выполняется
                </span>
                {job.current_ticker && (
                  <span className="font-mono text-[11px] text-sky-300">{job.current_ticker}</span>
                )}
              </div>
              <span className="font-mono text-[11px] tabular-nums text-sky-300">
                {job.tickers_done ?? 0}/{job.tickers_total ?? "—"}
              </span>
            </div>
            <div className="relative mt-2 h-1.5 overflow-hidden rounded-full bg-slate-800">
              <div
                className="h-full rounded-full bg-gradient-to-r from-sky-600 to-sky-400 transition-[width] duration-500"
                style={{ width: `${progressPct}%` }}
              />
              <div
                className="absolute inset-y-0 w-1/4 bg-sky-300/30"
                style={{ animation: "sl-shimmer 1.2s linear infinite" }}
              />
            </div>
          </div>
        )}
        {job.status === "failed" && (
          <div className="rounded-lg border border-rose-700 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
            Ошибка джобы: {job.error ?? "неизвестно"}
          </div>
        )}

        {/* Summary strip */}
        {summary && (
          <div className="flex flex-wrap items-end gap-x-8 gap-y-3 rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3" style={{ animation: "sl-fade .25s ease-out" }}>
            <div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">средний PF</div>
              <div className={"font-display text-3xl font-bold tabular-nums " + pfTone(summary.avgPf)}>
                {fmt(summary.avgPf)}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">сделок</div>
              <div className="font-display text-3xl font-bold tabular-nums text-slate-100">{summary.totalN}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">лучший</div>
              <div className="font-display text-xl font-semibold text-slate-100">
                {summary.bestTicker}
                <span className={"ml-2 font-mono text-base " + pfTone(summary.bestPf)}>{fmt(summary.bestPf)}</span>
              </div>
            </div>
            <div className="ml-auto text-right">
              <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">тикеров</div>
              <div className="font-mono text-lg text-slate-300">{summary.count}</div>
            </div>
          </div>
        )}

        {/* Full-sample table */}
        <div className="overflow-hidden rounded-lg border border-slate-800">
          <div className="flex items-center justify-between border-b border-slate-800 bg-slate-900 px-3 py-2">
            <span className="font-display text-xs font-semibold uppercase tracking-[0.14em] text-slate-300">
              Бэктест · full-sample
            </span>
            <span className="font-mono text-[10px] text-slate-600">{fullSample.length} тикеров</span>
          </div>
          {fullSample.length === 0 ? (
            <div className="px-3 py-6 text-center text-xs text-slate-600">
              нет результатов — сохраните и запустите стратегию
            </div>
          ) : (
            <table className="min-w-full text-sm">
              <thead className="bg-slate-900/80 text-[10px] uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="px-3 py-2 text-left">Тикер</th>
                  <th className="px-3 py-2 text-right">n</th>
                  <th className="px-3 py-2 text-right">PF</th>
                  <th className="px-3 py-2 text-right">Exp %</th>
                  <th className="px-3 py-2 text-right">WR</th>
                  <th className="px-3 py-2 text-right">MaxDD %</th>
                </tr>
              </thead>
              <tbody>
                {fullSample.map((r) => {
                  const m = (r.metrics ?? {}) as FullSampleMetrics;
                  return (
                    <tr key={r.id} className="border-t border-slate-800/70 transition-colors hover:bg-slate-900/70">
                      <td className="px-3 py-1.5 font-mono font-medium text-slate-200">{r.ticker}</td>
                      <td className="px-3 py-1.5 text-right font-mono tabular-nums text-slate-400">{m.n ?? "—"}</td>
                      <td className={"px-3 py-1.5 text-right font-mono tabular-nums " + pfTone(m.pf)}>{fmt(m.pf)}</td>
                      <td className={"px-3 py-1.5 text-right font-mono tabular-nums " + ((m.exp_pct ?? 0) >= 0 ? "text-emerald-300" : "text-rose-400")}>
                        {(m.exp_pct ?? 0) >= 0 ? "+" : ""}{fmt(m.exp_pct, 3)}
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono tabular-nums text-slate-300">{fmt(m.wr, 1)}</td>
                      <td className="px-3 py-1.5 text-right font-mono tabular-nums text-amber-300/90">{fmt(m.maxdd_pct, 1)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* Walk-forward table */}
        <div className="overflow-hidden rounded-lg border border-slate-800">
          <div className="flex items-center justify-between border-b border-slate-800 bg-slate-900 px-3 py-2">
            <span className="font-display text-xs font-semibold uppercase tracking-[0.14em] text-slate-300">
              Walk-forward · по полугодиям
            </span>
            <span className="font-mono text-[10px] text-slate-600">{walkforward.length} тикеров</span>
          </div>
          {walkforward.length === 0 ? (
            <div className="px-3 py-6 text-center text-xs text-slate-600">
              нет результатов walk-forward
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="bg-slate-900/80 text-[10px] uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="px-3 py-2 text-left">Тикер</th>
                    {WF_PERIODS.map((p) => (
                      <th key={p} className="px-2 py-2 text-right">{p}</th>
                    ))}
                    <th className="px-2 py-2 text-right">PF&gt;1</th>
                    <th className="px-2 py-2 text-right">min</th>
                    <th className="px-2 py-2 text-right">avg</th>
                  </tr>
                </thead>
                <tbody>
                  {walkforward.map((r) => {
                    const m = (r.metrics ?? {}) as WalkforwardMetrics;
                    const periods = m.periods ?? {};
                    return (
                      <tr key={r.id} className="border-t border-slate-800/70 transition-colors hover:bg-slate-900/70">
                        <td className="px-3 py-1.5 font-mono font-medium text-slate-200">{r.ticker}</td>
                        {WF_PERIODS.map((p) => {
                          const pf = periods[p]?.pf ?? null;
                          return (
                            <td key={p} className="px-1.5 py-1.5 text-right">
                              <span className={"inline-block min-w-[3.2rem] rounded px-1.5 py-0.5 text-center font-mono text-[11px] tabular-nums " + pfHeat(pf)}>
                                {fmt(pf)}
                              </span>
                            </td>
                          );
                        })}
                        <td className="px-2 py-1.5 text-right font-mono text-[11px] tabular-nums text-slate-300">{m.pf_gt1 ?? "—"}</td>
                        <td className={"px-2 py-1.5 text-right font-mono text-[11px] tabular-nums " + pfTone(m.min_pf)}>{fmt(m.min_pf)}</td>
                        <td className={"px-2 py-1.5 text-right font-mono text-[11px] tabular-nums " + pfTone(m.avg_pf)}>{fmt(m.avg_pf)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
