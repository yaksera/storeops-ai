"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2 } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { toast } from "sonner";

import { ErrorState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { hasRole, useShop } from "@/components/shop-context";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { meQueryKey } from "@/hooks/use-me";
import { api, type Plan } from "@/lib/api";
import { cn, formatNumber } from "@/lib/utils";

interface PlanSpec {
  plan: Plan;
  name: string;
  price_usd: string;
  ai_actions: number | null;
  llm_budget_usd: string;
  features: string[];
}

interface BillingData {
  plan: PlanSpec;
  metered: boolean;
  usage: {
    period_start: string;
    ai_actions: number;
    ai_actions_limit: number | null;
    llm_cost_usd: number;
    llm_budget_usd: string;
  };
  subscription: { status: string; provider: string; current_period_end: string | null } | null;
  plans: PlanSpec[];
}

function Meter({
  label,
  used,
  limit,
  format,
}: {
  label: string;
  used: number;
  limit: number | null;
  format: (n: number) => string;
}) {
  const ratio = limit ? Math.min(1, used / limit) : 0;
  return (
    <div className="grid gap-2">
      <div className="flex items-baseline justify-between text-sm">
        <span className="font-medium">{label}</span>
        <span className="tabular text-muted-foreground">
          {format(used)} {limit ? `of ${format(limit)}` : "· unlimited"}
        </span>
      </div>
      <div
        className="h-2 overflow-hidden rounded-full bg-muted"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={limit ?? used}
        aria-valuenow={used}
      >
        <div
          className={cn(
            "h-full rounded-full",
            ratio >= 1 ? "bg-destructive" : ratio >= 0.8 ? "bg-warning" : "bg-viz-current",
          )}
          style={{ width: `${limit ? Math.max(ratio * 100, 1) : 0}%` }}
        />
      </div>
    </div>
  );
}

function Notices() {
  const params = useSearchParams();
  const upgraded = params.get("upgraded");
  const error = params.get("error");
  if (upgraded)
    return (
      <p
        role="status"
        className="mb-4 rounded-md border border-success/30 bg-success/10 px-4 py-3 text-sm text-success"
      >
        You&apos;re now on the {upgraded} plan.
      </p>
    );
  if (error)
    return (
      <p
        role="alert"
        className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
      >
        {error}
      </p>
    );
  return null;
}

export default function BillingPage() {
  const shop = useShop();
  const queryClient = useQueryClient();
  const isOwner = hasRole(shop.role, "owner");
  const data = useQuery({
    queryKey: ["shop", shop.id, "billing"],
    queryFn: () => api<BillingData>(`/api/shops/${shop.id}/billing`),
  });
  const subscribe = useMutation({
    mutationFn: (plan: Plan) =>
      api<{ plan: Plan; confirmation_url: string | null }>(`/api/shops/${shop.id}/billing/subscribe`, {
        json: { plan },
      }),
    onSuccess: (res) => {
      if (res.confirmation_url) {
        window.location.assign(res.confirmation_url);
        return;
      }
      toast.success(`Switched to the ${res.plan} plan`);
      void queryClient.invalidateQueries({ queryKey: ["shop", shop.id, "billing"] });
      void queryClient.invalidateQueries({ queryKey: meQueryKey });
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <>
      <PageHeader
        title="Plan & usage"
        description={
          shop.mode === "demo"
            ? "Demo stores can switch plans instantly and are never charged."
            : "Billed through Shopify. Charges appear on your Shopify invoice."
        }
      />
      <Suspense>
        <Notices />
      </Suspense>
      {data.isPending ? (
        <div className="grid gap-4">
          <Skeleton className="h-40 rounded-xl" />
          <Skeleton className="h-72 rounded-xl" />
        </div>
      ) : data.isError ? (
        <ErrorState message={data.error.message} onRetry={() => data.refetch()} />
      ) : (
        <div className="grid max-w-5xl gap-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                {data.data.plan.name} plan
                {!data.data.metered && <Badge variant="info">Demo · unmetered</Badge>}
              </CardTitle>
              <CardDescription>
                Usage since{" "}
                {new Date(data.data.usage.period_start + "T00:00:00").toLocaleDateString(undefined, {
                  month: "long",
                  day: "numeric",
                })}
                . When a limit is reached agents keep analysing but stop acting until next month.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-5 sm:grid-cols-2">
              <Meter
                label="AI actions"
                used={data.data.usage.ai_actions}
                limit={data.data.metered ? data.data.usage.ai_actions_limit : null}
                format={formatNumber}
              />
              <Meter
                label="AI model spend"
                used={data.data.usage.llm_cost_usd}
                limit={data.data.metered ? Number(data.data.usage.llm_budget_usd) : null}
                format={(n) => `$${n.toFixed(2)}`}
              />
            </CardContent>
          </Card>

          <div className="grid gap-4 md:grid-cols-3">
            {data.data.plans.map((p) => {
              const current = p.plan === data.data.plan.plan;
              return (
                <Card key={p.plan} className={cn(current && "border-primary/40")}>
                  <CardHeader>
                    <CardTitle className="flex items-center justify-between">
                      {p.name}
                      {current && <Badge>Current</Badge>}
                    </CardTitle>
                    <p className="text-3xl font-semibold tracking-tight">
                      ${Number(p.price_usd)}
                      <span className="text-sm font-normal text-muted-foreground"> / month</span>
                    </p>
                  </CardHeader>
                  <CardContent className="grid gap-4">
                    <ul className="grid gap-2 text-sm">
                      {p.features.map((f) => (
                        <li key={f} className="flex gap-2">
                          <Check className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
                          {f}
                        </li>
                      ))}
                    </ul>
                    {isOwner && !current && (
                      <Button
                        variant={p.plan === "free" ? "outline" : "default"}
                        onClick={() => subscribe.mutate(p.plan)}
                        disabled={subscribe.isPending}
                      >
                        {subscribe.isPending && subscribe.variables === p.plan && (
                          <Loader2 className="animate-spin" aria-hidden />
                        )}
                        {p.plan === "free" ? "Downgrade" : `Choose ${p.name}`}
                      </Button>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>
          {!isOwner && (
            <p className="text-sm text-muted-foreground">Only store owners can change the plan.</p>
          )}
        </div>
      )}
    </>
  );
}
