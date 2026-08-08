import { createTradingStore } from "@/store/useTradingStore";

/** The funding-momentum scanner bot's store — same shape as
 * useTradingStore, a fully independent instance so the two strategies'
 * live state never leaks into each other. */
export const useScannerStore = createTradingStore();
