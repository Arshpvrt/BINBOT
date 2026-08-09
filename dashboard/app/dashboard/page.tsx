"use client";

import { TelemetryRibbon } from "@/components/dashboard/telemetry-ribbon";
import { MultiPositionChartGrid } from "@/components/dashboard/multi-position-chart-grid";
import { PnlChart } from "@/components/dashboard/pnl-chart";
import { OrderBlotter } from "@/components/dashboard/order-blotter";
import { RiskCommand } from "@/components/dashboard/risk-command";
import { AuditStream } from "@/components/dashboard/audit-stream";
import { OpenTradesTable } from "@/components/dashboard/open-trades-table";
import { ClosedTradesTable } from "@/components/dashboard/closed-trades-table";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useScannerStore } from "@/store/useScannerStore";

export default function DashboardPage() {
  useWebSocket(useScannerStore, process.env.NEXT_PUBLIC_SCANNER_WS_URL);

  return (
    <div className="flex min-h-screen flex-col">
      <TelemetryRibbon store={useScannerStore} strategyLabel="Funding-Momentum Scanner" />
      <main className="flex-1 grid grid-cols-1 lg:grid-cols-12 gap-4 p-4">
        <div className="lg:col-span-8 flex flex-col gap-4">
          <MultiPositionChartGrid store={useScannerStore} />
          <PnlChart store={useScannerStore} />
        </div>
        <div className="lg:col-span-4">
          <RiskCommand store={useScannerStore} />
        </div>
        <div className="lg:col-span-8">
          <OrderBlotter store={useScannerStore} />
        </div>
        <div className="lg:col-span-4">
          <AuditStream store={useScannerStore} />
        </div>
        <div className="lg:col-span-6">
          <OpenTradesTable store={useScannerStore} />
        </div>
        <div className="lg:col-span-6">
          <ClosedTradesTable store={useScannerStore} />
        </div>
      </main>
    </div>
  );
}
