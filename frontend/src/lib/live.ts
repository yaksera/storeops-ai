export type Severity = "info" | "warning" | "critical";
export type AgentName =
  | "orchestrator"
  | "fraud_guard"
  | "inventory_planner"
  | "cart_recovery"
  | "support"
  | "review_reputation"
  | "revenue_analyst"
  | "pricing_advisor";

export interface Kpis {
  currency: string;
  revenue_minor: number;
  orders: number;
  aov_minor: number;
  conversion_rate: number;
  checkouts: number;
  recovered_revenue_minor: number;
  revenue_last_week_minor: number;
  orders_last_week: number;
}

export interface OrderSummary {
  id: string;
  name: string;
  total_minor: number;
  refunded_minor: number;
  currency: string;
  customer_name: string | null;
  customer_orders: number | null;
  shipping_country: string | null;
  billing_country: string | null;
  item_count: number;
  financial_status: string | null;
  fulfillment_status: string | null;
  source: string | null;
  risk_score: number | null;
  is_held: boolean;
  cancelled: boolean;
  processed_at: string;
}

export interface AgentState {
  agent: AgentName;
  label: string;
  enabled: boolean;
  autonomy: string;
  status: string;
  task: string | null;
  last_active_at: string | null;
  actions_today: number;
}

export interface ActivityData {
  id: string;
  kind: string;
  agent: AgentName | null;
  title: string;
  detail: string;
  severity: Severity;
  ref: Record<string, unknown>;
}

export interface LiveEvent<T = unknown> {
  id: string;
  type: string;
  data: T;
  at: string;
}

export interface Scenario {
  id: string;
  label: string;
}

export interface Dashboard {
  kpis: Kpis;
  revenue_by_hour: { today: number[]; last_week: number[] };
  orders: OrderSummary[];
  agents: AgentState[];
  activity: LiveEvent<ActivityData>[];
  last_event_id: string;
  simulator: { running: boolean; scenarios: Scenario[] } | null;
  pending_approvals: number;
  sync: { state: "syncing" | "done"; products: number; orders: number } | null;
}

export interface LiveState {
  kpis: Kpis;
  revenueByHour: { today: number[]; last_week: number[] };
  orders: OrderSummary[];
  agents: AgentState[];
  activity: LiveEvent<ActivityData>[];
  simulatorRunning: boolean | null;
  lastEventId: string;
}

export const LIVE_EVENT_TYPES = [
  "activity",
  "agent.status",
  "kpis.updated",
  "order.created",
  "order.updated",
  "order.refunded",
  "order.fulfilled",
  "simulator.status",
  "proposal.created",
  "proposal.updated",
] as const;

const MAX_ORDERS = 40;
const MAX_ACTIVITY = 80;

export function fromDashboard(d: Dashboard): LiveState {
  return {
    kpis: d.kpis,
    revenueByHour: d.revenue_by_hour,
    orders: d.orders,
    agents: d.agents,
    activity: d.activity,
    simulatorRunning: d.simulator?.running ?? null,
    lastEventId: d.last_event_id,
  };
}

function localHour(iso: string, timeZone: string): number {
  const hour = new Intl.DateTimeFormat("en-US", { hour: "numeric", hourCycle: "h23", timeZone }).format(
    new Date(iso),
  );
  return Number(hour) % 24;
}

/** Pure reducer: apply one streamed event to the dashboard state. */
export function applyEvent(state: LiveState, event: LiveEvent, timeZone = "UTC"): LiveState {
  const next: LiveState = { ...state, lastEventId: event.id };
  switch (event.type) {
    case "kpis.updated":
      next.kpis = event.data as Kpis;
      break;
    case "order.created": {
      const order = event.data as OrderSummary;
      if (state.orders.some((o) => o.id === order.id)) break;
      next.orders = [order, ...state.orders].slice(0, MAX_ORDERS);
      const hour = localHour(order.processed_at, timeZone);
      const today = [...state.revenueByHour.today];
      today[hour] = (today[hour] ?? 0) + order.total_minor;
      next.revenueByHour = { ...state.revenueByHour, today };
      break;
    }
    case "order.updated":
    case "order.refunded":
    case "order.fulfilled": {
      const update = event.data as Partial<OrderSummary> & { id: string };
      next.orders = state.orders.map((o) =>
        o.id === update.id ? { ...o, ...update, item_count: update.item_count || o.item_count } : o,
      );
      break;
    }
    case "agent.status": {
      const update = event.data as { agent: AgentName; status: string; task: string | null; at: string };
      next.agents = state.agents.map((a) =>
        a.agent === update.agent
          ? { ...a, status: update.status, task: update.task, last_active_at: update.at }
          : a,
      );
      break;
    }
    case "activity": {
      const item = event as LiveEvent<ActivityData>;
      if (state.activity.some((a) => a.id === item.id)) break;
      next.activity = [item, ...state.activity].slice(0, MAX_ACTIVITY);
      break;
    }
    case "simulator.status":
      next.simulatorRunning = (event.data as { running: boolean }).running;
      break;
  }
  return next;
}

/** An agent shows as "working" briefly after picking up a task, then returns to watching. */
export function effectiveStatus(agent: AgentState, now: number): "off" | "working" | "watching" {
  if (!agent.enabled || agent.status === "off") return "off";
  if (agent.status === "working" && agent.last_active_at) {
    return now - new Date(agent.last_active_at).getTime() < 6000 ? "working" : "watching";
  }
  return "watching";
}
