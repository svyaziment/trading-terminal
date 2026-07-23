import { useEffect, useState } from "react";
import { getSignals } from "../api";
import type { Signal } from "../types";
import SignalDetailModal from "./SignalDetailModal";

type SortKey =
  | "timestamp"
  | "ticker"
  | "figi"
  | "timeframe"
  | "signal"
  | "confidence"
  | "price"
  | "rsi"
  | "macd"
  | "bb_position"
  | "volume_ratio"
  | "atr_pct"
  | "pattern_name";

type SortDir = "asc" | "desc";

interface Filters {
  ticker: string;
  timeframe: string;
  signal: string;
  dateFrom: string;
  dateTo: string;
}

const columns: Array<{ key: SortKey; label: string; numeric?: boolean }> = [
  { key: "timestamp", label: "Timestamp" },
  { key: "ticker", label: "Ticker" },
  { key: "figi", label: "FIGI" },
  { key: "timeframe", label: "TF" },
  { key: "signal", label: "Signal" },
  { key: "confidence", label: "Conf", numeric: true },
  { key: "price", label: "Price", numeric: true },
  { key: "rsi", label: "RSI", numeric: true },
  { key: "macd", label: "MACD", numeric: true },
  { key: "bb_position", label: "BB%", numeric: true },
  { key: "volume_ratio", label: "VolRatio", numeric: true },
  { key: "atr_pct", label: "ATR%", numeric: true },
  { key: "pattern_name", label: "Pattern" },
];

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }

  return Number(value).toFixed(digits);
}

function SignalBadge({ value }: { value: string }) {
  let className = "rounded px-2 py-0.5 text-xs font-semibold ";

  if (value === "BUY") {
    className += "bg-emerald-500/20 text-emerald-300";
  } else if (value === "SELL") {
    className += "bg-rose-500/20 text-rose-300";
  } else {
    className += "bg-slate-500/20 text-slate-300";
  }

  return <span className={className}>{value}</span>;
}

export default function SignalsPanel() {
  const [items, setItems] = useState<Signal[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Signal | null>(null);

  const [ticker, setTicker] = useState("");
  const [timeframe, setTimeframe] = useState("");
  const [signal, setSignal] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const [filters, setFilters] = useState<Filters>({
    ticker: "",
    timeframe: "",
    signal: "",
    dateFrom: "",
    dateTo: "",
  });

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sortBy, setSortBy] = useState<SortKey>("timestamp");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  async function load() {
    setLoading(true);
    setError(null);

    try {
      const response = await getSignals({
        limit: pageSize,
        offset: (page - 1) * pageSize,
        ticker: filters.ticker || undefined,
        timeframe: filters.timeframe || undefined,
        signal: filters.signal || undefined,
        date_from: filters.dateFrom || undefined,
        date_to: filters.dateTo ? `${filters.dateTo} 23:59:59` : undefined,
        sort_by: sortBy,
        sort_dir: sortDir,
      });

      setItems(response.items);
      setTotal(response.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [filters, page, pageSize, sortBy, sortDir, reloadToken]);

  useEffect(() => {
    if (!autoRefresh) {
      return;
    }

    const id = window.setInterval(() => {
      setReloadToken((token) => token + 1);
    }, 30000);

    return () => window.clearInterval(id);
  }, [autoRefresh]);

  function applyFilters() {
    setFilters({
      ticker,
      timeframe,
      signal,
      dateFrom,
      dateTo,
    });
    setPage(1);
  }

  function resetFilters() {
    setTicker("");
    setTimeframe("");
    setSignal("");
    setDateFrom("");
    setDateTo("");
    setFilters({
      ticker: "",
      timeframe: "",
      signal: "",
      dateFrom: "",
      dateTo: "",
    });
    setPage(1);
  }

  function toggleSort(key: SortKey) {
    if (sortBy === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortBy(key);
      setSortDir(key === "timestamp" ? "desc" : "asc");
    }

    setPage(1);
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="space-y-1 text-sm">
          <span className="block text-slate-400">Ticker</span>
          <input
            value={ticker}
            onChange={(event) => setTicker(event.target.value.toUpperCase())}
            placeholder="VTBR"
            className="w-36 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm outline-none focus:border-sky-500"
          />
        </label>

        <label className="space-y-1 text-sm">
          <span className="block text-slate-400">Timeframe</span>
          <select
            value={timeframe}
            onChange={(event) => setTimeframe(event.target.value)}
            className="w-32 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm outline-none focus:border-sky-500"
          >
            <option value="">Все</option>
            <option value="30min">30min</option>
            <option value="1h">1h</option>
            <option value="4h">4h</option>
            <option value="1d">1d</option>
          </select>
        </label>

        <label className="space-y-1 text-sm">
          <span className="block text-slate-400">Signal</span>
          <select
            value={signal}
            onChange={(event) => setSignal(event.target.value)}
            className="w-28 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm outline-none focus:border-sky-500"
          >
            <option value="">Все</option>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </select>
        </label>

        <label className="space-y-1 text-sm">
          <span className="block text-slate-400">Date from</span>
          <input
            type="date"
            value={dateFrom}
            onChange={(event) => setDateFrom(event.target.value)}
            className="w-40 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm outline-none focus:border-sky-500"
          />
        </label>

        <label className="space-y-1 text-sm">
          <span className="block text-slate-400">Date to</span>
          <input
            type="date"
            value={dateTo}
            onChange={(event) => setDateTo(event.target.value)}
            className="w-40 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm outline-none focus:border-sky-500"
          />
        </label>

        <button
          onClick={applyFilters}
          className="rounded bg-sky-500/20 px-3 py-1.5 text-sm font-medium text-sky-300 transition hover:bg-sky-500/30"
        >
          Применить
        </button>

        <button
          onClick={resetFilters}
          className="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300 transition hover:bg-slate-800"
        >
          Сброс
        </button>

        <button
          onClick={() => void load()}
          disabled={loading}
          className="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300 transition hover:bg-slate-800 disabled:opacity-50"
        >
          {loading ? "Загрузка..." : "Обновить"}
        </button>

        <label className="flex items-center gap-2 text-sm text-slate-400">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(event) => setAutoRefresh(event.target.checked)}
          />
          Автообновление 30 сек
        </label>
      </div>

      {error && (
        <div className="rounded border border-rose-700 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
          Ошибка: {error}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 text-sm text-slate-400">
        <div>Всего сигналов: {total}</div>
        <div>Показано: {items.length}</div>

        <div className="ml-auto flex items-center gap-2">
          <button
            disabled={page <= 1 || loading}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
            className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40"
          >
            Назад
          </button>

          <span>
            Страница {page} из {totalPages}
          </span>

          <button
            disabled={page >= totalPages || loading}
            onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
            className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40"
          >
            Вперёд
          </button>

          <select
            value={pageSize}
            onChange={(event) => {
              setPageSize(Number(event.target.value));
              setPage(1);
            }}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm outline-none focus:border-sky-500"
          >
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      </div>

      <div className="overflow-x-auto rounded border border-slate-800">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  onClick={() => toggleSort(column.key)}
                  className={
                    "cursor-pointer select-none px-2 py-2 " +
                    (column.numeric ? "text-right" : "text-left")
                  }
                >
                  {column.label}
                  {sortBy === column.key ? (sortDir === "asc" ? " ↑" : " ↓") : ""}
                </th>
              ))}
              <th className="px-2 py-2 text-left">Summary</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr
                key={item.id}
                onClick={() => setSelected(item)}
                className="cursor-pointer border-t border-slate-800 hover:bg-slate-900/60"
              >
                <td className="whitespace-nowrap px-2 py-1">
                  {item.timestamp.replace("T", " ").slice(0, 19)}
                </td>
                <td className="px-2 py-1 font-medium">{item.ticker}</td>
                <td className="px-2 py-1 text-slate-400">{item.figi ?? "—"}</td>
                <td className="px-2 py-1">{item.timeframe}</td>
                <td className="px-2 py-1">
                  <SignalBadge value={item.signal} />
                </td>
                <td className="px-2 py-1 text-right">
                  {formatNumber(item.confidence, 2)}
                </td>
                <td className="px-2 py-1 text-right">
                  {formatNumber(item.price, 4)}
                </td>
                <td className="px-2 py-1 text-right">
                  {formatNumber(item.rsi, 2)}
                </td>
                <td className="px-2 py-1 text-right">
                  {formatNumber(item.macd, 4)}
                </td>
                <td className="px-2 py-1 text-right">
                  {formatNumber(item.bb_position, 2)}
                </td>
                <td className="px-2 py-1 text-right">
                  {formatNumber(item.volume_ratio, 2)}
                </td>
                <td className="px-2 py-1 text-right">
                  {formatNumber(item.atr_pct, 2)}
                </td>
                <td className="max-w-xs px-2 py-1 text-slate-300">
                  {item.pattern_name ?? "—"}
                </td>
                <td className="max-w-xl px-2 py-1 text-slate-300">
                  {item.summary ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <SignalDetailModal signal={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
