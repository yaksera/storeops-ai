"use client";

import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { Skeleton } from "@/components/ui/skeleton";
import type { Kpis } from "@/lib/live";
import { cn, formatMoney, formatNumber, formatPercent } from "@/lib/utils";

function Delta({ current, previous, label }: { current: number; previous: number; label: string }) {
  if (previous <= 0) return <span className="text-muted-foreground">No data {label}</span>;
  const change = (current - previous) / previous;
  const flat = Math.abs(change) < 0.005;
  const Icon = flat ? Minus : change > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1",
        flat ? "text-muted-foreground" : change > 0 ? "text-success" : "text-destructive",
      )}
    >
      <Icon className="size-3.5" aria-hidden />
      <span>
        {flat ? "Flat" : `${change > 0 ? "+" : ""}${formatPercent(change, 0)}`}{" "}
        <span className="text-muted-foreground">{label}</span>
      </span>
    </span>
  );
}

function Tile({
  label,
  value,
  footer,
  highlight = false,
}: {
  label: string;
  value: string;
  footer: React.ReactNode;
  highlight?: boolean;
}) {
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl border bg-card px-4 py-3.5",
        highlight &&
          "border-primary/25 bg-[linear-gradient(135deg,oklch(0.8_0.13_195/0.08),transparent_60%)]",
      )}
    >
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
      <div className="mt-1.5 h-8 overflow-hidden">
        <AnimatePresence mode="popLayout" initial={false}>
          <motion.p
            key={value}
            initial={{ y: 14, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: -14, opacity: 0 }}
            transition={{ duration: 0.25, ease: "easeOut" }}
            className="tabular text-2xl leading-8 font-semibold tracking-tight"
          >
            {value}
          </motion.p>
        </AnimatePresence>
      </div>
      <p className="mt-1 text-xs">{footer}</p>
    </div>
  );
}

export function KpiTicker({ kpis }: { kpis: Kpis }) {
  const c = kpis.currency;
  const aovLastWeek = kpis.orders_last_week ? kpis.revenue_last_week_minor / kpis.orders_last_week : 0;
  return (
    <section
      aria-label="Today's key metrics"
      className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5"
    >
      <Tile
        highlight
        label="Revenue today"
        value={formatMoney(kpis.revenue_minor, c)}
        footer={
          <Delta current={kpis.revenue_minor} previous={kpis.revenue_last_week_minor} label="vs last week" />
        }
      />
      <Tile
        label="Orders"
        value={formatNumber(kpis.orders)}
        footer={<Delta current={kpis.orders} previous={kpis.orders_last_week} label="vs last week" />}
      />
      <Tile
        label="Avg order value"
        value={formatMoney(kpis.aov_minor, c)}
        footer={<Delta current={kpis.aov_minor} previous={aovLastWeek} label="vs last week" />}
      />
      <Tile
        label="Conversion"
        value={formatPercent(kpis.conversion_rate)}
        footer={
          <span className="text-muted-foreground">
            {formatNumber(kpis.orders)} of {formatNumber(kpis.checkouts)} checkouts
          </span>
        }
      />
      <Tile
        label="Recovered revenue"
        value={formatMoney(kpis.recovered_revenue_minor, c)}
        footer={<span className="text-muted-foreground">From abandoned carts today</span>}
      />
    </section>
  );
}

export function KpiTickerSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      {Array.from({ length: 5 }, (_, i) => (
        <div key={i} className="rounded-xl border bg-card px-4 py-3.5">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="mt-3 h-7 w-28" />
          <Skeleton className="mt-2 h-3 w-20" />
        </div>
      ))}
    </div>
  );
}
