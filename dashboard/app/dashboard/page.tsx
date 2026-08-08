"use client";

import { TelemetryRibbon } from "@/components/dashboard/telemetry-ribbon";
import { PriceChart } from "@/components/dashboard/price-chart";
import { MultiPositionChartGrid } from "@/components/dashboard/multi-position-chart-grid";
import { PnlChart } from "@/components/dashboard/pnl-chart";
import { OrderBlotter } from "@/components/dashboard/order-blotter";
import { RiskCommand } from "@/components/dashboard/risk-command";
import { AuditStream } from "@/components/dashboard/audit-stream";
import { OpenTradesTable } from "@/components/dashboard/open-trades-table";
import { ClosedTradesTable } from "@/components/dashboard/closed-trades-table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useTradingStore } from "@/store/useTradingStore";
import { useScannerStore } from "@/store/useScannerStore";

function PairsTradingTab() {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 p-4">
      <div className="lg:col-span-8 flex flex-col gap-4">
        <PriceChart store={useTradingStore} />
        <PnlChart store={useTradingStore} />
      </div>
      <div className="lg:col-span-4">
        <RiskCommand store={useTradingStore} />
      </div>
      <div className="lg:col-span-8">
        <OrderBlotter store={useTradingStore} />
      </div>
      <div className="lg:col-span-4">
        <AuditStream store={useTradingStore} />
      </div>
      <div className="lg:col-span-6">
        <OpenTradesTable store={useTradingStore} />
      </div>
      <div className="lg:col-span-6">
        <ClosedTradesTable store={useTradingStore} />
      </div>
    </div>
  );
}

function ScannerTab() {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 p-4">
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
    </div>
  );
}

export default function DashboardPage() {
  // Kept alive at the page level (not inside TabsContent) so both bots stay
  // connected and their stores keep receiving live data even while the
  // other strategy's tab is the one currently visible.
  useWebSocket(useTradingStore, process.env.NEXT_PUBLIC_LIVE_WS_URL);
  useWebSocket(useScannerStore, process.env.NEXT_PUBLIC_SCANNER_WS_URL);

  return (
    <div className="flex min-h-screen flex-col">
      <Tabs defaultValue="pairs" className="flex-1">
        {/* Not sticky: TelemetryRibbon below is already sticky top-0 per tab,
            and two stacked sticky-top-0 bars overlap instead of stacking. */}
        <div className="glass-panel flex items-center gap-3 rounded-none border-x-0 border-t-0 px-5 py-2">
          <TabsList>
            <TabsTrigger value="pairs">Kalman Pairs Trading</TabsTrigger>
            <TabsTrigger value="scanner">Funding-Momentum Scanner</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="pairs" className="flex-1 flex flex-col">
          <TelemetryRibbon store={useTradingStore} strategyLabel="Kalman Pairs Trading" />
          <PairsTradingTab />
        </TabsContent>
        <TabsContent value="scanner" className="flex-1 flex flex-col">
          <TelemetryRibbon store={useScannerStore} strategyLabel="Funding-Momentum Scanner" />
          <ScannerTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
