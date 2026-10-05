import Link from "next/link";

import { Logo } from "@/components/brand";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative flex min-h-dvh flex-col bg-grid">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-80 bg-[radial-gradient(ellipse_at_top,oklch(0.8_0.13_195/0.12),transparent_70%)]"
      />
      <header className="relative px-6 py-5">
        <Link href="/" className="inline-flex rounded-md">
          <Logo />
        </Link>
      </header>
      <main className="relative flex flex-1 items-center justify-center px-4 pb-16">{children}</main>
    </div>
  );
}
