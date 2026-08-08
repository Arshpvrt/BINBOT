"use client";

import { motion } from "framer-motion";

import { cn } from "@/lib/utils";

const SIZE = 92;
const STROKE = 8;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function RadialGauge({
  pct,
  label,
  valueLabel,
  tone = "system",
}: {
  pct: number; // 0-100
  label: string;
  valueLabel: string;
  tone?: "system" | "pending" | "loss";
}) {
  const clamped = Math.max(0, Math.min(100, pct));
  const color = clamped >= 90 ? "#ef4444" : clamped >= 65 ? "#f59e0b" : tone === "loss" ? "#ef4444" : "#06b6d4";

  return (
    <div className="flex items-center gap-3">
      <div className="relative" style={{ width: SIZE, height: SIZE }}>
        <svg width={SIZE} height={SIZE} className="-rotate-90">
          <circle cx={SIZE / 2} cy={SIZE / 2} r={RADIUS} stroke="rgba(148,163,184,0.12)" strokeWidth={STROKE} fill="none" />
          <motion.circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={RADIUS}
            stroke={color}
            strokeWidth={STROKE}
            fill="none"
            strokeLinecap="round"
            strokeDasharray={CIRCUMFERENCE}
            initial={{ strokeDashoffset: CIRCUMFERENCE }}
            animate={{ strokeDashoffset: CIRCUMFERENCE * (1 - clamped / 100) }}
            transition={{ duration: 0.6, ease: "easeOut" }}
            style={{ filter: `drop-shadow(0 0 6px ${color}66)` }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-sm font-bold font-mono tabular-nums text-slate-100">{clamped.toFixed(0)}%</span>
        </div>
      </div>
      <div className="flex flex-col gap-0.5">
        <span className="text-[10px] uppercase tracking-wider text-slate-500 font-sans">{label}</span>
        <span className={cn("text-xs font-mono tabular-nums", clamped >= 90 ? "text-loss" : "text-slate-300")}>
          {valueLabel}
        </span>
      </div>
    </div>
  );
}
