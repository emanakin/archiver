import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
const variants = cva("inline-flex min-h-11 items-center justify-center rounded-xl px-4 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-300 disabled:pointer-events-none disabled:opacity-50", {variants:{variant:{default:"bg-emerald-300 text-zinc-950 hover:bg-emerald-200",outline:"border border-zinc-700 hover:bg-zinc-800"}},defaultVariants:{variant:"default"}});
export function Button({className,variant,asChild=false,...props}: React.ComponentProps<"button"> & VariantProps<typeof variants> & {asChild?:boolean}) {const Comp=asChild?Slot:"button";return <Comp className={cn(variants({variant}),className)} {...props}/>;}
