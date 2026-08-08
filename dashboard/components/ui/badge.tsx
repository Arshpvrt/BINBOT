import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider font-sans w-fit",
  {
    variants: {
      variant: {
        neutral: "bg-slate-800/60 border-slate-700 text-slate-300",
        profit: "bg-profit/10 border-profit/40 text-profit",
        loss: "bg-loss/10 border-loss/40 text-loss",
        pending: "bg-pending/10 border-pending/40 text-pending",
        system: "bg-system/10 border-system/40 text-system",
      },
    },
    defaultVariants: {
      variant: "neutral",
    },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant, className }))} {...props} />;
}

export { Badge, badgeVariants };
