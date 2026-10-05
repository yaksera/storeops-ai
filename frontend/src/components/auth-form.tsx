"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { meQueryKey } from "@/hooks/use-me";
import { api, ApiError, type Me } from "@/lib/api";

function safeNext(value: string | null): string | null {
  return value && value.startsWith("/") && !value.startsWith("//") ? value : null;
}

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const isSignup = mode === "signup";

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    try {
      const me = await api<Me>(`/api/auth/${mode}`, {
        json: {
          email: form.get("email"),
          password: form.get("password"),
          ...(isSignup ? { full_name: form.get("full_name") } : {}),
        },
      });
      queryClient.setQueryData(meQueryKey, me);
      const next = safeNext(params.get("next"));
      router.replace(next ?? (me.shops[0] ? `/app/${me.shops[0].id}` : "/onboarding"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
      setPending(false);
    }
  }

  return (
    <Card className="w-full max-w-sm animate-fade-in border-border/80 bg-card/80 backdrop-blur">
      <CardHeader>
        <CardTitle className="text-xl">{isSignup ? "Create your account" : "Welcome back"}</CardTitle>
        <CardDescription>
          {isSignup
            ? "Put an AI operations team on your store in minutes."
            : "Sign in to your mission control."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-4" noValidate={false}>
          {isSignup && (
            <div className="grid gap-2">
              <Label htmlFor="full_name">Name</Label>
              <Input id="full_name" name="full_name" autoComplete="name" placeholder="Alex Rivera" />
            </div>
          )}
          <div className="grid gap-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              required
              placeholder="you@store.example"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              name="password"
              type="password"
              autoComplete={isSignup ? "new-password" : "current-password"}
              required
              minLength={isSignup ? 10 : 1}
              aria-describedby={isSignup ? "password-hint" : undefined}
            />
            {isSignup && (
              <p id="password-hint" className="text-xs text-muted-foreground">
                At least 10 characters.
              </p>
            )}
          </div>
          {error && (
            <p
              role="alert"
              className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              {error}
            </p>
          )}
          <Button type="submit" disabled={pending} className="w-full">
            {pending && <Loader2 className="animate-spin" aria-hidden />}
            {isSignup ? "Create account" : "Sign in"}
          </Button>
        </form>
        <p className="mt-6 text-center text-sm text-muted-foreground">
          {isSignup ? "Already have an account? " : "New to StoreOps? "}
          <Link
            href={isSignup ? "/login" : "/signup"}
            className="font-medium text-foreground underline-offset-4 hover:underline"
          >
            {isSignup ? "Sign in" : "Create an account"}
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
