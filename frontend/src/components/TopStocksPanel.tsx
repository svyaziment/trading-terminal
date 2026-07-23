import { useEffect, useState } from "react";
import { getTopStocks } from "../api";
import type { TopStock } from "../types";

export default function TopStocksPanel() {
  const [items, setItems] = useState<TopStock[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

      <div className="overflow-x-auto rounded border border-slate-800">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              <th className="px-2 py-2 text-left">Rank</th>
              <th className="px-2 py-2 text-left">Report Date</th>
              <th className="px-2 py-2 text-left">Ticker</th>
              <th className="px-2 py-2 text-left">Name</th>
              <th className="px-2 py-2 text-left">FIGI</th>
              <th className="px-2 py-2 text-right">Volume</th>
              <th className="px-2 py-2 text-right">Candles</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr
                key={`${item.report_date}-${item.ticker}`}
                className="border-t border-slate-800 hover:bg-slate-900/60"
              >
                <td className="px-2 py-1">{item.rank}</td>
                <td className="px-2 py-1">{item.report_date}</td>
                <td className="px-2 py-1 font-medium">{item.ticker}</td>
                <td className="px-2 py-1">{item.name ?? "—"}</td>
                <td className="px-2 py-1 text-slate-400">{item.figi}</td>
                <td className="px-2 py-1 text-right">
                  {Number(item.sum_volume).toLocaleString("ru-RU")}
                </td>
                <td className="px-2 py-1 text-right">{item.candle_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
