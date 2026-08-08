"use client";

import type { StoreApi, UseBoundStore } from "zustand";
import { Pause, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { formatUsd } from "@/lib/utils";
import type { TradingState } from "@/store/useTradingStore";
import { RadialGauge } from "@/components/dashboard/radial-gauge";
import { SlideToConfirm } from "@/components/dashboard/slide-to-confirm";
import { KillSwitchDialog } from "@/components/dashboard/kill-switch-dialog";

export function RiskCommand({ store }: { store: UseBoundStore<StoreApi<TradingState>> }) {
  const risk = store((s) => s.risk);
  const togglePauseStrategy = store((s) => s.togglePauseStrategy);
  const triggerFlatten = store((s) => s.triggerFlatten);

  const lossPct = (risk.dailyLossUsedUsd / risk.maxDailyLossUsd) * 100;
  const positionPct = (risk.positionContractsUsed / risk.maxPositionContracts) * 100;

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>Risk Command</CardTitle>
        {risk.circuitBreakerHalted ? (
          <Badge variant="loss">HALTED</Badge>
        ) : risk.strategyPaused ? (
          <Badge variant="pending">PAUSED</Badge>
        ) : (
          <Badge variant="profit">ACTIVE</Badge>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <div className="flex items-center justify-between gap-4">
          <RadialGauge
            pct={lossPct}
            label="Daily Loss Limit"
            valueLabel={`${formatUsd(risk.dailyLossUsedUsd)} / ${formatUsd(risk.maxDailyLossUsd)}`}
          />
          <RadialGauge
            pct={positionPct}
            label="Max Position Size"
            valueLabel={`${risk.positionContractsUsed} / ${risk.maxPositionContracts} ct`}
          />
        </div>

        <Separator />

        <div className="flex flex-col gap-3">
          <Button
            variant="pending"
            size="lg"
            className="w-full font-semibold tracking-wide"
            onClick={togglePauseStrategy}
          >
            {risk.strategyPaused ? <Play className="size-4" /> : <Pause className="size-4" />}
            {risk.strategyPaused ? "RESUME STRATEGY" : "PAUSE STRATEGY"}
          </Button>

          <SlideToConfirm
            label="SLIDE TO FLATTEN POSITIONS"
            confirmedLabel="FLATTENING..."
            onConfirm={triggerFlatten}
          />

          <KillSwitchDialog store={store} />
        </div>

        {risk.haltReason && (
          <p className="text-[11px] text-loss/90 font-mono text-center -mt-1">{risk.haltReason}</p>
        )}
      </CardContent>
    </Card>
  );
}
