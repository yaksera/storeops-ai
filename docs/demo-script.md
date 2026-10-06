# Five-minute demo

A walkthrough for showing StoreOps AI with the built-in Northbound Outdoor Gear store. No API keys
are needed and nothing is sent externally.

**Setup:** `make dev`, then `make seed` (prints the demo login), or sign up at
<http://localhost:3000> and choose **Launch demo store**. Keep the Live Ops page open for a
minute before presenting so the simulator is warm.

## 1. Live Ops (1 min)

- The KPI ticker compares today with the same time last week. Five weeks of history are seeded.
- Orders stream in every few seconds. Each one appears in the feed, then Fraud Guard's verdict
  lands on it (risk badge).
- The agent team panel shows who is working on what right now. The activity stream explains
  *why* each event matters.
- Point out the **Live** connection badge: Server-Sent Events, resumable after a disconnect.

## 2. Fraud (1 min)

- **Trigger scenario → Fraud attempt.** Three suspicious orders arrive: billed abroad, unusual
  quantity, new customer, disposable email.
- Open **Approvals**. Each hold shows the score, the factors with their points, and the reasoning.
- Press **A** to approve the first (keyboard shortcuts: J/K to move, E to edit, R to reject).
  The order is held and the action shows up in the **Audit log**.

## 3. Stock and pricing (1 min)

- **Trigger scenario → Stockout.** A best seller sells through.
- **Inventory** shows days to stockout per SKU. The Inventory Planner has drafted a supplier PO
  in Approvals: edit the quantity, then approve.
- The Pricing Advisor may suggest a price rise for fast movers. It never changes a price without
  approval and never goes below the margin floor.

## 4. Customers (1 min)

- **Trigger scenario → Angry review.** A 1★ review and an angry email arrive.
- **Support → Hand-over** shows the email, escalated because of the chargeback language and refund
  size. Reply and resolve.
- **Trigger scenario → Shipping delay.** "Where is my order?" emails are answered automatically
  with live tracking (see the Resolved tab).
- **Insights** shows the review themes and any revenue anomaly with its likely cause (a
  **Flash sale spike** makes a good one).

## 5. Control (1 min)

- **Agents:** switch an agent between Off / Suggest / Auto, set daily caps, and run **Test with a
  sample event** (a dry run on real data that saves nothing).
- **Settings:** kill switch, dry-run mode, discount and refund limits, quiet hours, team roles.
- **Recovery:** abandoned carts, follow-up emails (consent-aware, with unsubscribe links) and
  recovered revenue.
- **Plan & usage:** metering per plan. Demo stores switch plans instantly.
