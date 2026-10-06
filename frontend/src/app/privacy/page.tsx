import type { Metadata } from "next";
import Link from "next/link";

import { Logo } from "@/components/brand";

export const metadata: Metadata = { title: "Privacy policy" };

const sections: { title: string; body: React.ReactNode }[] = [
  {
    title: "Who we are",
    body: (
      <p>
        StoreOps AI provides an operations assistant for Shopify merchants. For data about a store&apos;s
        customers, the merchant is the controller and StoreOps AI is a processor acting on their instructions.
      </p>
    ),
  },
  {
    title: "What we process",
    body: (
      <ul>
        <li>
          <strong>Merchant accounts:</strong> name, email address and a hashed password.
        </li>
        <li>
          <strong>Store data from Shopify:</strong> orders, products, inventory, checkouts and fulfilments.
        </li>
        <li>
          <strong>Customer data, kept to a minimum:</strong> email, first name, country, order count,
          marketing consent. We do not store payment details, full postal addresses or phone numbers.
        </li>
        <li>
          <strong>Support and review content</strong> the merchant routes to the app.
        </li>
      </ul>
    ),
  },
  {
    title: "Why",
    body: (
      <p>
        To detect fraud, forecast inventory, follow up on abandoned checkouts (only for customers who agreed
        to marketing), answer support questions, analyse reviews and sales, and keep an audit trail of every
        action taken on the merchant&apos;s behalf.
      </p>
    ),
  },
  {
    title: "Retention",
    body: (
      <ul>
        <li>Raw webhook payloads are deleted 30 days after processing.</li>
        <li>
          When the app is uninstalled, access is revoked immediately and all store data, including the audit
          log, is permanently deleted 48 hours later, or as soon as Shopify sends a shop deletion request.
        </li>
        <li>Customer redaction requests from Shopify are applied as soon as they arrive.</li>
      </ul>
    ),
  },
  {
    title: "Your rights",
    body: (
      <p>
        Customers can ask the merchant to access or delete their data. Shopify forwards those requests to us
        and we fulfil them automatically: data exports are made available to the merchant, and redactions
        remove personal data from orders, checkouts and support conversations. Every marketing email includes
        a one-click unsubscribe link and the merchant&apos;s postal address.
      </p>
    ),
  },
  {
    title: "Subprocessors",
    body: (
      <ul>
        <li>Shopify: source of store data and billing.</li>
        <li>OpenRouter: language model provider, used only for connected stores and only when enabled.</li>
        <li>Resend: transactional and recovery email delivery.</li>
        <li>Our hosting providers for the application, database and cache.</li>
      </ul>
    ),
  },
  {
    title: "Security",
    body: (
      <p>
        Shopify access tokens are encrypted at rest. Sessions use secure, httpOnly cookies. Every webhook is
        signature-verified. Access is role-based and every agent action is recorded in an append-only audit
        log.
      </p>
    ),
  },
];

export default function PrivacyPage() {
  return (
    <div className="min-h-dvh bg-grid">
      <header className="mx-auto flex max-w-3xl items-center justify-between px-6 py-5">
        <Link href="/">
          <Logo />
        </Link>
      </header>
      <main className="mx-auto max-w-3xl px-6 pb-24">
        <h1 className="text-3xl font-semibold tracking-tight">Privacy policy</h1>
        <p className="mt-2 text-sm text-muted-foreground">Last updated October 2026</p>
        <div className="mt-10 grid gap-8">
          {sections.map((s) => (
            <section
              key={s.title}
              className="grid gap-2 text-sm leading-relaxed text-muted-foreground [&_li]:ml-5 [&_li]:list-disc [&_strong]:text-foreground [&_ul]:grid [&_ul]:gap-1.5"
            >
              <h2 className="text-base font-semibold text-foreground">{s.title}</h2>
              {s.body}
            </section>
          ))}
        </div>
      </main>
    </div>
  );
}
