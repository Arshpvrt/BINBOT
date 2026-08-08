"use client";

import type { StoreApi, UseBoundStore } from "zustand";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CandlestickChart } from "@/components/dashboard/candlestick-chart";
import type { TradingState } from "@/store/useTradingStore";
import type { Candle, ExecutionMarker } from "@/lib/types";

const EMPTY_CANDLES: Candle[] = [];
const EMPTY_MARKERS: ExecutionMarker[] = [];

export function PriceChart({
  store,
  symbol,
}: {
  store: UseBoundStore<StoreApi<TradingState>>;
  symbol?: string;
}) {
  const sym = symbol || process.env.NEXT_PUBLIC_CHART_SYMBOL || "ES";
  const candles = store((s) => s.candlesBySymbol[sym] ?? EMPTY_CANDLES);
  const markers = store((s) => s.executionMarkersBySymbol[sym] ?? EMPTY_MARKERS);
  const lastCandle = candles[candles.length - 1];

  return (
    <Card className="h-full">
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle>{sym} Futures</CardTitle>
          {lastCandle && (
            <span className="text-xs font-mono tabular-nums text-slate-300">{lastCandle.close.toFixed(2)}</span>
          )}
        </div>
        <Badge variant="system">15s bars</Badge>
      </CardHeader>
      <CardContent className="p-2">
        <CandlestickChart candles={candles} markers={markers} heightClass="h-[280px]" />
      </CardContent>
    </Card>
  );
}
