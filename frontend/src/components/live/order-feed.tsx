"use client";

import { PackageOpen, ShieldAlert, ShieldCheck, Truck, Undo2 } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { OrderSummary } from "@/lib/live";
import { cn, formatMoney, timeAgo } from "@/lib/utils";

function RiskBadge({ order }: { order: OrderSummary }) {
  if (order.is_held)
    return (
      <Badge variant="destructive">
        <ShieldAlert aria-hidden /> Held
      </Badge>
    );
  if (order.risk_score === null) return null;
  if (order.risk_score >= 70)
    return (
      <Badge variant="destructive">
        <ShieldAlert aria-hidden /> Risk {order.risk_score}
      </Badge>
    );
  if (order.risk_score >= 40)
    return (
      <Badge variant="warning">
        <ShieldAlert aria-hidden /> Risk {order.risk_score}
      </Badge>
    );
  return (
    <Badge variant="success">
      <ShieldCheck aria-hidden /> Low risk
    </Badge>
  );
}

function StatusBadges({ order }: { order: OrderSummary }) {
  return (
    <>
      {order.cancelled && <Badge variant="outline">Cancelled</Badge>}
      {order.financial_status === "pending" && <Badge variant="warning">Payment pending</Badge>}
      {order.refunded_minor > 0 && (
        <Badge variant="outline">
          <Undo2 aria-hidden /> {order.financial_status === "refunded" ? "Refunded" : "Part refund"}
        </Badge>
      )}
      {order.fulfillment_status === "fulfilled" && (
        <Badge variant="secondary">
          <Truck aria-hidden /> Shipped
        </Badge>
      )}
    </>
  );
}

export function OrderFeed({ orders, now }: { orders: OrderSummary[]; now: number }) {
  return (
    <Card className="gap-2 pb-2">
      <CardHeader>
        <CardTitle>Live orders</CardTitle>
        <CardDescription>Newest first, with Fraud Guard&apos;s verdict as it lands.</CardDescription>
      </CardHeader>
      <CardContent className="px-2">
        {orders.length === 0 ? (
          <EmptyState
            icon={PackageOpen}
            title="No orders yet"
            description="New orders appear here instantly."
          />
        ) : (
          <ul aria-live="polite" aria-relevant="additions" className="max-h-[28rem] overflow-y-auto">
            <AnimatePresence initial={false}>
              {orders.map((order) => {
                const mismatch =
                  order.billing_country &&
                  order.shipping_country &&
                  order.billing_country !== order.shipping_country;
                return (
                  <motion.li
                    key={order.id}
                    layout
                    initial={{ opacity: 0, backgroundColor: "oklch(0.8 0.13 195 / 0.12)" }}
                    animate={{ opacity: 1, backgroundColor: "oklch(0.8 0.13 195 / 0)" }}
                    transition={{ duration: 1.2 }}
                    className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg px-3 py-2.5"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-2 text-sm">
                        <span className="font-medium">{order.name}</span>
                        <span className="truncate text-muted-foreground">
                          {order.customer_name ?? "Guest"}
                          {order.customer_orders === 1 && " · new customer"}
                        </span>
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {order.item_count} item{order.item_count === 1 ? "" : "s"}
                        {order.shipping_country && ` · ships to ${order.shipping_country}`}
                        {mismatch && (
                          <span className="text-warning"> · billed in {order.billing_country}</span>
                        )}
                        {order.source && ` · ${order.source}`}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <RiskBadge order={order} />
                      <StatusBadges order={order} />
                    </div>
                    <div className="w-24 text-right">
                      <p
                        className={cn(
                          "tabular text-sm font-medium",
                          order.cancelled && "line-through opacity-60",
                        )}
                      >
                        {formatMoney(order.total_minor, order.currency)}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        <time dateTime={order.processed_at}>{timeAgo(order.processed_at, now)}</time>
                      </p>
                    </div>
                  </motion.li>
                );
              })}
            </AnimatePresence>
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
