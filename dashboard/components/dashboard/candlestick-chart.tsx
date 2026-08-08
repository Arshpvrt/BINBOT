"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

import type { Candle, ExecutionMarker } from "@/lib/types";

const CHART_COLORS = {
  profit: "#10b981",
  loss: "#ef4444",
  grid: "rgba(148, 163, 184, 0.06)",
  text: "#64748b",
  crosshair: "#06b6d4",
};

/**
 * Store-agnostic candlestick chart: takes candles/markers as plain props
 * rather than reading a specific Zustand store, so it can be reused both
 * for the single primary chart (PriceChart) and for the scanner's
 * multi-position grid, where several instances render simultaneously for
 * different symbols.
 */
export function CandlestickChart({
  candles,
  markers,
  heightClass = "h-[280px]",
  compact = false,
}: {
  candles: Candle[];
  markers: ExecutionMarker[];
  heightClass?: string;
  compact?: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: CHART_COLORS.text,
        fontFamily: "var(--font-mono)",
        fontSize: compact ? 9 : 11,
      },
      grid: {
        vertLines: { color: CHART_COLORS.grid },
        horzLines: { color: CHART_COLORS.grid },
      },
      crosshair: {
        vertLine: { color: CHART_COLORS.crosshair, labelBackgroundColor: CHART_COLORS.crosshair },
        horzLine: { color: CHART_COLORS.crosshair, labelBackgroundColor: CHART_COLORS.crosshair },
      },
      rightPriceScale: { borderColor: "rgba(148, 163, 184, 0.12)" },
      timeScale: { borderColor: "rgba(148, 163, 184, 0.12)", timeVisible: true, secondsVisible: !compact },
      autoSize: true,
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: CHART_COLORS.profit,
      downColor: CHART_COLORS.loss,
      borderVisible: false,
      wickUpColor: CHART_COLORS.profit,
      wickDownColor: CHART_COLORS.loss,
    });

    const seriesMarkers = createSeriesMarkers(series, []);

    chartRef.current = chart;
    seriesRef.current = series;
    markersRef.current = seriesMarkers;

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      markersRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- chart is created once per mount
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series || candles.length === 0) return;
    const last = candles[candles.length - 1];
    series.update({
      time: last.time as UTCTimestamp,
      open: last.open,
      high: last.high,
      low: last.low,
      close: last.close,
    });
  }, [candles]);

  useEffect(() => {
    const plugin = markersRef.current;
    if (!plugin) return;
    plugin.setMarkers(
      markers.map((m) => ({
        time: m.time as UTCTimestamp,
        position: m.side === "BUY" ? "belowBar" : "aboveBar",
        color: m.side === "BUY" ? CHART_COLORS.profit : CHART_COLORS.loss,
        shape: m.side === "BUY" ? "arrowUp" : "arrowDown",
        text: `${m.side} ${m.quantity}`,
      }))
    );
  }, [markers]);

  return <div ref={containerRef} className={`${heightClass} w-full`} />;
}
