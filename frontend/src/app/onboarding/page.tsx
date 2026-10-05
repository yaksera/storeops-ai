"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Loader2, Mountain } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { toast } from "sonner";

import { Logo } from "@/components/brand";
import { ConnectShopifyCard } from "@/components/connect-shopify";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { meQueryKey } from "@/hooks/use-me";
import { api, ApiError, type Me, type ShopSummary } from "@/lib/api";

function InstallError() {
  const error = useSearchParams().get("error");
  if (!error) return null;
  return (
    <p
      role="alert"
      className="mt-6 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
    >
      {error}
    </p>
  );
}

export default function OnboardingPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const createDemo = useMutation({
    mutationFn: () => api<ShopSummary>("/api/shops/demo", { json: {} }),
    onSuccess: (shop) => {
      queryClient.setQueryData<Me>(meQueryKey, (me) => (me ? { ...me, shops: [...me.shops, shop] } : me));
      router.push(`/app/${shop.id}`);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) router.replace("/login?next=/onboarding");
      else toast.error(error.message);
    },
  });

  return (
    <div className="min-h-dvh bg-grid">
      <header className="px-6 py-5">
        <Logo />
      </header>
      <main className="mx-auto max-w-3xl px-4 pt-8 pb-16 sm:pt-16">
        <h1 className="text-3xl font-semibold tracking-tight">Connect a store</h1>
        <p className="mt-2 text-muted-foreground">
          Start with the demo store to see the agents work on live simulated traffic, or connect your own
          Shopify store.
        </p>
        <Suspense>
          <InstallError />
        </Suspense>
        <div className="mt-8 grid gap-4 md:grid-cols-2">
          <Card className="border-primary/30 bg-card/80">
            <CardHeader>
              <div className="mb-2 grid size-10 place-items-center rounded-lg border bg-primary/10 text-primary">
                <Mountain className="size-5" aria-hidden />
              </div>
              <CardTitle className="text-base">Northbound Outdoor Gear</CardTitle>
              <CardDescription>
                A simulated outdoor-gear brand with realistic orders, stock movement, carts, reviews and
                support. No API keys needed and nothing is ever sent externally.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button className="w-full" onClick={() => createDemo.mutate()} disabled={createDemo.isPending}>
                {createDemo.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null}
                Launch demo store
                {!createDemo.isPending && <ArrowRight aria-hidden />}
              </Button>
            </CardContent>
          </Card>
          <ConnectShopifyCard />
        </div>
      </main>
    </div>
  );
}
