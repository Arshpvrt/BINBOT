"use client";

import { useId } from "react";
import type { StoreApi, UseBoundStore } from "zustand";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatTime, formatUsd } from "@/lib/utils";
import type { TradingState } from "@/store/useTradingStore";
import type { EquityPoint } from "@/lib/types";

interface EquityTooltipProps {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: EquityPoint }>;
}

function EquityTooltip({ active, payload }: EquityTooltipProps) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  return (
    <div className="glass-panel rounded-md px-3 py-2 text-xs font-mono">
      <div className="text-slate-400">{formatTime(point.time * 1000)}</div>
      <div className="text-slate-100 font-semibold">{formatUsd(point.equity)}</div>
      <div className={point.drawdownPct < 0 ? "text-loss" : "text-profit"}>
        {point.drawdownPct.toFixed(3)}% from HWM
      </div>
    </div>
  );
}

export function PnlChart({ store }: { store: UseBoundStore<StoreApi<TradingState>> }) {
  const equityCurve = store((s) => s.equityCurve);
  const uid = useId();
  const equityGradientId = `equityGradient-${uid}`;
  const drawdownGradientId = `drawdownGradient-${uid}`;
  const startingEquity = equityCurve[0]?.equity ?? 0;
  const last = equityCurve[equityCurve.length - 1];
  const netChange = last ? last.equity - startingEquity : 0;
  const isUp = netChange >= 0;

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>Intraday P&amp;L Curve</CardTitle>
        <span className={`text-xs font-mono tabular-nums ${isUp ? "text-profit" : "text-loss"}`}>
          {isUp ? "+" : ""}
          {formatUsd(netChange, { signed: false })}
        </span>
      </CardHeader>
      <CardContent className="p-2 flex flex-col gap-1">
        <div className="h-[120px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={equityCurve} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id={equityGradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={isUp ? "#10b981" : "#ef4444"} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={isUp ? "#10b981" : "#ef4444"} stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="time" hide />
              <YAxis domain={["dataMin - 200", "dataMax + 200"]} hide />
              <Tooltip content={EquityTooltip} />
              <Area
                type="monotone"
                dataKey="equity"
                stroke={isUp ? "#10b981" : "#ef4444"}
                strokeWidth={1.5}
                fill={`url(#${equityGradientId})`}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div className="h-[48px] w-full">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 font-sans mb-1">
            Drawdown from high-water mark
          </div>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={equityCurve} margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id={drawdownGradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#ef4444" stopOpacity={0.05} />
                  <stop offset="100%" stopColor="#ef4444" stopOpacity={0.45} />
                </linearGradient>
              </defs>
              <XAxis dataKey="time" hide />
              <YAxis domain={["dataMin - 0.05", 0]} hide />
              <Area
                type="monotone"
                dataKey="drawdownPct"
                stroke="#ef4444"
                strokeWidth={1}
                fill={`url(#${drawdownGradientId})`}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
