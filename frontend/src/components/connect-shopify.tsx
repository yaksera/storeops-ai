"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, Loader2, Store } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

export function ConnectShopifyCard() {
  const [shop, setShop] = useState("");
  const status = useQuery({
    queryKey: ["shopify", "status"],
    queryFn: () => api<{ configured: boolean; app_url: string }>("/api/shopify/status"),
    staleTime: Infinity,
  });
  const install = useMutation({
    mutationFn: () => api<{ url: string }>("/api/shopify/install", { json: { shop } }),
    onSuccess: ({ url }) => window.location.assign(url),
  });
  const configured = status.data?.configured ?? false;

  return (
    <Card className="bg-card/60">
      <CardHeader>
        <div className="mb-2 grid size-10 place-items-center rounded-lg border bg-muted text-muted-foreground">
          <Store className="size-5" aria-hidden />
        </div>
        <CardTitle className="flex items-center gap-2 text-base">
          Your Shopify store{" "}
          {!status.isPending && !configured && <Badge variant="outline">Setup needed</Badge>}
        </CardTitle>
        <CardDescription>
          Install the app on your store with Shopify OAuth. Agents start in suggest-only mode until you raise
          their autonomy.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            install.mutate();
          }}
        >
          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">Store domain</span>
            <div className="flex items-center rounded-md border border-input bg-background/40 focus-within:ring-[3px] focus-within:ring-ring/40">
              <Input
                required
                value={shop}
                onChange={(e) => setShop(e.target.value)}
                placeholder="your-store"
                aria-describedby="shop-suffix"
                className="border-0 bg-transparent shadow-none focus-visible:ring-0"
                disabled={!configured}
              />
              <span id="shop-suffix" className="pr-3 text-sm text-muted-foreground">
                .myshopify.com
              </span>
            </div>
          </label>
          {install.isError && (
            <p role="alert" className="text-sm text-destructive">
              {install.error.message}
            </p>
          )}
          {!status.isPending && !configured && (
            <p className="text-xs text-muted-foreground">
              Add <code>SHOPIFY_API_KEY</code>, <code>SHOPIFY_API_SECRET</code> and{" "}
              <code>PUBLIC_APP_URL</code> to <code>.env</code> to enable installs.
            </p>
          )}
          <Button type="submit" variant="outline" disabled={!configured || install.isPending || !shop.trim()}>
            {install.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null}
            Connect Shopify
            {!install.isPending && <ArrowRight aria-hidden />}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
