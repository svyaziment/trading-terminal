import { useEffect, useState } from "react";
import type { Candle, Signal } from "../types";
import { getCandles } from "../api";
import CandleChart from "./CandleChart";

function Field({
  label,
  value,
}: {
  label: string;
  value: string | number | null | undefined;
}) {
  const text =
    value === null || value === undefined || value === "" ? "—" : String(value);

  return (
    <div className="rounded border border-slate-800 bg-slate-900/60 px-3 py-2">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-sm">{text}</div>
    </div>
  );
}

export default function SignalDetailModal({
  signal,
  onClose,
}: {
  signal: Signal;
  onClose: () => void;
}) {
  const [candles, setCandles] = useState<Candle[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);

      try {
        const response = await getCandles({
          ticker: signal.ticker,
          timeframe: signal.timeframe,
          limit: 300,
        });

        if (!cancelled) {
          setCandles(response.items);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, [signal]);

  const fields = [
    { label: "Ticker", value: signal.ticker },
    { label: "FIGI", value: signal.figi },
    { label: "Timeframe", value: signal.timeframe },
    { label: "Timestamp", value: signal.timestamp.replace("T", " ").slice(0, 19) },
    { label: "Signal", value: signal.signal },
    { label: "Confidence", value: signal.confidence },
    { label: "Price", value: signal.price },
    { label: "RSI", value: signal.rsi },
    { label: "MACD", value: signal.macd },
    { label: "BB Position", value: signal.bb_position },
    { label: "Volume Ratio", value: signal.volume_ratio },
    { label: "ATR %", value: signal.atr_pct },
    { label: "Buy Signals", value: signal.buy_signals },
    { label: "Sell Signals", value: signal.sell_signals },
    { label: "Total Signals", value: signal.total_signals },
    { label: "Pattern", value: signal.pattern_name },
  ];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-5xl overflow-y-auto rounded border border-slate-700 bg-slate-950 p-4"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h3 className="text-lg font-semibold">
              {signal.ticker} · {signal.timeframe} · {signal.signal}
            </h3>
            <p className="text-sm text-slate-400">
              {signal.timestamp.replace("T", " ").slice(0, 19)}
            </p>
          </div>

          <button
            onClick={onClose}
            className="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300 transition hover:bg-slate-800"
          >
            Закрыть
          </button>
        </div>

        <div className="mb-4 grid grid-cols-2 gap-2 md:grid-cols-4">
          {fields.map((field) => (
            <Field key={field.label} label={field.label} value={field.value} />
          ))}
        </div>

        <div className="mb-2 rounded border border-slate-800 bg-slate-900/60 px-3 py-2">
          <div className="text-xs text-slate-500">Summary</div>
          <div className="text-sm">{signal.summary ?? "—"}</div>
        </div>

        <div>
          <h4 className="mb-2 text-sm font-semibold text-slate-300">
            Свечи ({signal.ticker}, {signal.timeframe})
          </h4>

          {error && (
            <div className="mb-2 rounded border border-rose-700 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
              Ошибка загрузки свечей: {error}
            </div>
          )}

          {loading ? (
            <div className="flex h-64 items-center justify-center text-sm text-slate-500">
              Загрузка свечей...
            </div>
          ) : (
            <CandleChart candles={candles} height={360} />
          )}
        </div>
      </div>
    </div>
  );
}
