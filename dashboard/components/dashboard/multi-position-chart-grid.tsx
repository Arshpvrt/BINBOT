"use client";

import type { StoreApi, UseBoundStore } from "zustand";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CandlestickChart } from "@/components/dashboard/candlestick-chart";
import { cn, formatUsd } from "@/lib/utils";
import type { TradingState } from "@/store/useTradingStore";
import type { Candle, ExecutionMarker } from "@/lib/types";

const GRID_SLOTS = 4;
const EMPTY_CANDLES: Candle[] = [];
const EMPTY_MARKERS: ExecutionMarker[] = [];

function EmptySlot() {
  return (
    <div className="flex h-full min-h-[220px] items-center justify-center rounded-lg border border-dashed border-panel-border/50 text-xs text-slate-600 font-sans">
      No open position
    </div>
  );
}

function PositionTile({
  store,
  symbol,
}: {
  store: UseBoundStore<StoreApi<TradingState>>;
  symbol: string;
}) {
  const candles = store((s) => s.candlesBySymbol[symbol] ?? EMPTY_CANDLES);
  const markers = store((s) => s.executionMarkersBySymbol[symbol] ?? EMPTY_MARKERS);
  const position = store((s) => s.openPositions.find((p) => p.symbol === symbol));

  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-panel-border/60 bg-panel-2/40 p-2">
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-slate-100 font-mono">{symbol}</span>
          {position && (
            <Badge variant={position.side === "BUY" ? "profit" : "loss"}>{position.side}</Badge>
          )}
        </div>
        {position && (
          <span
            className={cn(
              "text-xs font-mono tabular-nums",
              position.unrealizedPnl >= 0 ? "text-profit" : "text-loss"
            )}
          >
            {formatUsd(position.unrealizedPnl, { signed: true })}
          </span>
        )}
      </div>
      <CandlestickChart candles={candles} markers={markers} heightClass="h-[220px]" compact />
    </div>
  );
}

/**
 * 2x2 grid mirroring the scanner strategy's 4-position limit: one tile per
 * currently open position slot, empty placeholders for unfilled slots.
 */
export function MultiPositionChartGrid({ store }: { store: UseBoundStore<StoreApi<TradingState>> }) {
  const openPositions = store((s) => s.openPositions);
  const symbols = openPositions.slice(0, GRID_SLOTS).map((p) => p.symbol);

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>Open Position Charts</CardTitle>
        <span className="text-[10px] text-slate-500 font-mono">{openPositions.length}/{GRID_SLOTS} slots</span>
      </CardHeader>
      <CardContent className="p-2">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {Array.from({ length: GRID_SLOTS }).map((_, i) =>
            symbols[i] ? (
              <PositionTile key={symbols[i]} store={store} symbol={symbols[i]} />
            ) : (
              <EmptySlot key={`empty-${i}`} />
            )
          )}
        </div>
      </CardContent>
    </Card>
  );
}
