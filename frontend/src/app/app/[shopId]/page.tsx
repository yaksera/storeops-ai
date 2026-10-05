"use client";

import { useQueryClient } from "@tanstack/react-query";

import { ErrorState } from "@/components/empty-state";
import { ActivityStream } from "@/components/live/activity-stream";
import { AgentPanel } from "@/components/live/agent-panel";
import { ConnectionBadge, ScenarioMenu, SimulatorToggle } from "@/components/live/controls";
import { KpiTicker, KpiTickerSkeleton } from "@/components/live/kpi-ticker";
import { OrderFeed } from "@/components/live/order-feed";
import { RevenueChart } from "@/components/live/revenue-chart";
import { PageHeader } from "@/components/page-header";
import { hasRole, useShop } from "@/components/shop-context";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useLiveOps, useNow } from "@/hooks/use-live-ops";
import { pendingKey } from "@/lib/agents";

function currentLocalHour(timeZone: string, now: number): number {
  return (
    Number(new Intl.DateTimeFormat("en-US", { hour: "numeric", hourCycle: "h23", timeZone }).format(now)) % 24
  );
}

export default function LiveOpsPage() {
  const shop = useShop();
  const now = useNow();
  const queryClient = useQueryClient();
  const { snapshot, state, status, setState } = useLiveOps(shop.id, shop.timezone, (event) => {
    if (event.type.startsWith("proposal."))
      void queryClient.invalidateQueries({ queryKey: pendingKey(shop.id) });
  });
  const canOperate = hasRole(shop.role, "admin");
  const sim = snapshot.data?.simulator;

  return (
    <>
      <PageHeader
        title="Live Ops"
        description={
          <span className="inline-flex items-center gap-2">
            {shop.name}
            <Badge variant={shop.mode === "demo" ? "info" : "success"}>
              {shop.mode === "demo" ? "Demo store" : "Live store"}
            </Badge>
          </span>
        }
        actions={
          <>
            <ConnectionBadge status={status} />
            {sim && state && (
              <>
                <SimulatorToggle
                  shopId={shop.id}
                  running={state.simulatorRunning ?? true}
                  disabled={!canOperate}
                  onChange={(running) => setState((s) => (s ? { ...s, simulatorRunning: running } : s))}
                />
                <ScenarioMenu shopId={shop.id} scenarios={sim.scenarios} disabled={!canOperate} />
              </>
            )}
          </>
        }
      />

      {snapshot.isError && !state ? (
        <ErrorState message={snapshot.error.message} onRetry={() => snapshot.refetch()} />
      ) : !state ? (
        <div className="grid gap-4">
          <KpiTickerSkeleton />
          <div className="grid gap-4 lg:grid-cols-3">
            <Skeleton className="h-80 rounded-xl lg:col-span-2" />
            <Skeleton className="h-80 rounded-xl" />
          </div>
        </div>
      ) : (
        <div className="grid gap-4">
          <KpiTicker kpis={state.kpis} />
          <div className="grid gap-4 lg:grid-cols-5">
            <div className="grid min-w-0 content-start gap-4 lg:col-span-3">
              <RevenueChart
                today={state.revenueByHour.today}
                lastWeek={state.revenueByHour.last_week}
                currency={state.kpis.currency}
                currentHour={currentLocalHour(shop.timezone, now)}
              />
              <OrderFeed orders={state.orders} now={now} />
            </div>
            <div className="grid min-w-0 content-start gap-4 lg:col-span-2">
              <AgentPanel agents={state.agents} now={now} />
              <ActivityStream items={state.activity} now={now} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
