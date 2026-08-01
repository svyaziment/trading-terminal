import { useEffect, useMemo, useState } from "react";
import { getTopStocks } from "../api";
import type { TopStock } from "../types";
import DataTable, { type ColumnDef, type FilterState } from "./ui/DataTable";
import FilterChips from "./ui/FilterChips";

/* task-006: миграция на единый DataTable — сортировка всех полей + фильтр по тикеру */

export default function TopStocksPanel() {
  const [items, setItems] = useState<TopStock[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<FilterState>({});

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const response = await getTopStocks(30);
      setItems(response.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const columns = useMemo<ColumnDef<TopStock>[]>(() => [
    { key: "rank", label: "Rank", numeric: true, accessor: (t) => t.rank, render: (t) => <span className="text-slate-400">{t.rank}</span> },
    { key: "report_date", label: "Report Date", accessor: (t) => t.report_date },
    {
      key: "ticker", label: "Ticker",
      accessor: (t) => t.ticker,
      render: (t) => <span className="font-medium">{t.ticker}</span>,
      filter: { kind: "text", placeholder: "например RUAL" },
    },
    { key: "name", label: "Name", accessor: (t) => t.name ?? "", render: (t) => <span>{t.name ?? "—"}</span> },
    { key: "figi", label: "FIGI", accessor: (t) => t.figi, render: (t) => <span className="text-slate-400">{t.figi}</span> },
    {
      key: "sum_volume", label: "Volume", numeric: true,
      accessor: (t) => t.sum_volume,
      render: (t) => <span>{Number(t.sum_volume).toLocaleString("ru-RU")}</span>,
    },
    { key: "candle_count", label: "Candles", numeric: true, accessor: (t) => t.candle_count },
  ], []);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold">ТОП-30 по объёму</h2>
        <button
          onClick={() => void load()}
          disabled={loading}
          className="rounded bg-sky-500/20 px-3 py-1.5 text-sm font-medium text-sky-300 transition hover:bg-sky-500/30 disabled:opacity-50"
        >
          {loading ? "Загрузка..." : "Обновить"}
        </button>
      </div>

      {error && (
        <div className="rounded border border-rose-700 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
          Ошибка: {error}
        </div>
      )}

      <FilterChips filters={filters} onChange={setFilters} />

      <DataTable
        columns={columns}
        rows={items}
        rowKey={(t) => t.report_date + "-" + t.ticker}
        filters={filters}
        onFiltersChange={setFilters}
        emptyText={loading ? "загрузка…" : "нет данных"}
      />
    </div>
  );
}
