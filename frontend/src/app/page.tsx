import {
  ArrowRight,
  Boxes,
  MessageSquareText,
  ShieldAlert,
  ShoppingCart,
  Star,
  TrendingUp,
} from "lucide-react";
import Link from "next/link";

import { Logo } from "@/components/brand";
import { StatusDot } from "@/components/status-dot";
import { Button } from "@/components/ui/button";

const agents = [
  {
    icon: ShieldAlert,
    name: "Fraud Guard",
    text: "Scores every order and holds risky ones before they ship.",
  },
  {
    icon: Boxes,
    name: "Inventory Planner",
    text: "Forecasts stockouts and drafts supplier POs for approval.",
  },
  {
    icon: ShoppingCart,
    name: "Cart Recovery",
    text: "Personal follow-ups on abandoned checkouts, within your limits.",
  },
  {
    icon: MessageSquareText,
    name: "Support Agent",
    text: "Answers order-status questions with live tracking data.",
  },
  { icon: Star, name: "Reputation", text: "Triages reviews, drafts replies and surfaces top complaints." },
  {
    icon: TrendingUp,
    name: "Revenue Analyst",
    text: "Spots anomalies against last week and explains the likely cause.",
  },
];

export default function Home() {
  return (
    <div className="relative min-h-dvh overflow-hidden bg-grid">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[32rem] bg-[radial-gradient(ellipse_at_top,oklch(0.8_0.13_195/0.16),transparent_65%)]"
      />
      <header className="relative mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
        <Logo />
        <nav className="flex items-center gap-2">
          <Button variant="ghost" asChild>
            <Link href="/login">Sign in</Link>
          </Button>
          <Button asChild>
            <Link href="/signup">Get started</Link>
          </Button>
        </nav>
      </header>

      <main className="relative mx-auto max-w-6xl px-6 pt-16 pb-24 sm:pt-24">
        <div className="mx-auto max-w-3xl text-center">
          <p className="mx-auto mb-6 inline-flex items-center gap-2 rounded-full border bg-card/60 px-3 py-1 text-xs text-muted-foreground backdrop-blur">
            <StatusDot pulse /> Agents on watch 24/7
          </p>
          <h1 className="text-4xl font-semibold tracking-tight text-balance sm:text-6xl">
            Your Shopify store&apos;s <span className="text-glow text-primary">operations team</span>, running
            in real time.
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg text-pretty text-muted-foreground">
            StoreOps AI watches orders, stock, carts, reviews and support as they happen, acts within seconds,
            and routes anything risky to you for approval.
          </p>
          <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
            <Button size="lg" asChild>
              <Link href="/signup">
                Try the live demo <ArrowRight aria-hidden />
              </Link>
            </Button>
            <Button size="lg" variant="outline" asChild>
              <Link href="/login">Sign in</Link>
            </Button>
          </div>
        </div>

        <ul className="mt-24 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {agents.map(({ icon: Icon, name, text }) => (
            <li
              key={name}
              className="rounded-xl border bg-card/70 p-5 backdrop-blur transition-colors hover:border-primary/30"
            >
              <div className="flex items-center gap-3">
                <span className="grid size-9 place-items-center rounded-lg border bg-muted text-primary">
                  <Icon className="size-4" aria-hidden />
                </span>
                <h2 className="font-medium">{name}</h2>
              </div>
              <p className="mt-3 text-sm text-muted-foreground">{text}</p>
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}
