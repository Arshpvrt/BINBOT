"use client";

import type { StoreApi, UseBoundStore } from "zustand";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn, formatTime, formatUsd } from "@/lib/utils";
import type { TradingState } from "@/store/useTradingStore";

export function OpenTradesTable({ store }: { store: UseBoundStore<StoreApi<TradingState>> }) {
  const openPositions = store((s) => s.openPositions);

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>Open Trades</CardTitle>
        <span className="text-[10px] text-slate-500 font-mono">{openPositions.length} open</span>
      </CardHeader>
      <CardContent className="p-0">
        {openPositions.length === 0 ? (
          <div className="flex items-center justify-center py-10 text-xs text-slate-600 font-sans">
            No open positions
          </div>
        ) : (
          <div className="max-h-[260px] overflow-y-auto scrollbar-thin">
            <table className="w-full text-xs font-mono tabular-nums">
              <thead className="sticky top-0 bg-panel-2/95 backdrop-blur text-slate-500 uppercase text-[10px] tracking-wider font-sans">
                <tr>
                  <th className="py-1.5 pl-3 pr-2 text-left font-medium">Opened</th>
                  <th className="py-1.5 px-2 text-left font-medium">Sym</th>
                  <th className="py-1.5 px-2 text-left font-medium">Side</th>
                  <th className="py-1.5 px-2 text-right font-medium">Qty</th>
                  <th className="py-1.5 px-2 text-right font-medium">Entry</th>
                  <th className="py-1.5 px-2 text-right font-medium">Mark</th>
                  <th className="py-1.5 pl-2 pr-3 text-right font-medium">uPnL</th>
                </tr>
              </thead>
              <tbody>
                {openPositions.map((p) => (
                  <tr
                    key={p.symbol}
                    className="border-b border-panel-border/40 last:border-0 hover:bg-slate-800/30 transition-colors"
                  >
                    <td className="py-1.5 pl-3 pr-2 text-slate-500">
                      {p.openedAt != null ? formatTime(p.openedAt) : "—"}
                    </td>
                    <td className="py-1.5 px-2 text-slate-100 font-medium">{p.symbol}</td>
                    <td className={cn("py-1.5 px-2 font-semibold", p.side === "BUY" ? "text-profit" : "text-loss")}>
                      <Badge variant={p.side === "BUY" ? "profit" : "loss"}>{p.side}</Badge>
                    </td>
                    <td className="py-1.5 px-2 text-right text-slate-200">{p.quantity}</td>
                    <td className="py-1.5 px-2 text-right text-slate-200">{p.entryPrice.toFixed(4)}</td>
                    <td className="py-1.5 px-2 text-right text-slate-200">{p.markPrice.toFixed(4)}</td>
                    <td className={cn("py-1.5 pl-2 pr-3 text-right", p.unrealizedPnl >= 0 ? "text-profit" : "text-loss")}>
                      {formatUsd(p.unrealizedPnl, { signed: true })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
