import type { AgentName } from "@/lib/live";

export type RiskLevel = "low" | "medium" | "high";
export type ProposalStatus = "proposed" | "approved" | "rejected" | "executed" | "failed" | "expired";
export type Autonomy = "off" | "suggest" | "auto";

export interface EmailPreview {
  to: string;
  subject: string;
  body: string;
}

export interface RiskFactor {
  points: number;
  reason: string;
  code: string;
}

export interface Proposal {
  id: string;
  agent: AgentName;
  agent_label: string;
  action_type: "hold_order" | "send_recovery_email" | "draft_po" | string;
  title: string;
  summary: string;
  rationale: string;
  risk_level: RiskLevel;
  requires_approval: boolean;
  status: ProposalStatus;
  target_type: string | null;
  target_id: string | null;
  payload: Record<string, unknown>;
  preview: {
    email?: EmailPreview;
    factors?: RiskFactor[];
    order?: { name: string; total_minor: number; currency: string; customer_name: string | null };
    forecast?: Record<string, unknown>;
    price?: { from_minor: number; to_minor: number; currency: string };
    review?: { rating: number; title: string | null; body: string };
  };
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string | null;
  expires_at: string | null;
  executed_at: string | null;
}

export interface ProposalList {
  items: Proposal[];
  pending: number;
}

export interface AgentConfig {
  agent: AgentName;
  label: string;
  description: string;
  available: boolean;
  enabled: boolean;
  autonomy: Autonomy;
  daily_action_cap: number;
  model: string | null;
  tone: string;
  settings: Record<string, unknown>;
}

export interface AgentTestResult {
  event: { kind: string; data: Record<string, unknown> };
  output: Record<string, unknown> | null;
  proposals: Pick<Proposal, "action_type" | "title" | "summary" | "rationale" | "risk_level" | "preview">[];
}

export interface AuditEntry {
  id: number;
  occurred_at: string;
  actor_type: "system" | "agent" | "user";
  actor_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, unknown>;
}

export interface InventoryRow {
  variant_id: string;
  product: string;
  variant: string;
  sku: string | null;
  price_minor: number;
  on_hand: number;
  reorder_point: number;
  daily_rate: number;
  days_to_stockout: number | null;
  lead_time_days: number;
  suggested_order_qty: number;
  needs_reorder: boolean;
  po_pending: boolean;
}

export interface RecoveryData {
  currency: string;
  stats: {
    abandoned_14d: number;
    emails_sent_14d: number;
    recovered_orders_14d: number;
    recovered_revenue_14d_minor: number;
    carts_emailed_14d: number;
    recovery_rate: number;
  };
  series: { date: string; recovered_minor: number }[];
  carts: {
    id: string;
    customer_name: string | null;
    email: string | null;
    total_minor: number;
    items: string[];
    status: string;
    reminders_sent: number;
    last_reminder_at: string | null;
    created_at: string | null;
    consent: boolean;
  }[];
}

export const pendingKey = (shopId: string) => ["shop", shopId, "proposals", "pending-count"] as const;
