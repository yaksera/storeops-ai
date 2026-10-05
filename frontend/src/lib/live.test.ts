import { describe, expect, it } from "vitest";

import { applyEvent, effectiveStatus, type AgentState, type LiveState, type OrderSummary } from "./live";

const order = (id: string, overrides: Partial<OrderSummary> = {}): OrderSummary => ({
  id,
  name: `#${id}`,
  total_minor: 10000,
  refunded_minor: 0,
  currency: "USD",
  customer_name: "Kai",
  customer_orders: 1,
  shipping_country: "US",
  billing_country: "US",
  item_count: 1,
  financial_status: "paid",
  fulfillment_status: null,
  source: "web",
  risk_score: null,
  is_held: false,
  cancelled: false,
  processed_at: "2026-10-05T15:30:00Z",
  ...overrides,
});

const agent: AgentState = {
  agent: "fraud_guard",
  label: "Fraud Guard",
  enabled: true,
  autonomy: "suggest",
  status: "watching",
  task: null,
  last_active_at: null,
  actions_today: 0,
};

function initial(): LiveState {
  return {
    kpis: {
      currency: "USD",
      revenue_minor: 0,
      orders: 0,
      aov_minor: 0,
      conversion_rate: 0,
      checkouts: 0,
      recovered_revenue_minor: 0,
      revenue_last_week_minor: 0,
      orders_last_week: 0,
    },
    revenueByHour: { today: Array(24).fill(0), last_week: Array(24).fill(0) },
    orders: [],
    agents: [agent],
    activity: [],
    simulatorRunning: true,
    lastEventId: "0-0",
  };
}

describe("applyEvent", () => {
  it("prepends new orders once and adds revenue to the local hour", () => {
    let state = applyEvent(
      initial(),
      { id: "1-0", type: "order.created", data: order("a"), at: "" },
      "America/Denver",
    );
    state = applyEvent(
      state,
      { id: "2-0", type: "order.created", data: order("a"), at: "" },
      "America/Denver",
    );
    expect(state.orders.map((o) => o.id)).toEqual(["a"]);
    // 15:30 UTC is 09:30 in Denver (MDT).
    expect(state.revenueByHour.today[9]).toBe(10000);
    expect(state.lastEventId).toBe("2-0");
  });

  it("merges order updates", () => {
    let state = applyEvent(initial(), { id: "1-0", type: "order.created", data: order("a"), at: "" });
    state = applyEvent(state, {
      id: "2-0",
      type: "order.fulfilled",
      data: { id: "a", fulfillment_status: "fulfilled", item_count: 0 },
      at: "",
    });
    expect(state.orders[0].fulfillment_status).toBe("fulfilled");
    expect(state.orders[0].item_count).toBe(1);
  });

  it("updates agents, activity, KPIs and simulator status", () => {
    let state = applyEvent(initial(), {
      id: "1-0",
      type: "agent.status",
      data: { agent: "fraud_guard", status: "working", task: "Scoring #1", at: "2026-10-05T15:30:00Z" },
      at: "",
    });
    expect(state.agents[0]).toMatchObject({ status: "working", task: "Scoring #1" });
    state = applyEvent(state, {
      id: "2-0",
      type: "activity",
      data: {
        id: "x",
        kind: "order.created",
        agent: null,
        title: "New order",
        detail: "",
        severity: "info",
        ref: {},
      },
      at: "",
    });
    expect(state.activity).toHaveLength(1);
    state = applyEvent(state, {
      id: "3-0",
      type: "kpis.updated",
      data: { ...state.kpis, orders: 7 },
      at: "",
    });
    expect(state.kpis.orders).toBe(7);
    state = applyEvent(state, { id: "4-0", type: "simulator.status", data: { running: false }, at: "" });
    expect(state.simulatorRunning).toBe(false);
  });
});

describe("effectiveStatus", () => {
  const at = Date.parse("2026-10-05T15:30:00Z");
  it("decays working to watching after a few seconds", () => {
    const working = { ...agent, status: "working", last_active_at: "2026-10-05T15:30:00Z" };
    expect(effectiveStatus(working, at + 2000)).toBe("working");
    expect(effectiveStatus(working, at + 10000)).toBe("watching");
    expect(effectiveStatus({ ...agent, enabled: false }, at)).toBe("off");
  });
});
