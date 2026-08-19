import { useEffect, useRef } from "react";
import { createChart, ColorType, LineStyle } from "lightweight-charts";
import type {
  IChartApi,
  ISeriesApi,
  SeriesMarker,
  UTCTimestamp,
} from "lightweight-charts";
import type {
  Candle,
  ChartOverlay,
  OverlayBand,
  OverlayLine,
  OverlayMarker,
  OverlayRay,
} from "../types";

const LINE_PALETTE = ["#38bdf8", "#a78bfa", "#fbbf24", "#fb7185", "#34d399"];

function toUnixTimestamp(value: string): number {
  const normalized = value.includes("T") ? value : value.replace(" ", "T");
  const withZone =
    normalized.endsWith("Z") || normalized.includes("+")
      ? normalized
      : normalized + "Z";

  return Math.floor(new Date(withZone).getTime() / 1000);
}

function overlayTimestamp(overlay: { ts?: string; timestamp?: string }): string | null {
  return overlay.ts ?? overlay.timestamp ?? null;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function snapFromTime(targetTs: string, candleTimes: UTCTimestamp[]): UTCTimestamp | null {
  if (candleTimes.length === 0) {
    return null;
  }

  const target = toUnixTimestamp(targetTs);
  for (const time of candleTimes) {
    if (Number(time) === target) {
      return time;
    }
  }

  for (const time of candleTimes) {
    if (Number(time) >= target) {
      return time;
    }
  }

  return candleTimes[candleTimes.length - 1];
}

function snapToTime(targetTs: string, candleTimes: UTCTimestamp[]): UTCTimestamp | null {
  if (candleTimes.length === 0) {
    return null;
  }

  const target = toUnixTimestamp(targetTs);
  let fallback: UTCTimestamp | null = null;

  for (const time of candleTimes) {
    if (Number(time) <= target) {
      fallback = time;
    }
    if (Number(time) === target) {
      return time;
    }
  }

  return fallback ?? candleTimes[candleTimes.length - 1];
}

function levelColors(levelType?: string, color?: string): { line: string; fill: string } {
  if (color) {
    return {
      line: color,
      fill: color.includes("rgba")
        ? color.replace(/,\s*[\d.]+\)$/, ", 0.18)")
        : `${color}33`,
    };
  }

  if (levelType === "support") {
    return {
      line: "rgba(16, 185, 129, 0.9)",
      fill: "rgba(16, 185, 129, 0.18)",
    };
  }

  if (levelType === "resistance") {
    return {
      line: "rgba(244, 63, 94, 0.9)",
      fill: "rgba(244, 63, 94, 0.18)",
    };
  }

  return {
    line: "rgba(148, 163, 184, 0.9)",
    fill: "rgba(148, 163, 184, 0.15)",
  };
}

function markerStyle(overlay: OverlayMarker): {
  position: SeriesMarker<UTCTimestamp>["position"];
  shape: SeriesMarker<UTCTimestamp>["shape"];
  color: string;
} {
  const signal = overlay.signal?.toUpperCase();

  if (signal === "BUY") {
    return {
      position: overlay.position ?? "belowBar",
      shape: overlay.shape ?? "arrowUp",
      color: overlay.color ?? "#10b981",
    };
  }

  if (signal === "SELL") {
    return {
      position: overlay.position ?? "aboveBar",
      shape: overlay.shape ?? "arrowDown",
      color: overlay.color ?? "#f43f5e",
    };
  }

  return {
    position: overlay.position ?? "aboveBar",
    shape: overlay.shape ?? "circle",
    color: overlay.color ?? "#38bdf8",
  };
}

function applyRayOverlay(
  chart: IChartApi,
  overlay: OverlayRay,
  candleTimes: UTCTimestamp[]
): ISeriesApi<"Line"> | null {
  if (!isFiniteNumber(overlay.price)) {
    return null;
  }

  const fromTime = snapFromTime(overlay.from_ts, candleTimes);
  const toTime = snapToTime(overlay.to_ts, candleTimes);
  if (fromTime === null || toTime === null || Number(fromTime) > Number(toTime)) {
    return null;
  }

  const colors = levelColors(overlay.level_type, overlay.color);
  const series = chart.addLineSeries({
    color: colors.line,
    lineWidth: 1,
    lineStyle: LineStyle.Solid,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  });

  series.setData([
    { time: fromTime, value: overlay.price },
    { time: toTime, value: overlay.price },
  ]);

  return series;
}

function applyBandOverlay(
  chart: IChartApi,
  overlay: OverlayBand,
  candleTimes: UTCTimestamp[]
): ISeriesApi<"Baseline"> | null {
  if (!isFiniteNumber(overlay.lower) || !isFiniteNumber(overlay.upper)) {
    return null;
  }

  const lower = Math.min(overlay.lower, overlay.upper);
  const upper = Math.max(overlay.lower, overlay.upper);
  const fromTime = snapFromTime(overlay.from_ts, candleTimes);
  const toTime = snapToTime(overlay.to_ts, candleTimes);
  if (fromTime === null || toTime === null || Number(fromTime) > Number(toTime)) {
    return null;
  }

  const colors = levelColors(overlay.level_type, overlay.color);
  const series = chart.addBaselineSeries({
    baseValue: { type: "price", price: lower },
    topLineColor: "rgba(0,0,0,0)",
    topFillColor1: colors.fill,
    topFillColor2: colors.fill,
    bottomFillColor1: "rgba(0,0,0,0)",
    bottomFillColor2: "rgba(0,0,0,0)",
    bottomLineColor: "rgba(0,0,0,0)",
    lineVisible: false,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  });

  series.setData([
    { time: fromTime, value: upper },
    { time: toTime, value: upper },
  ]);

  return series;
}

function applyLineOverlay(
  chart: IChartApi,
  overlay: OverlayLine,
  candleTimes: UTCTimestamp[],
  paletteIndex: number
): ISeriesApi<"Line"> | null {
  if (!Array.isArray(overlay.points) || overlay.points.length === 0) {
    return null;
  }

  const byTime = new Map<number, number>();
  for (const point of overlay.points) {
    const ts = overlayTimestamp(point);
    if (!ts || !isFiniteNumber(point.value)) {
      continue;
    }
    const snapped = snapToTime(ts, candleTimes);
    if (snapped === null) {
      continue;
    }
    byTime.set(Number(snapped), point.value);
  }

  const data = Array.from(byTime.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([time, value]) => ({
      time: time as UTCTimestamp,
      value,
    }));

  if (data.length === 0) {
    return null;
  }

  const series = chart.addLineSeries({
    color: overlay.color ?? LINE_PALETTE[paletteIndex % LINE_PALETTE.length],
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
    title: overlay.label ?? "",
  });
  series.setData(data);
  return series;
}

function buildMarkers(
  overlays: ChartOverlay[],
  candleTimes: UTCTimestamp[]
): SeriesMarker<UTCTimestamp>[] {
  const markers: SeriesMarker<UTCTimestamp>[] = [];

  for (const overlay of overlays) {
    if (overlay.type !== "marker") {
      continue;
    }

    const ts = overlayTimestamp(overlay);
    if (!ts) {
      continue;
    }

    const time = snapToTime(ts, candleTimes);
    if (time === null) {
      continue;
    }

    const style = markerStyle(overlay);
    markers.push({
      time,
      position: style.position,
      shape: style.shape,
      color: style.color,
      text: overlay.text,
    });
  }

  return markers.sort((a, b) => Number(a.time) - Number(b.time));
}

function applyOverlays(
  chart: IChartApi,
  candleSeries: ISeriesApi<"Candlestick">,
  overlays: ChartOverlay[] | undefined,
  candleTimes: UTCTimestamp[]
): void {
  if (!overlays?.length) {
    return;
  }

  let lineIndex = 0;
  for (const overlay of overlays) {
    if (overlay.type === "band") {
      applyBandOverlay(chart, overlay, candleTimes);
    } else if (overlay.type === "ray") {
      applyRayOverlay(chart, overlay, candleTimes);
    } else if (overlay.type === "line") {
      applyLineOverlay(chart, overlay, candleTimes, lineIndex);
      lineIndex += 1;
    }
  }

  const markers = buildMarkers(overlays, candleTimes);
  if (markers.length > 0) {
    candleSeries.setMarkers(markers);
  }
}

export default function CandleChart({
  candles,
  overlays,
  height = 320,
}: {
  candles: Candle[];
  overlays?: ChartOverlay[];
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || candles.length === 0) {
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

    const candleSeries = chart.addCandlestickSeries({
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

    if (data.length === 0) {
      chart.remove();
      return;
    }

    const candleTimes = data.map((bar) => bar.time);
    applyOverlays(chart, candleSeries, overlays, candleTimes);
    candleSeries.setData(data);
    chart.timeScale().fitContent();

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth });
    };

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [candles, overlays, height]);

  if (candles.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-slate-500">
        Нет свечей для отображения
      </div>
    );
  }

  return <div ref={containerRef} style={{ height: `${height}px`, width: "100%" }} />;
}
