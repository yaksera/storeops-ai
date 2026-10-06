"use client";

import { useMutation } from "@tanstack/react-query";
import { ChevronDown, Pause, Play, Zap } from "lucide-react";
import { toast } from "sonner";

import { StatusDot, type StatusTone } from "@/components/status-dot";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { ConnectionStatus } from "@/hooks/use-live-ops";
import { api } from "@/lib/api";
import type { Scenario } from "@/lib/live";

const CONNECTION: Record<ConnectionStatus, { tone: StatusTone; label: string; pulse: boolean }> = {
  connecting: { tone: "idle", label: "Connecting…", pulse: false },
  live: { tone: "live", label: "Live", pulse: true },
  reconnecting: { tone: "warn", label: "Reconnecting…", pulse: false },
  polling: { tone: "warn", label: "Live (polling)", pulse: false },
  offline: { tone: "error", label: "Offline", pulse: false },
};

export function ConnectionBadge({ status }: { status: ConnectionStatus }) {
  const c = CONNECTION[status];
  return (
    <span
      role="status"
      className="inline-flex h-8 items-center gap-2 rounded-md border bg-card px-3 text-xs font-medium"
    >
      <StatusDot tone={c.tone} pulse={c.pulse} />
      {c.label}
    </span>
  );
}

export function SimulatorToggle({
  shopId,
  running,
  onChange,
  disabled,
}: {
  shopId: string;
  running: boolean;
  onChange: (running: boolean) => void;
  disabled?: boolean;
}) {
  const mutation = useMutation({
    mutationFn: (next: boolean) =>
      api<{ running: boolean }>(`/api/shops/${shopId}/demo/simulator`, { json: { running: next } }),
    onSuccess: (data) => onChange(data.running),
    onError: (error) => toast.error(error.message),
  });
  return (
    <Button
      variant="outline"
      size="sm"
      disabled={disabled || mutation.isPending}
      onClick={() => mutation.mutate(!running)}
      aria-pressed={running}
    >
      {running ? <Pause aria-hidden /> : <Play aria-hidden />}
      <span className="sr-only sm:not-sr-only">{running ? "Pause demo traffic" : "Resume demo traffic"}</span>
    </Button>
  );
}

export function ScenarioMenu({
  shopId,
  scenarios,
  disabled,
}: {
  shopId: string;
  scenarios: Scenario[];
  disabled?: boolean;
}) {
  const trigger = useMutation({
    mutationFn: (id: string) => api(`/api/shops/${shopId}/demo/scenarios/${id}`, { method: "POST" }),
    onSuccess: (_, id) => toast.success(`${scenarios.find((s) => s.id === id)?.label ?? id} started`),
    onError: (error) => toast.error(error.message),
  });
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" disabled={disabled}>
          <Zap aria-hidden /> Trigger scenario <ChevronDown aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>Simulate a situation</DropdownMenuLabel>
        {scenarios.map((s) => (
          <DropdownMenuItem key={s.id} onSelect={() => trigger.mutate(s.id)}>
            {s.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
