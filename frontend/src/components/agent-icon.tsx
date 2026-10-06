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

import type { AgentName } from "@/lib/live";
import { cn } from "@/lib/utils";

export const AGENT_ICONS: Record<AgentName, LucideIcon> = {
  orchestrator: Network,
  fraud_guard: ShieldAlert,
  inventory_planner: Boxes,
  cart_recovery: ShoppingCart,
  support: MessageSquareText,
  review_reputation: Star,
  revenue_analyst: TrendingUp,
  pricing_advisor: Tag,
};

export function AgentIcon({ agent, className }: { agent: AgentName; className?: string }) {
  const Icon = AGENT_ICONS[agent] ?? Network;
  return (
    <span
      className={cn(
        "grid size-8 shrink-0 place-items-center rounded-md border bg-muted text-primary",
        className,
      )}
    >
      <Icon className="size-4" aria-hidden />
    </span>
  );
}
