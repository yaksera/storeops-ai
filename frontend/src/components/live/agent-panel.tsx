"use client";

import {
  Boxes,
  MessageSquareText,
  Network,
  ShieldAlert,
  ShoppingCart,
  Star,
  Tag,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";

import { StatusDot } from "@/components/status-dot";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { effectiveStatus, type AgentName, type AgentState } from "@/lib/live";
import { cn, timeAgo } from "@/lib/utils";

const ICONS: Record<AgentName, LucideIcon> = {
  orchestrator: Network,
  fraud_guard: ShieldAlert,
  inventory_planner: Boxes,
  cart_recovery: ShoppingCart,
  support: MessageSquareText,
  review_reputation: Star,
  revenue_analyst: TrendingUp,
  pricing_advisor: Tag,
};

const AUTONOMY: Record<string, string> = { auto: "Auto", suggest: "Suggest", off: "Off" };

export function AgentPanel({ agents, now }: { agents: AgentState[]; now: number }) {
  const working = agents.filter((a) => effectiveStatus(a, now) === "working").length;
  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle>Agent team</CardTitle>
        <CardDescription>
          {working > 0 ? `${working} working right now` : "All agents watching for events"}
        </CardDescription>
      </CardHeader>
      <CardContent className="px-3">
        <ul className="grid gap-1">
          {agents.map((agent) => {
            const Icon = ICONS[agent.agent];
            const status = effectiveStatus(agent, now);
            return (
              <li
                key={agent.agent}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-2 py-2 transition-colors",
                  status === "working" && "bg-primary/5",
                  status === "off" && "opacity-55",
                )}
              >
                <span
                  className={cn(
                    "grid size-8 shrink-0 place-items-center rounded-md border bg-muted",
                    status === "working" ? "text-primary" : "text-muted-foreground",
                  )}
                >
                  <Icon className="size-4" aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-2 text-sm font-medium">
                    {agent.label}
                    <StatusDot
                      tone={status === "working" ? "busy" : status === "off" ? "idle" : "live"}
                      pulse={status === "working"}
                      label={status}
                    />
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {status === "off"
                      ? "Disabled"
                      : status === "working"
                        ? agent.task
                        : agent.last_active_at
                          ? `Watching · last active ${timeAgo(agent.last_active_at, now)}`
                          : "Watching"}
                  </p>
                </div>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="tabular shrink-0 rounded-md border px-1.5 py-0.5 text-xs text-muted-foreground">
                      {agent.actions_today} · {AUTONOMY[agent.autonomy] ?? agent.autonomy}
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>
                    {agent.actions_today} actions today · autonomy:{" "}
                    {AUTONOMY[agent.autonomy] ?? agent.autonomy}
                  </TooltipContent>
                </Tooltip>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
