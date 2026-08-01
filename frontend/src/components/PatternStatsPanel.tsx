import { useEffect, useMemo, useState } from "react";
import { getSignalStats } from "../api";
import type { SignalStats, DirectionCount, TimeframeCount, PatternCount } from "../types";
import DataTable, { type ColumnDef } from "./ui/DataTable";

/* task-006: 4 таблицы статистики на едином DataTable (сортировка бесплатно) */

export default function PatternStatsPanel() {
  const [stats, setStats] = useState<SignalStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const response = await getSignalStats();
      setStats(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [reloadToken]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = window.setInterval(() => setReloadToken((token) => token + 1), 30000);
    return () => window.clearInterval(id);
  }, [autoRefresh]);

  const dirColumns = useMemo<ColumnDef<DirectionCount>[]>(() => [
    { key: "signal", label: "Сигнал", accessor: (d) => d.signal },
    { key: "cnt", label: "Кол-во", numeric: true, accessor: (d) => d.cnt },
  ], []);

  const tfColumns = useMemo<ColumnDef<TimeframeCount>[]>(() => [
    { key: "timeframe", label: "Таймфрейм", accessor: (d) => d.timeframe },
    { key: "cnt", label: "Кол-во", numeric: true, accessor: (d) => d.cnt },
  ], []);

  const patColumns = useMemo<ColumnDef<PatternCount>[]>(() => [
    { key: "pattern_name", label: "Паттерн", accessor: (p) => p.pattern_name },
    { key: "cnt", label: "Кол-во", numeric: true, accessor: (p) => p.cnt },
  ], []);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Статистика по сигналам</h2>
        <button
          onClick={() => void load()}
          disabled={loading}
          className="rounded bg-sky-500/20 px-3 py-1.5 text-sm font-medium text-sky-300 transition hover:bg-sky-500/30 disabled:opacity-50"
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

      {stats && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <div className="rounded border border-slate-800 bg-slate-900/60 px-4 py-3">
              <div className="text-sm text-slate-400">Всего сигналов</div>
              <div className="text-2xl font-semibold">{stats.total}</div>
            </div>
            <div className="rounded border border-slate-800 bg-slate-900/60 px-4 py-3">
              <div className="text-sm text-slate-400">Последний сигнал</div>
              <div className="text-lg font-semibold">
                {stats.latest_timestamp
                  ? String(stats.latest_timestamp).replace("T", " ").slice(0, 19)
                  : "—"}
              </div>
            </div>
            <div className="rounded border border-slate-800 bg-slate-900/60 px-4 py-3">
              <div className="text-sm text-slate-400">Уникальных паттернов</div>
              <div className="text-2xl font-semibold">{stats.by_pattern.length}</div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <DataTable
              columns={dirColumns}
              rows={stats.by_direction}
              rowKey={(d) => d.signal}
              title="По направлению"
            />
            <DataTable
              columns={tfColumns}
              rows={stats.by_timeframe}
              rowKey={(d) => d.timeframe}
              title="По таймфреймам"
            />
            <DataTable
              columns={patColumns}
              rows={stats.by_pattern}
              rowKey={(p) => p.pattern_name}
              title="Паттерны"
              defaultSort={{ key: "cnt", dir: "desc" }}
              scrollClass="max-h-96 overflow-y-auto"
            />
          </div>

          <DataTable
            columns={patColumns}
            rows={stats.by_pattern_combined}
            rowKey={(p) => p.pattern_name}
            title="Комбинации паттернов"
            defaultSort={{ key: "cnt", dir: "desc" }}
            scrollClass="max-h-96 overflow-y-auto"
          />
        </div>
      )}
    </div>
  );
}
