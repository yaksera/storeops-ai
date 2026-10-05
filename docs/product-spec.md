# StoreOps AI — Product & Technical Specification

**Status:** Draft v1 · **Owner:** @yaksera

## 1. Overview

StoreOps AI is a Shopify app in which a team of AI agents monitors a store in real time (orders,
stock, carts, reviews, support) and acts within seconds. The merchant follows everything on a live
dashboard and approves any action classified as risky.

Goals: production quality (secure, multi-tenant, tested, observable, deployable) and a polished,
startup-grade UX.

## 2. Technology stack

| Area | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async) + Alembic, PostgreSQL 16 |
| Queue / realtime | Redis 7 — job queue (Arq), pub/sub for live events, rate-limit buckets |
| Frontend | Next.js (App Router, TypeScript, React 19), Tailwind v4, shadcn/ui, Motion, Recharts |
| Browser live updates | Server-Sent Events, resumable via `Last-Event-ID`; polling fallback |
| LLM | OpenRouter (default `google/gemini-2.5-flash`, configurable per agent); JSON-mode outputs validated with Pydantic; single client with retries, timeouts, token + cost logging |
| Messaging | Resend or SendGrid (email); optional Twilio / WhatsApp alerts |
| Infra | Docker Compose locally; API + workers on Railway/Render/Fly; frontend on Vercel |
| Observability | Structured JSON logs, Sentry, OpenTelemetry traces, `/health` and `/ready` |

## 3. Shopify integration

- Public/custom app with OAuth install flow. Minimum scopes: `read_orders`, `write_orders`,
  `read_products`, `write_products`, `read_inventory`, `write_inventory`, `read_customers`,
  `read_fulfillments`, `read_checkouts`, `write_discounts`.
- Access tokens encrypted at rest (Fernet / KMS). Tokens are never logged.
- GraphQL Admin API, version pinned in config. REST only where unavoidable.
- Cost-based rate limiting: per-shop leaky bucket in Redis, driven by
  `extensions.cost.throttleStatus`; back off and retry with jitter.
- Webhooks: `orders/create`, `orders/updated`, `orders/cancelled`, `refunds/create`,
  `products/update`, `inventory_levels/update`, `checkouts/create`, `checkouts/update`,
  `fulfillments/create`, `app/uninstalled`.
  - Verify `X-Shopify-Hmac-Sha256` on every request (constant-time compare); reject if invalid.
  - Acknowledge within 1 s: persist raw event, enqueue job, return 200. No work in the request.
  - Idempotency via unique `X-Shopify-Webhook-Id`; tolerate duplicate and out-of-order delivery.
  - Nightly reconciliation re-syncs orders and inventory to cover missed webhooks.
- Mandatory privacy webhooks fully implemented: `customers/data_request`, `customers/redact`,
  `shop/redact`.
- `app/uninstalled`: revoke token, stop jobs, schedule data deletion after 48 h.
- Optional: App Bridge embedding; Shopify Billing API for plans.

## 4. Agents

Each agent defines: name, trigger events, `decide()` (deterministic logic + LLM, returns a typed
action proposal), `execute()` (Shopify / email side effects), risk level, and per-shop settings
(enabled flag + autonomy: `OFF` / `SUGGEST` (approval required) / `AUTO` (acts within limits)).

1. **Orchestrator** — routes events to agents, enforces per-shop limits, writes the audit log.
2. **Fraud Guard** — scores each new order 0–100 (address mismatch, high-risk country, failed
   payments, unusual quantity, new customer + high value, Shopify risk assessment). At or above the
   threshold: hold the order and alert. Provides a plain-language explanation of the score.
3. **Inventory Planner** — on each stock change, forecasts days-until-stockout from 30/90-day sales
   (moving average + weekday seasonality). Below reorder point: drafts a supplier PO email
   (approval required) and alerts.
4. **Cart Recovery** — checkout abandoned for 60 min → personalised email referencing the actual
   products, optional unique discount (max 10 %, 48 h expiry). Stops as soon as the order completes.
   Max 2 reminders per checkout.
5. **Support Agent** — answers order-status, returns and sizing questions (email inbox or chat
   widget) using live order, fulfillment and tracking data. Escalates to a human when confidence is
   low, the customer is angry, a refund exceeds $100, or legal language appears.
6. **Review & Reputation** — ingests reviews (Shopify reviews / Judge.me / Google): sentiment, draft
   reply, ticket for 1–2★ reviews, weekly top-complaints summary.
7. **Revenue Analyst** — live sales pulse; flags anomalies vs. same hour last week and gives a
   one-sentence likely cause (stockout, price change, traffic source, refund wave).
8. **Pricing Advisor** (`SUGGEST` only) — price suggestions from stock level, sell-through and
   margin floor. Never applies changes without approval.

Rules for all agents:

- Pipeline: typed proposal → guardrail check → approval (if required) → execute → audit log.
- Guardrails: per-agent daily action caps, max discount %, refund ceilings, no email to
  unsubscribed customers, quiet hours, per-shop kill switch, dry-run mode.
- Every decision records inputs, model, prompt version, output, cost, latency and approver.
- LLM failure falls back to deterministic rules; one agent's error never blocks the others.

## 5. Data model

PostgreSQL, multi-tenant: `shop_id` on every table plus row-level checks.

`shops`, `users`, `memberships` (owner/admin/viewer), `shop_settings`, `agent_configs`,
`webhook_events` (raw payload, unique `webhook_id`, status), `orders`, `order_items`, `products`,
`variants`, `inventory_snapshots`, `customers` (minimal PII), `checkouts`, `reviews`, `tickets`,
`messages`, `agent_runs`, `action_proposals` (proposed/approved/rejected/executed/failed/expired),
`approvals`, `audit_log` (append-only), `notifications`, `llm_calls` (tokens, `cost_usd`),
`usage_counters`, `subscriptions`.

Money stored as integer minor units + currency code. All timestamps UTC.

## 6. Real-time pipeline

Shopify webhook → HMAC verification → insert `webhook_events` (dedupe) → enqueue → worker
normalises into domain tables → Orchestrator dispatches to agents → proposals/actions → publish to
Redis channel `shop:{id}` → SSE endpoint streams to the dashboard.

Target: < 2 s end to end.

## 7. Frontend

Dark "mission control" visual style, mobile-first, accessible (WCAG AA).

- **Live Ops** — KPI ticker (revenue today, orders, AOV, conversion, recovered revenue), live order
  feed with fraud badges, agent team panel (status, current task, actions today), activity stream
  with "why" explanations.
- **Approvals** — one card per proposal with context, risk and preview (email / discount / PO);
  approve / edit / reject; keyboard shortcuts; bulk approve.
- **Inventory** — products table with stock, days-to-stockout bar, reorder suggestions.
- **Recovery** — abandoned carts, emails sent, recovered-revenue chart.
- **Support** — conversation threads, AI drafts, hand-over queue.
- **Insights** — anomaly timeline and weekly summary.
- **Agent settings** — enable/disable, autonomy, limits, tone, test with a sample event.
- **Audit log** — filterable, CSV export.
- **Onboarding** — install → connect → first-sync progress → agents live, in under 3 minutes.
- Designed empty, loading, error and offline states on every screen; toasts; skeletons; no layout
  shift.

## 8. Demo mode

Built-in store simulator for a fictional brand, **Northbound Outdoor Gear** (`.example` domains),
streaming realistic events: orders every few seconds (some fraudulent), falling stock, abandoned
carts, reviews, support emails. A Demo / Live switch; demo mode needs no API keys and never sends
anything externally.

"Trigger scenario" menu: Flash sale spike, Fraud attempt, Stockout, Angry review, Shipping delay.

## 9. Security & compliance

OWASP Top 10; session auth with httpOnly SameSite cookies (Shopify session tokens when embedded);
CSRF protection; RBAC; secrets from environment only; encrypted tokens; rate limiting on public
endpoints; input validation throughout; PII minimisation and retention policy; GDPR webhooks;
privacy policy page; CAN-SPAM/GDPR-compliant marketing email (unsubscribe link, physical address,
consent check).

## 10. Plans & usage

| Plan | Includes |
|---|---|
| Free | Demo mode + 1 agent |
| Growth | All agents, 2,000 AI actions / month |
| Pro | Unlimited stores, priority processing |

Billing through Shopify Billing API (Stripe as alternative). AI cost tracked per shop; hard cap and
alert when a shop exceeds its plan.

## 11. Quality requirements

- Tests: pytest unit tests for every agent's `decide()`, webhook HMAC + idempotency tests,
  integration tests against a mocked Shopify API, Playwright E2E for onboarding, approvals and the
  live dashboard. Backend coverage ≥ 80 %.
- CI (GitHub Actions): ruff, eslint, mypy, tsc, tests, Docker build.
- Seed script and fixtures; `make dev` starts the full stack; README with Mermaid architecture
  diagram, screenshots, Shopify development-store setup and `.env.example`.
- Performance: webhook ack < 1 s, dashboard update < 2 s, 50 events/s per worker.

## 12. Milestones

1. Repository skeleton, Docker Compose, schema + migrations, auth, design system
2. Demo simulator, event pipeline, SSE live dashboard
3. Fraud Guard, Inventory Planner, Cart Recovery with approvals, guardrails, audit log
4. Shopify OAuth, webhooks (HMAC, idempotency, reconciliation), rate-limited GraphQL client
5. Support Agent, Review & Reputation, Revenue Analyst, Pricing Advisor
6. Billing, usage caps, GDPR webhooks, privacy page
7. Test hardening, CI, monitoring, deployment, README, screenshots, demo script
