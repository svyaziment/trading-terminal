import { useEffect, useMemo, useState } from "react";
import { getInstruments } from "../api";
import type { Instrument } from "../types";
import DataTable, { type ColumnDef, type FilterState } from "./ui/DataTable";
import FilterChips from "./ui/FilterChips";

/* task-006: миграция на единый DataTable — сортировка всех полей + фильтры */

export default function InstrumentsPanel() {
  const [items, setItems] = useState<Instrument[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<FilterState>({});

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const response = await getInstruments(100);
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

  const columns = useMemo<ColumnDef<Instrument>[]>(() => [
    {
      key: "ticker", label: "Ticker",
      accessor: (i) => i.ticker,
      render: (i) => <span className="font-medium">{i.ticker}</span>,
      filter: { kind: "text", placeholder: "например RUAL" },
    },
    { key: "name", label: "Name", accessor: (i) => i.name ?? "", render: (i) => <span>{i.name ?? "—"}</span>, filter: { kind: "text" } },
    { key: "figi", label: "FIGI", accessor: (i) => i.figi, render: (i) => <span className="text-slate-400">{i.figi}</span> },
    { key: "instrument_type", label: "Type", accessor: (i) => i.instrument_type ?? "", render: (i) => <span>{i.instrument_type ?? "—"}</span>, filter: { kind: "select" } },
    { key: "currency", label: "Currency", accessor: (i) => i.currency ?? "", render: (i) => <span>{i.currency ?? "—"}</span>, filter: { kind: "select" } },
    { key: "lot_size", label: "Lot", numeric: true, accessor: (i) => i.lot_size, render: (i) => <span>{i.lot_size ?? "—"}</span> },
    { key: "is_tradable", label: "Tradable", accessor: (i) => String(i.is_tradable ?? "—"), render: (i) => <span>{String(i.is_tradable ?? "—")}</span> },
    { key: "exchange", label: "Exchange", accessor: (i) => i.exchange ?? "", render: (i) => <span>{i.exchange ?? "—"}</span>, filter: { kind: "select" } },
  ], []);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold">Инструменты</h2>
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
        rowKey={(i) => i.figi}
        filters={filters}
        onFiltersChange={setFilters}
        emptyText={loading ? "загрузка…" : "нет данных"}
      />
    </div>
  );
}
