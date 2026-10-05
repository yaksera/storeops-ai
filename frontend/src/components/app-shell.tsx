"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Boxes,
  CheckCheck,
  ChevronsUpDown,
  CreditCard,
  LineChart,
  LogOut,
  Menu,
  MessageSquareText,
  ScrollText,
  Settings,
  ShoppingCart,
  Sparkles,
  X,
  type LucideIcon,
} from "lucide-react";
import { Dialog } from "radix-ui";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Logo } from "@/components/brand";
import { FullPageSpinner } from "@/components/full-page-spinner";
import { ShopProvider } from "@/components/shop-context";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useMe } from "@/hooks/use-me";
import { pendingKey, type ProposalList } from "@/lib/agents";
import { api, ApiError, type Me, type ShopSummary } from "@/lib/api";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  ready: boolean;
}

function navItems(shopId: string): NavItem[] {
  const base = `/app/${shopId}`;
  return [
    { href: base, label: "Live Ops", icon: Activity, ready: true },
    { href: `${base}/approvals`, label: "Approvals", icon: CheckCheck, ready: true },
    { href: `${base}/inventory`, label: "Inventory", icon: Boxes, ready: true },
    { href: `${base}/recovery`, label: "Recovery", icon: ShoppingCart, ready: true },
    { href: `${base}/support`, label: "Support", icon: MessageSquareText, ready: true },
    { href: `${base}/insights`, label: "Insights", icon: LineChart, ready: true },
    { href: `${base}/agents`, label: "Agents", icon: Sparkles, ready: true },
    { href: `${base}/audit`, label: "Audit log", icon: ScrollText, ready: true },
    { href: `${base}/billing`, label: "Plan & usage", icon: CreditCard, ready: true },
    { href: `${base}/settings`, label: "Settings", icon: Settings, ready: true },
  ];
}

function usePendingApprovals(shopId: string): number {
  const { data } = useQuery({
    queryKey: pendingKey(shopId),
    queryFn: () => api<ProposalList>(`/api/shops/${shopId}/proposals?status=proposed&limit=1`),
    refetchInterval: 10_000,
  });
  return data?.pending ?? 0;
}

function NavList({ shopId, onNavigate }: { shopId: string; onNavigate?: () => void }) {
  const pathname = usePathname();
  const pending = usePendingApprovals(shopId);
  return (
    <ul className="grid gap-0.5">
      {navItems(shopId).map(({ href, label, icon: Icon, ready }) => {
        const active = pathname === href;
        const content = (
          <>
            <Icon className="size-4" aria-hidden />
            <span className="flex-1">{label}</span>
            {label === "Approvals" && pending > 0 && (
              <Badge
                variant="warning"
                className="tabular px-1.5 py-0 text-[11px]"
                aria-label={`${pending} pending`}
              >
                {pending}
              </Badge>
            )}
            {!ready && (
              <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
                Soon
              </Badge>
            )}
          </>
        );
        const classes = cn(
          "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
          active
            ? "bg-sidebar-accent font-medium text-foreground shadow-[inset_2px_0_0_var(--primary)]"
            : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
          !ready && "pointer-events-none opacity-60",
        );
        return (
          <li key={href}>
            {ready ? (
              <Link
                href={href}
                className={classes}
                aria-current={active ? "page" : undefined}
                onClick={onNavigate}
              >
                {content}
              </Link>
            ) : (
              <span className={classes} aria-disabled>
                {content}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function ShopSwitcher({ me, shop }: { me: Me; shop: ShopSummary }) {
  const router = useRouter();
  const queryClient = useQueryClient();

  async function logout() {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } finally {
      queryClient.clear();
      router.replace("/login");
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="flex w-full items-center gap-3 rounded-lg border bg-card/60 px-3 py-2 text-left text-sm hover:bg-accent">
          <span className="grid size-8 shrink-0 place-items-center rounded-md bg-primary/15 text-xs font-semibold text-primary">
            {shop.name.slice(0, 2).toUpperCase()}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-medium">{shop.name}</span>
            <span className="block truncate text-xs text-muted-foreground">
              {shop.mode === "demo" ? "Demo store" : shop.domain}
            </span>
          </span>
          <ChevronsUpDown className="size-4 text-muted-foreground" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64">
        <DropdownMenuLabel>{me.user.email}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {me.shops.map((s) => (
          <DropdownMenuItem key={s.id} onSelect={() => router.push(`/app/${s.id}`)}>
            <span className="flex-1 truncate">{s.name}</span>
            <Badge variant={s.mode === "demo" ? "info" : "success"}>{s.mode}</Badge>
          </DropdownMenuItem>
        ))}
        <DropdownMenuItem onSelect={() => router.push("/onboarding")}>Add a store…</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={logout}>
          <LogOut aria-hidden /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AppShell({ shopId, children }: { shopId: string; children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { data: me, error, isPending, isFetching, refetch } = useMe();
  const [mobileOpen, setMobileOpen] = useState(false);
  const shop = me?.shops.find((s) => s.id === shopId);

  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    } else if (me && !shop && !isFetching) {
      // The shop may have just been created; only give up once a fresh profile confirms it's gone.
      void refetch().then((result) => {
        if (result.data && !result.data.shops.some((s) => s.id === shopId)) router.replace("/app");
      });
    }
  }, [error, me, shop, shopId, isFetching, refetch, router, pathname]);

  if (isPending || !me || !shop) return <FullPageSpinner label="Connecting to mission control" />;

  return (
    <ShopProvider value={shop}>
      <div className="flex min-h-dvh">
        <aside className="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col gap-6 border-r border-sidebar-border bg-sidebar p-4 lg:flex">
          <Link href={`/app/${shop.id}`} className="px-2 pt-1">
            <Logo />
          </Link>
          <ShopSwitcher me={me} shop={shop} />
          <nav aria-label="Main" className="flex-1 overflow-y-auto">
            <NavList shopId={shop.id} />
          </nav>
          <p className="px-2 text-xs text-muted-foreground">
            Signed in as <span className="text-foreground">{me.user.full_name || me.user.email}</span>
          </p>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-30 flex items-center gap-3 border-b bg-background/80 px-4 py-3 backdrop-blur lg:hidden">
            <Dialog.Root open={mobileOpen} onOpenChange={setMobileOpen}>
              <Dialog.Trigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label="Open navigation">
                  <Menu />
                </Button>
              </Dialog.Trigger>
              <Dialog.Portal>
                <Dialog.Overlay className="fixed inset-0 z-40 animate-fade-in bg-black/60" />
                <Dialog.Content className="fixed inset-y-0 left-0 z-50 flex w-72 animate-fade-in flex-col gap-6 border-r bg-sidebar p-4">
                  <div className="flex items-center justify-between">
                    <Dialog.Title asChild>
                      <span>
                        <Logo />
                      </span>
                    </Dialog.Title>
                    <Dialog.Close asChild>
                      <Button variant="ghost" size="icon-sm" aria-label="Close navigation">
                        <X />
                      </Button>
                    </Dialog.Close>
                  </div>
                  <Dialog.Description className="sr-only">Main navigation</Dialog.Description>
                  <ShopSwitcher me={me} shop={shop} />
                  <nav aria-label="Main">
                    <NavList shopId={shop.id} onNavigate={() => setMobileOpen(false)} />
                  </nav>
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>
            <Logo className="text-sm" />
          </header>
          <main className="flex-1 px-4 py-6 sm:px-6 lg:px-8">{children}</main>
        </div>
      </div>
    </ShopProvider>
  );
}
