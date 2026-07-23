import { useEffect, useRef } from "react";
import { createChart, ColorType } from "lightweight-charts";
import type { UTCTimestamp } from "lightweight-charts";
import type { Candle } from "../types";

function toUnixTimestamp(value: string): number {
  const normalized = value.includes("T") ? value : value.replace(" ", "T");
  const withZone =
    normalized.endsWith("Z") || normalized.includes("+")
      ? normalized
      : normalized + "Z";

  return Math.floor(new Date(withZone).getTime() / 1000);
}

export default function CandleChart({
  candles,
  height = 320,
}: {
  candles: Candle[];
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    const chart = createChart(container, {
      width: container.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: "#020617" },
        textColor: "#cbd5e1",
      },
      grid: {
        vertLines: { color: "#1e293b" },
        horzLines: { color: "#1e293b" },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const series = chart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#f43f5e",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#f43f5e",
    });

    const data = candles
      .filter(
        (candle) =>
          candle.open !== null &&
          candle.high !== null &&
          candle.low !== null &&
          candle.close !== null
      )
      .map((candle) => ({
        time: toUnixTimestamp(candle.timestamp) as UTCTimestamp,
        open: Number(candle.open),
        high: Number(candle.high),
        low: Number(candle.low),
        close: Number(candle.close),
      }))
      .sort((a, b) => Number(a.time) - Number(b.time));

    series.setData(data);
    chart.timeScale().fitContent();

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth });
    };

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [candles, height]);

  if (candles.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-slate-500">
        Нет свечей для отображения
      </div>
    );
  }

  return <div ref={containerRef} style={{ height: `${height}px`, width: "100%" }} />;
}
