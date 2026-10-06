"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { AgentIcon } from "@/components/agent-icon";
import { ErrorState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { hasRole, useShop } from "@/components/shop-context";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import type { AgentConfig, AgentTestResult, Autonomy } from "@/lib/agents";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const AUTONOMY: { value: Autonomy; label: string; hint: string }[] = [
  { value: "off", label: "Off", hint: "Ignores events" },
  { value: "suggest", label: "Suggest", hint: "Every action needs approval" },
  { value: "auto", label: "Auto", hint: "Acts within limits; high-risk still needs approval" },
];

type Patch = Partial<Pick<AgentConfig, "enabled" | "autonomy" | "daily_action_cap">> & {
  settings?: Record<string, unknown>;
};

function SettingsFields({
  config,
  disabled,
  onSave,
}: {
  config: AgentConfig;
  disabled: boolean;
  onSave: (settings: Record<string, unknown>) => void;
}) {
  if (config.agent === "cart_recovery") {
    return (
      <form
        className="grid gap-3 sm:grid-cols-4"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          onSave({
            abandon_after_minutes: Number(f.get("abandon_after_minutes")),
            reminder_interval_minutes: Number(f.get("reminder_interval_minutes")),
            discount_pct: Number(f.get("discount_pct")),
          });
        }}
      >
        <fieldset disabled={disabled} className="contents">
          <label className="grid gap-1 text-xs text-muted-foreground">
            Abandoned after (min)
            <Input
              name="abandon_after_minutes"
              type="number"
              min={1}
              defaultValue={Number(config.settings.abandon_after_minutes)}
            />
          </label>
          <label className="grid gap-1 text-xs text-muted-foreground">
            Between reminders (min)
            <Input
              name="reminder_interval_minutes"
              type="number"
              min={1}
              defaultValue={Number(config.settings.reminder_interval_minutes)}
            />
          </label>
          <label className="grid gap-1 text-xs text-muted-foreground">
            2nd-reminder discount %
            <Input
              name="discount_pct"
              type="number"
              min={0}
              max={10}
              defaultValue={Number(config.settings.discount_pct)}
            />
          </label>
          <div className="flex items-end">
            <Button type="submit" variant="secondary" size="sm">
              Save
            </Button>
          </div>
        </fieldset>
      </form>
    );
  }
  if (config.agent === "inventory_planner") {
    return (
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          const value = String(new FormData(e.currentTarget).get("supplier_email") ?? "").trim();
          onSave({ supplier_email: value || null });
        }}
      >
        <fieldset disabled={disabled} className="contents">
          <label className="grid min-w-64 flex-1 gap-1 text-xs text-muted-foreground">
            Supplier email for purchase orders
            <Input
              name="supplier_email"
              type="email"
              defaultValue={String(config.settings.supplier_email ?? "")}
            />
          </label>
          <Button type="submit" variant="secondary" size="sm">
            Save
          </Button>
        </fieldset>
      </form>
    );
  }
  return null;
}

function TestResult({ result }: { result: AgentTestResult }) {
  const output = Object.entries(result.output ?? {}).filter(([, v]) => typeof v !== "object" || v === null);
  return (
    <div className="grid gap-3 rounded-lg border bg-background/40 p-4 text-sm" aria-live="polite">
      <p className="text-xs text-muted-foreground">
        Sample event: <span className="font-mono text-foreground">{result.event.kind}</span> · nothing was
        saved
      </p>
      {output.length > 0 && (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3">
          {output.map(([key, value]) => (
            <div key={key} className="min-w-0">
              <dt className="text-xs text-muted-foreground">{key.replaceAll("_", " ")}</dt>
              <dd className="truncate font-medium">{String(value)}</dd>
            </div>
          ))}
        </dl>
      )}
      {result.proposals.length === 0 ? (
        <p className="text-muted-foreground">No action would be proposed for this event.</p>
      ) : (
        result.proposals.map((p) => (
          <div key={p.title} className="rounded-md border p-3">
            <p className="font-medium">Would propose: {p.title}</p>
            <p className="mt-1 text-muted-foreground">{p.summary}</p>
          </div>
        ))
      )}
    </div>
  );
}

function AgentCard({ config }: { config: AgentConfig }) {
  const shop = useShop();
  const queryClient = useQueryClient();
  const canEdit = hasRole(shop.role, "admin");
  const [test, setTest] = useState<AgentTestResult | null>(null);
  const update = useMutation({
    mutationFn: (patch: Patch) =>
      api<AgentConfig>(`/api/shops/${shop.id}/agents/${config.agent}`, { method: "PATCH", json: patch }),
    onSuccess: (data) => {
      queryClient.setQueryData<AgentConfig[]>(["shop", shop.id, "agents"], (list) =>
        list?.map((a) => (a.agent === data.agent ? data : a)),
      );
      toast.success(`${data.label} updated`);
    },
    onError: (error) => toast.error(error.message),
  });
  const runTest = useMutation({
    mutationFn: () =>
      api<AgentTestResult>(`/api/shops/${shop.id}/agents/${config.agent}/test`, { method: "POST" }),
    onSuccess: setTest,
    onError: (error) => toast.error(error.message),
  });
  const locked = !canEdit || !config.available || update.isPending;

  return (
    <article className={cn("grid gap-4 rounded-xl border bg-card p-5", !config.available && "opacity-70")}>
      <header className="flex flex-wrap items-start gap-3">
        <AgentIcon agent={config.agent} />
        <div className="min-w-0 flex-1">
          <h2 className="flex items-center gap-2 font-medium">
            {config.label}
            {!config.available && <Badge variant="outline">Coming soon</Badge>}
          </h2>
          <p className="text-sm text-muted-foreground">{config.description}</p>
        </div>
        <Switch
          aria-label={`Enable ${config.label}`}
          checked={config.enabled}
          disabled={locked || config.agent === "orchestrator"}
          onCheckedChange={(enabled) => update.mutate({ enabled })}
        />
      </header>

      {config.agent !== "orchestrator" && (
        <div className="flex flex-wrap items-end gap-4">
          <div
            role="radiogroup"
            aria-label={`${config.label} autonomy`}
            className="inline-flex rounded-lg border p-1"
          >
            {AUTONOMY.map((option) => {
              const disallowed = option.value === "auto" && config.agent === "pricing_advisor";
              return (
                <button
                  key={option.value}
                  role="radio"
                  aria-checked={config.autonomy === option.value}
                  title={disallowed ? "Pricing Advisor only suggests" : option.hint}
                  disabled={locked || disallowed}
                  onClick={() => update.mutate({ autonomy: option.value })}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50",
                    config.autonomy === option.value
                      ? "bg-primary/15 font-medium text-primary"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
          <label className="grid gap-1 text-xs text-muted-foreground">
            Daily action cap
            <Input
              type="number"
              min={0}
              className="w-28"
              defaultValue={config.daily_action_cap}
              disabled={locked}
              onBlur={(e) => {
                const value = Number(e.target.value);
                if (value !== config.daily_action_cap) update.mutate({ daily_action_cap: value });
              }}
            />
          </label>
          {config.available && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => runTest.mutate()}
              disabled={!canEdit || runTest.isPending}
            >
              {runTest.isPending ? (
                <Loader2 className="animate-spin" aria-hidden />
              ) : (
                <FlaskConical aria-hidden />
              )}
              Test with a sample event
            </Button>
          )}
        </div>
      )}

      <SettingsFields config={config} disabled={locked} onSave={(settings) => update.mutate({ settings })} />
      {test && <TestResult result={test} />}
    </article>
  );
}

export default function AgentsPage() {
  const shop = useShop();
  const agents = useQuery({
    queryKey: ["shop", shop.id, "agents"],
    queryFn: () => api<AgentConfig[]>(`/api/shops/${shop.id}/agents`),
  });
  return (
    <>
      <PageHeader
        title="Agents"
        description="Turn agents on or off, choose how much they may do on their own, and set their limits."
      />
      {agents.isPending ? (
        <div className="grid max-w-4xl gap-4">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-36 rounded-xl" />
          ))}
        </div>
      ) : agents.isError ? (
        <ErrorState message={agents.error.message} onRetry={() => agents.refetch()} />
      ) : (
        <div className="grid max-w-4xl gap-4">
          {agents.data.map((config) => (
            <AgentCard key={config.agent} config={config} />
          ))}
        </div>
      )}
    </>
  );
}
