"use client";

import { useEffect, useRef, useState } from "react";
import { motion, useMotionValue, animate } from "framer-motion";
import { ChevronsRight, Check } from "lucide-react";

import { cn } from "@/lib/utils";

const THUMB_SIZE = 36;
const CONFIRM_THRESHOLD = 0.78;

export function SlideToConfirm({
  label,
  confirmedLabel,
  onConfirm,
  disabled,
}: {
  label: string;
  confirmedLabel: string;
  onConfirm: () => void;
  disabled?: boolean;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [maxDrag, setMaxDrag] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  const x = useMotionValue(0);

  useEffect(() => {
    const measure = () => {
      if (trackRef.current) setMaxDrag(trackRef.current.offsetWidth - THUMB_SIZE - 8);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const reset = () => {
    animate(x, 0, { type: "spring", stiffness: 400, damping: 30 });
  };

  const handleDragEnd = () => {
    if (maxDrag <= 0) return;
    if (x.get() >= maxDrag * CONFIRM_THRESHOLD) {
      animate(x, maxDrag, { type: "spring", stiffness: 300, damping: 26 });
      setConfirmed(true);
      onConfirm();
      window.setTimeout(() => {
        setConfirmed(false);
        reset();
      }, 1800);
    } else {
      reset();
    }
  };

  return (
    <div
      ref={trackRef}
      className={cn(
        "relative h-11 w-full rounded-full border overflow-hidden select-none",
        confirmed ? "border-profit/50 bg-profit/10" : "border-pending/40 bg-pending/10",
        disabled && "opacity-40 pointer-events-none"
      )}
    >
      <div className="absolute inset-0 flex items-center justify-center gap-1.5 text-xs font-medium font-sans tracking-wide">
        {confirmed ? (
          <span className="text-profit flex items-center gap-1.5">
            <Check className="size-3.5" /> {confirmedLabel}
          </span>
        ) : (
          <span className="text-pending/90 flex items-center gap-1">
            {label} <ChevronsRight className="size-3.5 animate-pulse" />
          </span>
        )}
      </div>
      <motion.div
        drag={!confirmed && !disabled ? "x" : false}
        dragConstraints={{ left: 0, right: maxDrag }}
        dragElastic={0}
        dragMomentum={false}
        onDragEnd={handleDragEnd}
        style={{ x, width: THUMB_SIZE, height: THUMB_SIZE }}
        className={cn(
          "absolute left-1 top-1/2 -translate-y-1/2 rounded-full flex items-center justify-center cursor-grab active:cursor-grabbing shadow-lg",
          confirmed ? "bg-profit text-obsidian" : "bg-pending text-obsidian"
        )}
      >
        {confirmed ? <Check className="size-4" /> : <ChevronsRight className="size-4" />}
      </motion.div>
    </div>
  );
}
