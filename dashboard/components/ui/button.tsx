import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium font-sans transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-obsidian disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-panel border border-panel-border text-slate-100 hover:bg-slate-800/60",
        profit:
          "bg-profit/10 border border-profit/40 text-profit hover:bg-profit/20 hover:shadow-glow-profit focus-visible:ring-profit",
        loss: "bg-loss/10 border border-loss/40 text-loss hover:bg-loss/20 hover:shadow-glow-loss focus-visible:ring-loss",
        pending:
          "bg-pending/10 border border-pending/40 text-pending hover:bg-pending/20 hover:shadow-glow-pending focus-visible:ring-pending",
        ghost: "text-slate-300 hover:bg-slate-800/60 hover:text-slate-100",
        outline: "border border-panel-border bg-transparent text-slate-300 hover:bg-slate-800/40",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 rounded-md px-3 text-xs",
        lg: "h-11 rounded-md px-6 text-base",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size, className }))} {...props} />;
}

export { Button, buttonVariants };
