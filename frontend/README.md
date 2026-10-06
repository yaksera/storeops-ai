# StoreOps AI — web

Next.js (App Router) dashboard. Dark "mission control" design system built on Tailwind v4 tokens
(`src/app/globals.css`) and shadcn/ui components (`src/components/ui`).

```bash
npm install
npm run dev          # http://localhost:3000, proxies /api to API_INTERNAL_URL (default :8000)
npm run lint
npm run typecheck
npm run format:check
```

The browser only talks to the Next.js origin: `/api/*` is rewritten to the FastAPI backend, so the
session cookie stays first-party and httpOnly. `src/proxy.ts` redirects signed-out visitors to
`/login`; authorisation is always enforced by the API.
