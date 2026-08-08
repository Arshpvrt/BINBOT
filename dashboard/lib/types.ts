/**
 * Domain types mirroring the Python ATS event model (core/events.py,
 * core/enums.py, risk/, execution/). Keeping these aligned makes it a
 * mechanical swap to wire the real Redis pub/sub → WebSocket bridge in
 * later instead of the mock engine in hooks/useWebSocket.ts.
 */

export type ConnectionState = "connected" | "degraded" | "disconnected";

export interface SystemStatus {
  broker: ConnectionState;
  redis: ConnectionState;
  engine: ConnectionState;
  latencyMs: number;
}

export interface PortfolioKpis {
  portfolioValue: number;
  realizedPnl: number;
  unrealizedPnl: number;
  sharpeRatio: number;
  marginUsedUsd: number;
  marginLimitUsd: number;
}

export type OrderSide = "BUY" | "SELL";
export type OrderStatusValue = "WORKING" | "PARTIAL" | "FILLED" | "CANCELED" | "REJECTED";

export interface OrderRow {
  id: string;
  symbol: string;
  side: OrderSide;
  orderType: "MKT" | "LMT" | "STP";
  quantity: number;
  filledQuantity: number;
  limitPrice: number | null;
  avgFillPrice: number | null;
  status: OrderStatusValue;
  strategyId: string;
  slippageTicks: number | null;
  submittedAt: number; // epoch ms
  updatedAt: number; // epoch ms
  rejectReason?: string;
}

export interface Candle {
  symbol: string;
  time: number; // unix seconds, required by lightweight-charts
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface ExecutionMarker {
  symbol: string;
  time: number; // unix seconds
  price: number;
  side: OrderSide;
  quantity: number;
}

export interface OpenPositionDetail {
  symbol: string;
  side: OrderSide;
  quantity: number;
  entryPrice: number;
  markPrice: number;
  unrealizedPnl: number;
  openedAt: number | null; // epoch ms
}

export interface ClosedTradeRecord {
  symbol: string;
  side: OrderSide;
  quantity: number;
  entryPrice: number;
  exitPrice: number;
  pnl: number;
  openedAt: number; // epoch ms
  closedAt: number; // epoch ms
  durationSec: number;
}

export interface EquityPoint {
  time: number; // unix seconds
  equity: number;
  drawdownPct: number;
}

export interface RiskLimits {
  maxDailyLossUsd: number;
  dailyLossUsedUsd: number;
  maxPositionContracts: number;
  positionContractsUsed: number;
  strategyPaused: boolean;
  circuitBreakerHalted: boolean;
  haltReason: string | null;
}

export type AuditLevel = "info" | "signal" | "risk" | "fill" | "warn" | "error";

export interface AuditEvent {
  id: string;
  ts: number; // epoch ms
  level: AuditLevel;
  source: string;
  message: string;
  meta?: Record<string, string | number>;
}
