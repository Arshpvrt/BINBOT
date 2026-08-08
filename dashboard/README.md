# BIN BOT — Institutional Quant OEMS Dashboard

A real-time Order & Execution Management System (OEMS) dashboard for the BIN BOT futures ATS. Next.js 16 (App Router) + Tailwind CSS v4 + shadcn-style primitives + Framer Motion + TradingView `lightweight-charts` + Recharts + Zustand.

## Stack

- **Next.js 16** (App Router, Turbopack, React 19)
- **Tailwind CSS v4** — CSS-first config; the design system (obsidian palette, glow shadows, flash-on-fill keyframes) lives in [`app/globals.css`](app/globals.css)'s `@theme` block — there is no separate `tailwind.config.ts` in v4
- **shadcn-style primitives** in `components/ui/` (Button, Card, Badge, Tabs, Dialog, Progress, Separator), hand-built on the installed Radix packages so they're a drop-in for `npx shadcn add` later
- **Framer Motion** — kill-switch modal step transitions, radial gauge fill animation, slide-to-confirm drag widget
- **`lightweight-charts` v5** — candlestick price panel with buy/sell execution markers via `createSeriesMarkers`
- **Recharts** — intraday equity curve + drawdown-from-high-water-mark strip
- **Zustand** — single store (`store/useTradingStore.ts`) holding connection status, KPIs, candles, orders, risk limits, and the audit log

## Run it

```bash
npm install
npm run dev
```

Visit `http://localhost:3000` (redirects to `/dashboard`).

## Live data — currently mocked

`hooks/useWebSocket.ts` runs a self-contained simulated engine (`MockTradingEngine`) that mirrors the causal shape of the real Python ATS: price ticks → Kalman-filter pairs signal → risk pre-trade checks → order submission → fill, all logged to the audit stream exactly like the real system's structured logs. Every component reads exclusively from `useTradingStore` — nothing talks to the mock engine directly.

**To wire up the real backend**, replace the body of `hooks/useWebSocket.ts` with a real WebSocket/Redis-pub-sub client that calls the same store actions (`pushCandle`, `upsertOrder`, `pushAuditEvent`, etc.) — no other file needs to change. `lib/types.ts` mirrors the Python side's `core/events.py` / `core/enums.py` / `risk/` types, so the shape should translate directly from the backend's JSON event payloads.

## Panels

| Panel | File |
|---|---|
| Telemetry ribbon (connection badges, KPIs) | `components/dashboard/telemetry-ribbon.tsx` |
| Candlestick chart + execution markers | `components/dashboard/price-chart.tsx` |
| Intraday P&L curve + drawdown | `components/dashboard/pnl-chart.tsx` |
| Order blotter (Working / Fills / Rejected) | `components/dashboard/order-blotter.tsx` |
| Risk command (gauges, pause, flatten, kill switch) | `components/dashboard/risk-command.tsx` |
| Audit & risk event stream | `components/dashboard/audit-stream.tsx` |

## Notes

- The kill switch requires two explicit confirmations (`kill-switch-dialog.tsx`) before it engages, and re-arming is a separate deliberate action.
- "Flatten positions" is a Framer Motion drag-to-confirm slider (`slide-to-confirm.tsx`) rather than a single click, to avoid accidental liquidation.
- `npm run build` and `npx tsc --noEmit` both pass clean.
