"use client";

import { useQuery } from "@tanstack/react-query";
import { ShoppingCart } from "lucide-react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { EmptyState, ErrorState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { useShop } from "@/components/shop-context";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useNow } from "@/hooks/use-live-ops";
import type { RecoveryData } from "@/lib/agents";
import { api } from "@/lib/api";
import { formatMoney, formatNumber, formatPercent, timeAgo } from "@/lib/utils";

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border bg-card px-4 py-3.5">
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
      <p className="tabular mt-1.5 text-2xl font-semibold tracking-tight">{value}</p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

const STATUS: Record<string, { label: string; variant: "success" | "warning" | "secondary" }> = {
  recovered: { label: "Recovered", variant: "success" },
  abandoned: { label: "Abandoned", variant: "warning" },
};

export default function RecoveryPage() {
  const shop = useShop();
  const now = useNow(30_000);
  const data = useQuery({
    queryKey: ["shop", shop.id, "recovery"],
    queryFn: () => api<RecoveryData>(`/api/shops/${shop.id}/recovery`),
    refetchInterval: 15_000,
  });

  return (
    <>
      <PageHeader
        title="Recovery"
        description="Abandoned checkouts, follow-ups sent and revenue won back (last 14 days)."
      />
      {data.isPending ? (
        <div className="grid gap-4">
          <Skeleton className="h-24 rounded-xl" />
          <Skeleton className="h-72 rounded-xl" />
        </div>
      ) : data.isError ? (
        <ErrorState message={data.error.message} onRetry={() => data.refetch()} />
      ) : (
        <div className="grid gap-4">
          <section
            aria-label="Recovery metrics"
            className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5"
          >
            <Stat label="Abandoned carts" value={formatNumber(data.data.stats.abandoned_14d)} />
            <Stat label="Emails sent" value={formatNumber(data.data.stats.emails_sent_14d)} />
            <Stat label="Recovered orders" value={formatNumber(data.data.stats.recovered_orders_14d)} />
            <Stat
              label="Recovered revenue"
              value={formatMoney(data.data.stats.recovered_revenue_14d_minor, data.data.currency)}
            />
            <Stat
              label="Recovery rate"
              value={formatPercent(data.data.stats.recovery_rate)}
              hint="of emailed carts"
            />
          </section>

          <Card className="gap-2">
            <CardHeader>
              <CardTitle>Recovered revenue per day</CardTitle>
              <CardDescription>Orders placed after a recovery email.</CardDescription>
            </CardHeader>
            <CardContent className="px-2 sm:px-4">
              <div className="h-56" role="img" aria-label="Bar chart of recovered revenue per day">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.data.series} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                    <CartesianGrid vertical={false} stroke="var(--border)" />
                    <XAxis
                      dataKey="date"
                      tickFormatter={(d: string) => d.slice(5)}
                      tickLine={false}
                      axisLine={false}
                      interval={1}
                      tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                    />
                    <YAxis
                      width={52}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={(v: number) => formatMoney(v, data.data.currency, true)}
                      tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                    />
                    <Tooltip
                      cursor={{ fill: "var(--accent)", opacity: 0.4 }}
                      content={({ active, payload }) =>
                        active && payload?.length ? (
                          <div className="rounded-lg border bg-popover px-3 py-2 text-xs shadow-xl">
                            <p className="font-medium">{String(payload[0].payload.date)}</p>
                            <p className="tabular">
                              {formatMoney(Number(payload[0].value), data.data.currency)}
                            </p>
                          </div>
                        ) : null
                      }
                    />
                    <Bar
                      dataKey="recovered_minor"
                      fill="var(--viz-current)"
                      radius={[4, 4, 0, 0]}
                      maxBarSize={28}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>

          <Card className="gap-2 pb-2">
            <CardHeader>
              <CardTitle>Recent abandoned carts</CardTitle>
              <CardDescription>
                Cart Recovery emails shoppers who consented to marketing, up to two reminders per cart.
              </CardDescription>
            </CardHeader>
            <CardContent className="px-2">
              {data.data.carts.length === 0 ? (
                <EmptyState icon={ShoppingCart} title="No abandoned carts in the last two days" />
              ) : (
                <ul className="divide-y">
                  {data.data.carts.map((cart) => {
                    const status = STATUS[cart.status] ?? {
                      label: cart.status,
                      variant: "secondary" as const,
                    };
                    return (
                      <li key={cart.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2.5">
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium">
                            {cart.customer_name ?? "Shopper"}{" "}
                            <span className="font-normal text-muted-foreground">
                              · {cart.items.join(", ") || "items"}
                            </span>
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {cart.created_at && `Started ${timeAgo(cart.created_at, now)}`}
                            {cart.reminders_sent > 0 &&
                              ` · ${cart.reminders_sent} reminder${cart.reminders_sent === 1 ? "" : "s"} sent`}
                            {!cart.consent && " · no marketing consent"}
                          </p>
                        </div>
                        <Badge variant={status.variant}>{status.label}</Badge>
                        <span className="tabular w-24 text-right text-sm">
                          {formatMoney(cart.total_minor, data.data.currency)}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </>
  );
}
