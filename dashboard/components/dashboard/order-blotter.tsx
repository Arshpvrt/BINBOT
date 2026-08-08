"use client";

import { useEffect } from "react";
import type { StoreApi, UseBoundStore } from "zustand";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn, formatTime } from "@/lib/utils";
import type { TradingState } from "@/store/useTradingStore";
import type { OrderRow, OrderStatusValue } from "@/lib/types";

type Store = UseBoundStore<StoreApi<TradingState>>;

const STATUS_VARIANT: Record<OrderStatusValue, "profit" | "loss" | "pending" | "neutral"> = {
  WORKING: "pending",
  PARTIAL: "pending",
  FILLED: "profit",
  CANCELED: "neutral",
  REJECTED: "loss",
};

function BlotterRow({ order, store }: { order: OrderRow; store: Store }) {
  const flash = store((s) => s.flashingRows[order.id]);
  const clearFlash = store((s) => s.clearFlash);

  useEffect(() => {
    if (!flash) return;
    const t = window.setTimeout(() => clearFlash(order.id), 720);
    return () => window.clearTimeout(t);
  }, [flash, order.id, clearFlash]);

  return (
    <tr
      className={cn(
        "border-b border-panel-border/40 last:border-0 hover:bg-slate-800/30 transition-colors",
        flash === "profit" && "animate-flash-profit",
        flash === "loss" && "animate-flash-loss"
      )}
    >
      <td className="py-1.5 pl-3 pr-2 text-slate-500">{formatTime(order.updatedAt)}</td>
      <td className="py-1.5 px-2 text-slate-400">{order.id}</td>
      <td className="py-1.5 px-2 text-slate-100 font-medium">{order.symbol}</td>
      <td className={cn("py-1.5 px-2 font-semibold", order.side === "BUY" ? "text-profit" : "text-loss")}>
        {order.side}
      </td>
      <td className="py-1.5 px-2 text-slate-400">{order.orderType}</td>
      <td className="py-1.5 px-2 text-right text-slate-200">{order.filledQuantity}/{order.quantity}</td>
      <td className="py-1.5 px-2 text-right text-slate-200">
        {order.avgFillPrice != null ? order.avgFillPrice.toFixed(2) : order.limitPrice != null ? order.limitPrice.toFixed(2) : "—"}
      </td>
      <td className="py-1.5 px-2 text-right">
        {order.slippageTicks != null ? (
          <span className={order.slippageTicks <= 0 ? "text-profit" : "text-loss"}>
            {order.slippageTicks >= 0 ? "+" : ""}
            {order.slippageTicks.toFixed(2)}t
          </span>
        ) : (
          <span className="text-slate-600">—</span>
        )}
      </td>
      <td className="py-1.5 pl-2 pr-3 text-right">
        <Badge variant={STATUS_VARIANT[order.status]}>{order.status}</Badge>
      </td>
    </tr>
  );
}

function BlotterTable({ orders, emptyLabel, store }: { orders: OrderRow[]; emptyLabel: string; store: Store }) {
  if (orders.length === 0) {
    return <div className="flex items-center justify-center py-10 text-xs text-slate-600 font-sans">{emptyLabel}</div>;
  }
  return (
    <div className="max-h-[260px] overflow-y-auto scrollbar-thin">
      <table className="w-full text-xs font-mono tabular-nums">
        <thead className="sticky top-0 bg-panel-2/95 backdrop-blur text-slate-500 uppercase text-[10px] tracking-wider font-sans">
          <tr>
            <th className="py-1.5 pl-3 pr-2 text-left font-medium">Time</th>
            <th className="py-1.5 px-2 text-left font-medium">Order</th>
            <th className="py-1.5 px-2 text-left font-medium">Sym</th>
            <th className="py-1.5 px-2 text-left font-medium">Side</th>
            <th className="py-1.5 px-2 text-left font-medium">Type</th>
            <th className="py-1.5 px-2 text-right font-medium">Qty</th>
            <th className="py-1.5 px-2 text-right font-medium">Price</th>
            <th className="py-1.5 px-2 text-right font-medium">Shortfall</th>
            <th className="py-1.5 pl-2 pr-3 text-right font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <BlotterRow key={o.id} order={o} store={store} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function OrderBlotter({ store }: { store: Store }) {
  const orders = store((s) => s.orders);

  const working = orders.filter((o) => o.status === "WORKING" || o.status === "PARTIAL");
  const fills = orders.filter((o) => o.status === "FILLED");
  const rejected = orders.filter((o) => o.status === "REJECTED" || o.status === "CANCELED");

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>Order Blotter</CardTitle>
        <span className="text-[10px] text-slate-500 font-mono">{orders.length} total</span>
      </CardHeader>
      <CardContent className="p-2">
        <Tabs defaultValue="working">
          <TabsList>
            <TabsTrigger value="working">
              Working <span className="ml-1 text-pending">{working.length}</span>
            </TabsTrigger>
            <TabsTrigger value="fills">
              Fills <span className="ml-1 text-profit">{fills.length}</span>
            </TabsTrigger>
            <TabsTrigger value="rejected">
              Canceled / Rejected <span className="ml-1 text-loss">{rejected.length}</span>
            </TabsTrigger>
          </TabsList>
          <TabsContent value="working" className="mt-2">
            <BlotterTable orders={working} emptyLabel="No working orders" store={store} />
          </TabsContent>
          <TabsContent value="fills" className="mt-2">
            <BlotterTable orders={fills} emptyLabel="No fills yet" store={store} />
          </TabsContent>
          <TabsContent value="rejected" className="mt-2">
            <BlotterTable orders={rejected} emptyLabel="Nothing canceled or rejected" store={store} />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
