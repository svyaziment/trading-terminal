import { useEffect, useState } from "react";
import { getInstruments } from "../api";
import type { Instrument } from "../types";

export default function InstrumentsPanel() {
  const [items, setItems] = useState<Instrument[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

      <div className="overflow-x-auto rounded border border-slate-800">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              <th className="px-2 py-2 text-left">Ticker</th>
              <th className="px-2 py-2 text-left">Name</th>
              <th className="px-2 py-2 text-left">FIGI</th>
              <th className="px-2 py-2 text-left">Type</th>
              <th className="px-2 py-2 text-left">Currency</th>
              <th className="px-2 py-2 text-right">Lot</th>
              <th className="px-2 py-2 text-left">Tradable</th>
              <th className="px-2 py-2 text-left">Exchange</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr
                key={item.figi}
                className="border-t border-slate-800 hover:bg-slate-900/60"
              >
                <td className="px-2 py-1 font-medium">{item.ticker}</td>
                <td className="px-2 py-1">{item.name ?? "—"}</td>
                <td className="px-2 py-1 text-slate-400">{item.figi}</td>
                <td className="px-2 py-1">{item.instrument_type ?? "—"}</td>
                <td className="px-2 py-1">{item.currency ?? "—"}</td>
                <td className="px-2 py-1 text-right">{item.lot_size ?? "—"}</td>
                <td className="px-2 py-1">{String(item.is_tradable ?? "—")}</td>
                <td className="px-2 py-1">{item.exchange ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
