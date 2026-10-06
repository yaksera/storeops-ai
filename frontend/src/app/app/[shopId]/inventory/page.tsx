"use client";

import { useQuery } from "@tanstack/react-query";
import { Boxes } from "lucide-react";
import Link from "next/link";

import { EmptyState, ErrorState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { useShop } from "@/components/shop-context";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { InventoryRow } from "@/lib/agents";
import { api } from "@/lib/api";
import { cn, formatMoney } from "@/lib/utils";

const SCALE_DAYS = 60;

function StockoutBar({ row }: { row: InventoryRow }) {
  const days = row.days_to_stockout;
  if (days === null) return <span className="text-xs text-muted-foreground">No recent sales</span>;
  const critical = days <= row.lead_time_days;
  const warning = !critical && days <= row.lead_time_days + 7;
  const width = Math.max(2, Math.min(100, (days / SCALE_DAYS) * 100));
  return (
    <div className="flex items-center gap-3">
      <div
        className="h-2 w-28 overflow-hidden rounded-full bg-muted"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={SCALE_DAYS}
        aria-valuenow={Math.min(days, SCALE_DAYS)}
        aria-label={`${days} days to stockout`}
      >
        <div
          className={cn(
            "h-full rounded-full",
            critical ? "bg-destructive" : warning ? "bg-warning" : "bg-viz-current",
          )}
          style={{ width: `${width}%` }}
        />
      </div>
      <span className="tabular text-sm">{days >= 365 ? "365+" : days.toFixed(days < 10 ? 1 : 0)}d</span>
    </div>
  );
}

export default function InventoryPage() {
  const shop = useShop();
  const data = useQuery({
    queryKey: ["shop", shop.id, "inventory"],
    queryFn: () => api<{ currency: string; items: InventoryRow[] }>(`/api/shops/${shop.id}/inventory`),
    refetchInterval: 15_000,
  });
  const needs = data.data?.items.filter((i) => i.needs_reorder).length ?? 0;

  return (
    <>
      <PageHeader
        title="Inventory"
        description={
          data.data
            ? `${needs} SKU${needs === 1 ? "" : "s"} need reordering. Forecast from 30/90-day sales with weekday seasonality.`
            : "Stock levels and forecasted days until stockout."
        }
      />
      {data.isPending ? (
        <Skeleton className="h-96 rounded-xl" />
      ) : data.isError ? (
        <ErrorState message={data.error.message} onRetry={() => data.refetch()} />
      ) : data.data.items.length === 0 ? (
        <EmptyState
          icon={Boxes}
          title="No products yet"
          description="Products sync from your store automatically."
        />
      ) : (
        <div className="overflow-x-auto rounded-xl border bg-card">
          <table className="w-full min-w-[52rem] text-sm">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2.5 font-medium">Product</th>
                <th className="px-4 py-2.5 text-right font-medium">On hand</th>
                <th className="px-4 py-2.5 font-medium">Days to stockout</th>
                <th className="px-4 py-2.5 text-right font-medium">Units / day</th>
                <th className="px-4 py-2.5 text-right font-medium">Reorder point</th>
                <th className="px-4 py-2.5 text-right font-medium">Suggested order</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data.data.items.map((row) => (
                <tr key={row.variant_id}>
                  <td className="px-4 py-2.5">
                    <p className="font-medium">{row.product}</p>
                    <p className="text-xs text-muted-foreground">
                      {row.variant} · {row.sku} · {formatMoney(row.price_minor, data.data.currency)}
                    </p>
                  </td>
                  <td className="tabular px-4 py-2.5 text-right">{row.on_hand}</td>
                  <td className="px-4 py-2.5">
                    <StockoutBar row={row} />
                  </td>
                  <td className="tabular px-4 py-2.5 text-right">{row.daily_rate.toFixed(1)}</td>
                  <td className="tabular px-4 py-2.5 text-right">{row.reorder_point}</td>
                  <td className="tabular px-4 py-2.5 text-right">{row.suggested_order_qty || "–"}</td>
                  <td className="px-4 py-2.5">
                    {row.po_pending ? (
                      <Link href={`/app/${shop.id}/approvals`}>
                        <Badge variant="info">PO awaiting approval</Badge>
                      </Link>
                    ) : row.on_hand <= 0 ? (
                      <Badge variant="destructive">Out of stock</Badge>
                    ) : row.needs_reorder ? (
                      <Badge variant="warning">Reorder</Badge>
                    ) : (
                      <Badge variant="success">Healthy</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
