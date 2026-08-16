"use client";

import { useState } from "react";
import type { StoreApi, UseBoundStore } from "zustand";
import { AnimatePresence, motion } from "framer-motion";
import { AlertOctagon, Skull } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import type { TradingState } from "@/store/useTradingStore";

export function KillSwitchDialog({ store }: { store: UseBoundStore<StoreApi<TradingState>> }) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<1 | 2>(1);
  const killSwitchEngaged = store((s) => s.killSwitchEngaged);
  const circuitBreakerHalted = store((s) => s.risk.circuitBreakerHalted);
  const triggerKillSwitch = store((s) => s.triggerKillSwitch);
  const resetKillSwitch = store((s) => s.resetKillSwitch);

  // circuitBreakerHalted covers a halt the RISK ENGINE tripped on its own
  // (e.g. a daily drawdown breach) — killSwitchEngaged only ever becomes
  // true from clicking the button below in THIS browser tab. Gating the
  // reset button on killSwitchEngaged alone meant an automatic halt had no
  // way to be cleared from the dashboard at all.
  if (killSwitchEngaged || circuitBreakerHalted) {
    return (
      <Button
        variant="loss"
        size="lg"
        className="w-full font-bold tracking-wide shadow-glow-loss animate-pulse"
        onClick={resetKillSwitch}
      >
        <AlertOctagon className="size-4" />
        RE-ARM SYSTEM
      </Button>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setStep(1);
      }}
    >
      <DialogTrigger asChild>
        <Button variant="loss" size="lg" className="w-full font-bold tracking-wide shadow-glow-loss">
          <Skull className="size-4" />
          SYSTEM KILL SWITCH
        </Button>
      </DialogTrigger>
      <DialogContent className="border-loss/40">
        <AnimatePresence mode="wait">
          {step === 1 ? (
            <motion.div
              key="step-1"
              initial={{ opacity: 0, x: 12 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -12 }}
              transition={{ duration: 0.18 }}
            >
              <DialogHeader>
                <div className="flex items-center gap-2 text-loss mb-1">
                  <AlertOctagon className="size-5" />
                  <DialogTitle className="text-loss">Kill Switch — Step 1 of 2</DialogTitle>
                </div>
              </DialogHeader>
              <p className="text-sm text-slate-300 font-sans leading-relaxed">
                This will immediately send <span className="font-mono text-slate-100">MKT</span> cancellation
                requests for every working order, halt the strategy signal loop, and set the risk engine circuit
                breaker to <span className="font-mono text-loss">HALTED</span>. This action affects live broker
                state.
              </p>
              <DialogFooter>
                <Button variant="ghost" onClick={() => setOpen(false)}>
                  Cancel
                </Button>
                <Button variant="loss" onClick={() => setStep(2)}>
                  I Understand, Continue
                </Button>
              </DialogFooter>
            </motion.div>
          ) : (
            <motion.div
              key="step-2"
              initial={{ opacity: 0, x: 12 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -12 }}
              transition={{ duration: 0.18 }}
            >
              <DialogHeader>
                <div className="flex items-center gap-2 text-loss mb-1">
                  <Skull className="size-5" />
                  <DialogTitle className="text-loss">Confirm Kill Switch — Step 2 of 2</DialogTitle>
                </div>
              </DialogHeader>
              <p className="text-sm text-slate-300 font-sans leading-relaxed">
                Final confirmation required. This cannot be triggered accidentally from here on. Engage the kill
                switch now?
              </p>
              <DialogFooter>
                <Button variant="ghost" onClick={() => setStep(1)}>
                  Back
                </Button>
                <Button
                  variant="loss"
                  className="shadow-glow-loss-lg font-bold"
                  onClick={() => {
                    triggerKillSwitch();
                    setOpen(false);
                    setStep(1);
                  }}
                >
                  ENGAGE KILL SWITCH
                </Button>
              </DialogFooter>
            </motion.div>
          )}
        </AnimatePresence>
      </DialogContent>
    </Dialog>
  );
}
