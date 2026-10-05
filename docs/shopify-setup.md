# Connecting a Shopify development store

Demo mode needs none of this. Follow these steps to run StoreOps AI against a real (development)
store on your machine.

## 1. Expose the app over HTTPS

Shopify only redirects to and sends webhooks to HTTPS URLs. Start the stack (`make dev`), then open a
tunnel to the web app on port 3000, for example:

```bash
cloudflared tunnel --url http://localhost:3000
# or: ngrok http 3000
```

Note the public URL, e.g. `https://storeops-dev.trycloudflare.com`. The web app proxies `/api/*` to
the API, so one URL covers the OAuth callback and webhooks.

## 2. Create the app in the Partners dashboard

1. In [Shopify Partners](https://partners.shopify.com), create a **development store** if you don't
   have one, then **Apps → Create app → Create app manually**.
2. **App URL:** `<PUBLIC_APP_URL>/onboarding`
3. **Allowed redirection URL:** `<PUBLIC_APP_URL>/api/shopify/callback`
4. **Compliance webhooks** (customer data request, customer redact, shop redact):
   `<PUBLIC_APP_URL>/api/webhooks/shopify`
5. Copy the **Client ID** and **Client secret**.

## 3. Configure `.env`

```bash
SHOPIFY_API_KEY=<client id>
SHOPIFY_API_SECRET=<client secret>
PUBLIC_APP_URL=https://storeops-dev.trycloudflare.com
# Optional, for real cart-recovery and PO emails:
RESEND_API_KEY=
EMAIL_FROM=Your Store <orders@yourdomain.com>
# Optional, for LLM-written explanations and email copy:
OPENROUTER_API_KEY=
```

Restart the API and worker (`make down && make dev`).

## 4. Install

Sign in to StoreOps, open **Onboarding → Your Shopify store**, enter the store's
`.myshopify.com` name and approve the requested scopes. After the redirect StoreOps:

- stores the access token encrypted (set `TOKEN_ENCRYPTION_KEY` in production)
- subscribes to `orders/*`, `refunds/create`, `products/update`, `inventory_levels/update`,
  `checkouts/*`, `fulfillments/create` and `app/uninstalled`
- imports products and the last 90 days of orders through the normal pipeline (progress shows on
  Live Ops)

Place a test order in the store and it appears on Live Ops within a couple of seconds.

## How it behaves

- **Webhooks:** every request's `X-Shopify-Hmac-Sha256` is verified (constant-time) before anything
  is stored. Events are deduplicated on `X-Shopify-Webhook-Id` and acknowledged immediately;
  processing happens on the worker.
- **Reconciliation:** a nightly job (09:15 UTC) re-reads orders updated in the last 26 hours and all
  products through the same pipeline, repairing anything a missed webhook would have left stale.
- **Rate limits:** the GraphQL client mirrors Shopify's cost-based leaky bucket in Redis per shop,
  waits before sending queries that wouldn't fit, and backs off with jitter on `THROTTLED`, 429 and
  5xx responses.
- **Actions:** Fraud Guard holds use `fulfillmentOrderHold` plus an order tag (`storeops-held`).
  Cart Recovery discount codes are single-use `discountCodeBasicCreate` codes. Emails go through
  Resend.
- **Uninstall:** `app/uninstalled` revokes the token, expires pending actions, stops all agent work
  and schedules the shop's data for deletion after 48 hours.
