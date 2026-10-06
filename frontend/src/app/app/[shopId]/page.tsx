"use client";

import { Radar } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { useShop } from "@/components/shop-context";
import { StatusDot } from "@/components/status-dot";
import { Badge } from "@/components/ui/badge";

export default function LiveOpsPage() {
  const shop = useShop();
  return (
    <>
      <PageHeader
        title="Live Ops"
        description={shop.name}
        actions={
          <Badge variant={shop.mode === "demo" ? "info" : "success"} className="gap-2">
            <StatusDot tone={shop.mode === "demo" ? "busy" : "live"} />{" "}
            {shop.mode === "demo" ? "Demo" : "Live"}
          </Badge>
        }
      />
      <EmptyState
        icon={Radar}
        title="Waiting for the first events"
        description="Orders, stock changes and agent activity will stream in here as they happen."
      />
    </>
  );
}
