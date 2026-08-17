import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ColorType, LineStyle, createChart } from "lightweight-charts";
import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";

import {
  getLivePositions,
  getNotificationStatus,
  getPaperDynamics,
} from "../api";
import type {
  DynamicsPoint,
  LivePosition,
  NotificationStatus,
} from "../types";

const CLOSED_STATUS_OPTIONS = [
  { value: "closed", label: "Все закрытые" },
  { value: "closed_take", label: "Take profit" },
  { value: "closed_stop", label: "Stop loss" },
  { value: "cancelled", label: "Отменённые" },
];
const PAGE_SIZES = [20, 50, 100];
const POLL_INTERVAL_MS = 10_000;

const fmtNum = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined || Number.isNaN(value)
    ? "—"
    : Number(value).toFixed(digits);

const fmtPct = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `${fmtNum(value, 2)}%`;

const fmtTime = (value: string | null | undefined) =>
  value ? value.replace("T", " ").slice(0, 16) : "—";

const pnlClass = (value: number | null | undefined) => {
  if (value === null || value === undefined) return "text-slate-500";
  return value >= 0 ? "text-emerald-400" : "text-rose-400";
};

const statusClass = (status: string) => {
  if (status === "open") return "border-sky-600/60 bg-sky-500/15 text-sky-300";
  if (status === "closed_take") return "border-emerald-600/60 bg-emerald-500/15 text-emerald-300";
  if (status === "closed_stop") return "border-rose-600/60 bg-rose-500/15 text-rose-300";
  if (status === "cancelled") return "border-slate-600 bg-slate-800 text-slate-400";
  return "border-amber-600/60 bg-amber-500/15 text-amber-300";
};

const statusLabel = (status: string) => {
  if (status === "open") return "открыта";
  if (status === "closed_take") return "take";
  if (status === "closed_stop") return "stop";
  if (status === "cancelled") return "отменена";
  return status;
};

const toChartTime = (timestamp: string, timeframe: "1h" | "1d"): Time => {
  if (timeframe === "1d") return timestamp.slice(0, 10) as Time;
  const date = new Date(timestamp.replace(" ", "T") + "Z");
  return Math.floor(date.getTime() / 1000) as Time;
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex rounded border px-2 py-0.5 font-mono text-[10px] ${statusClass(status)}`}>
      {statusLabel(status)}
    </span>
  );
}

function SortButton({
  field,
  label,
  sortBy,
  sortDir,
  onSort,
}: {
  field: string;
  label: string;
  sortBy: string;
  sortDir: "asc" | "desc";
  onSort: (field: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSort(field)}
      className="whitespace-nowrap text-left transition hover:text-sky-300"
    >
      {label} {sortBy === field ? (sortDir === "asc" ? "↑" : "↓") : ""}
    </button>
  );
}

export default function LiveTradingPanel() {
  const [openPositions, setOpenPositions] = useState<LivePosition[]>([]);
  const [history, setHistory] = useState<LivePosition[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [dynamics, setDynamics] = useState<DynamicsPoint[]>([]);
  const [telegram, setTelegram] = useState<NotificationStatus | null>(null);
  const [ticker, setTicker] = useState("");
  const [historyStatus, setHistoryStatus] = useState("closed");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [timeframe, setTimeframe] = useState<"1h" | "1d">("1h");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [sortBy, setSortBy] = useState("exit_ts");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const chartBoxRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);

  const normalizedTicker = ticker.trim().toUpperCase() || undefined;

  useEffect(() => {
    const element = chartBoxRef.current;
    if (!element) return;

    const chart = createChart(element, {
      height: 250,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#64748b",
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
        fontSize: 10,
      },
      grid: {
        vertLines: { color: "rgba(148,163,184,0.05)" },
        horzLines: { color: "rgba(148,163,184,0.08)" },
      },
      rightPriceScale: { borderColor: "rgba(148,163,184,0.15)" },
      timeScale: {
        borderColor: "rgba(148,163,184,0.15)",
        timeVisible: timeframe === "1h",
      },
      crosshair: {
        vertLine: { style: LineStyle.Dashed, color: "rgba(56,189,248,0.45)" },
        horzLine: { style: LineStyle.Dashed, color: "rgba(56,189,248,0.45)" },
      },
    });
    const series = chart.addAreaSeries({
      lineColor: "#38bdf8",
      topColor: "rgba(56,189,248,0.28)",
      bottomColor: "rgba(56,189,248,0.02)",
      lineWidth: 2,
      priceLineVisible: false,
    });
    chartRef.current = chart;
    seriesRef.current = series;

    const resize = () => chart.applyOptions({ width: element.clientWidth });
    resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.timeScale().applyOptions({ timeVisible: timeframe === "1h" });
    seriesRef.current?.setData(
      dynamics.map((point) => ({
        time: toChartTime(point.ts, timeframe),
        value: point.cum_pnl_rub,
      }))
    );
    chartRef.current?.timeScale().fitContent();
  }, [dynamics, timeframe]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const common = {
        ticker: normalizedTicker,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      };
      const [opened, closed, equity, telegramStatus] = await Promise.all([
        getLivePositions({
          ...common,
          status: "open",
          limit: 1000,
          offset: 0,
          sort_by: "entry_ts",
          sort_dir: "desc",
        }),
        getLivePositions({
          ...common,
          status: historyStatus,
          limit: pageSize,
          offset: (page - 1) * pageSize,
          sort_by: sortBy,
          sort_dir: sortDir,
        }),
        getPaperDynamics({ ...common, timeframe }),
        getNotificationStatus(),
      ]);
      setOpenPositions(opened.items);
      setHistory(closed.items);
      setHistoryTotal(closed.total);
      setDynamics(equity.points);
      setTelegram(telegramStatus);
      setLastUpdated(new Date());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [
    normalizedTicker,
    dateFrom,
    dateTo,
    historyStatus,
    page,
    pageSize,
    sortBy,
    sortDir,
    timeframe,
    reloadToken,
  ]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => setReloadToken((value) => value + 1), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, []);

  const handleSort = (field: string) => {
    if (sortBy === field) setSortDir((value) => (value === "asc" ? "desc" : "asc"));
    else {
      setSortBy(field);
      setSortDir("desc");
    }
    setPage(1);
  };

  const resetFilters = () => {
    setTicker("");
    setHistoryStatus("closed");
    setDateFrom("");
    setDateTo("");
    setPage(1);
  };

  const totalPages = Math.max(1, Math.ceil(historyTotal / pageSize));
  const realizedPnl = useMemo(
    () => history.reduce((sum, position) => sum + (position.pnl_rub ?? 0), 0),
    [history]
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <section className="rounded-lg border border-slate-800 bg-slate-900/60 px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Sandbox monitoring</div>
            <h2 className="font-display text-2xl font-bold text-slate-100">Live Trading</h2>
          </div>
          <div className="flex flex-wrap items-center gap-6">
            <div className="text-right">
              <div className="text-[10px] uppercase tracking-widest text-slate-500">Открыто</div>
              <div className="font-display text-2xl font-bold text-sky-300">{openPositions.length}</div>
            </div>
            <div className="text-right">
              <div className="text-[10px] uppercase tracking-widest text-slate-500">PnL страницы</div>
              <div className={`font-mono text-lg font-semibold ${pnlClass(realizedPnl)}`}>
                {realizedPnl >= 0 ? "+" : ""}{fmtNum(realizedPnl, 0)} ₽
              </div>
            </div>
            <div className="flex items-center gap-2 rounded border border-slate-700 bg-slate-950/60 px-3 py-2">
              <span className={`h-2.5 w-2.5 rounded-full ${
                telegram?.status === "connected" ? "bg-emerald-400 shadow-[0_0_10px_#34d399]" : "bg-rose-400"
              }`} />
              <div>
                <div className="text-[9px] uppercase tracking-widest text-slate-500">Telegram</div>
                <div className="font-mono text-[11px] text-slate-200">
                  {telegram?.status === "connected" ? "connected" : "disconnected"}
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-slate-500">
            Тикер
            <input
              value={ticker}
              onChange={(event) => { setTicker(event.target.value); setPage(1); }}
              placeholder="SBER"
              className="w-28 rounded border border-slate-700 bg-slate-950 px-3 py-1.5 font-mono text-xs uppercase text-slate-200 outline-none focus:border-sky-500"
            />
          </label>
          <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-slate-500">
            Статус истории
            <select
              value={historyStatus}
              onChange={(event) => { setHistoryStatus(event.target.value); setPage(1); }}
              className="rounded border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-slate-200 outline-none focus:border-sky-500"
            >
              {CLOSED_STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-slate-500">
            Дата от
            <input type="date" value={dateFrom} onChange={(event) => { setDateFrom(event.target.value); setPage(1); }}
              className="rounded border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-slate-200 outline-none focus:border-sky-500" />
          </label>
          <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-slate-500">
            Дата до
            <input type="date" value={dateTo} onChange={(event) => { setDateTo(event.target.value); setPage(1); }}
              className="rounded border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-slate-200 outline-none focus:border-sky-500" />
          </label>
          <button type="button" onClick={resetFilters}
            className="rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-400 transition hover:border-rose-500 hover:text-rose-300">
            Сбросить
          </button>
          <div className="ml-auto text-right font-mono text-[10px] text-slate-500">
            <div>{loading ? "обновление…" : "автообновление 10с"}</div>
            <div>{lastUpdated ? `последнее ${lastUpdated.toLocaleTimeString("ru-RU")}` : "—"}</div>
          </div>
        </div>
      </section>

      {error && (
        <div className="rounded border border-rose-700 bg-rose-500/10 px-4 py-2 text-sm text-rose-300">
          Ошибка мониторинга: {error}
        </div>
      )}

      <section className="rounded-lg border border-slate-800 bg-slate-900/50">
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <h3 className="font-display text-sm font-semibold uppercase tracking-wider text-slate-200">
            Открытые позиции
          </h3>
          <span className="font-mono text-[10px] text-slate-500">{openPositions.length} поз.</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-slate-950/50 text-left text-[10px] uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-4 py-2">Тикер</th><th className="px-4 py-2">Вход</th>
                <th className="px-4 py-2">Текущая</th><th className="px-4 py-2">PnL ₽</th>
                <th className="px-4 py-2">PnL %</th><th className="px-4 py-2">Размер</th>
                <th className="px-4 py-2">Статус</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {openPositions.map((position) => (
                <tr key={position.id} className="hover:bg-slate-800/40">
                  <td className="px-4 py-2 font-mono font-semibold text-slate-100">{position.ticker}</td>
                  <td className="px-4 py-2 font-mono text-slate-300">{fmtNum(position.entry_price, 3)}</td>
                  <td className="px-4 py-2 font-mono text-sky-300">{fmtNum(position.current_price, 3)}</td>
                  <td className={`px-4 py-2 font-mono ${pnlClass(position.pnl_rub)}`}>{fmtNum(position.pnl_rub, 2)}</td>
                  <td className={`px-4 py-2 font-mono ${pnlClass(position.pnl_pct)}`}>{fmtPct(position.pnl_pct)}</td>
                  <td className="px-4 py-2 font-mono text-slate-400">{position.size_lots ?? "—"} лот.</td>
                  <td className="px-4 py-2"><StatusBadge status={position.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && openPositions.length === 0 && (
            <div className="py-8 text-center text-xs text-slate-600">Нет открытых позиций</div>
          )}
        </div>
      </section>

      <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-display text-sm font-semibold uppercase tracking-wider text-slate-200">
            Equity curve · накопленный PnL
          </h3>
          <div className="flex gap-1">
            {(["1h", "1d"] as const).map((value) => (
              <button key={value} type="button" onClick={() => setTimeframe(value)}
                className={`rounded border px-2.5 py-1 font-mono text-[11px] ${
                  timeframe === value
                    ? "border-sky-500 bg-sky-500/20 text-sky-200"
                    : "border-slate-700 text-slate-400"
                }`}>
                {value}
              </button>
            ))}
          </div>
        </div>
        <div ref={chartBoxRef} className={loading ? "opacity-60" : "opacity-100"} />
        {!loading && dynamics.length === 0 && (
          <div className="py-4 text-center text-xs text-slate-600">Нет закрытых сделок для графика</div>
        )}
      </section>

      <section className="rounded-lg border border-slate-800 bg-slate-900/50">
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <h3 className="font-display text-sm font-semibold uppercase tracking-wider text-slate-200">
            История сделок
          </h3>
          <span className="font-mono text-[10px] text-slate-500">{historyTotal} всего</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-slate-950/50 text-left text-[10px] uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-4 py-2"><SortButton field="ticker" label="Тикер" {...{ sortBy, sortDir }} onSort={handleSort} /></th>
                <th className="px-4 py-2"><SortButton field="entry_ts" label="Вход" {...{ sortBy, sortDir }} onSort={handleSort} /></th>
                <th className="px-4 py-2"><SortButton field="exit_ts" label="Выход" {...{ sortBy, sortDir }} onSort={handleSort} /></th>
                <th className="px-4 py-2"><SortButton field="entry_price" label="Цена вх." {...{ sortBy, sortDir }} onSort={handleSort} /></th>
                <th className="px-4 py-2"><SortButton field="exit_price" label="Цена вых." {...{ sortBy, sortDir }} onSort={handleSort} /></th>
                <th className="px-4 py-2"><SortButton field="pnl_rub" label="PnL ₽" {...{ sortBy, sortDir }} onSort={handleSort} /></th>
                <th className="px-4 py-2"><SortButton field="pnl_pct" label="PnL %" {...{ sortBy, sortDir }} onSort={handleSort} /></th>
                <th className="px-4 py-2"><SortButton field="status" label="Статус" {...{ sortBy, sortDir }} onSort={handleSort} /></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {history.map((position) => (
                <tr key={position.id} className="hover:bg-slate-800/40">
                  <td className="px-4 py-2 font-mono font-semibold text-slate-100">{position.ticker}</td>
                  <td className="px-4 py-2 font-mono text-slate-400">{fmtTime(position.entry_ts)}</td>
                  <td className="px-4 py-2 font-mono text-slate-400">{fmtTime(position.exit_ts)}</td>
                  <td className="px-4 py-2 font-mono text-slate-300">{fmtNum(position.entry_price, 3)}</td>
                  <td className="px-4 py-2 font-mono text-slate-300">{fmtNum(position.exit_price, 3)}</td>
                  <td className={`px-4 py-2 font-mono ${pnlClass(position.pnl_rub)}`}>{fmtNum(position.pnl_rub, 2)}</td>
                  <td className={`px-4 py-2 font-mono ${pnlClass(position.pnl_pct)}`}>{fmtPct(position.pnl_pct)}</td>
                  <td className="px-4 py-2"><StatusBadge status={position.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && history.length === 0 && (
            <div className="py-8 text-center text-xs text-slate-600">Нет сделок для выбранных фильтров</div>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-800 px-4 py-3 text-xs">
          <button disabled={page <= 1 || loading} onClick={() => setPage((value) => value - 1)}
            className="rounded border border-slate-700 px-3 py-1 disabled:opacity-40">Назад</button>
          <span className="font-mono text-slate-500">{page} / {totalPages}</span>
          <button disabled={page >= totalPages || loading} onClick={() => setPage((value) => value + 1)}
            className="rounded border border-slate-700 px-3 py-1 disabled:opacity-40">Вперёд</button>
          <select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }}
            className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
            {PAGE_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}
          </select>
        </div>
      </section>
    </div>
  );
}
