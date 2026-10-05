"use client";

import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, Loader2, MailX } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";

function Unsubscribe() {
  const token = useSearchParams().get("t") ?? "";
  const confirm = useMutation({
    mutationFn: () => api<{ status: string }>("/api/unsubscribe", { json: { token } }),
  });

  if (confirm.isSuccess) {
    return (
      <Card className="w-full max-w-md text-center">
        <CardHeader className="justify-items-center">
          <CheckCircle2 className="size-10 text-success" aria-hidden />
          <CardTitle className="text-xl">You&apos;re unsubscribed</CardTitle>
          <CardDescription>You won&apos;t receive marketing emails from this store again.</CardDescription>
        </CardHeader>
      </Card>
    );
  }
  return (
    <Card className="w-full max-w-md text-center">
      <CardHeader className="justify-items-center">
        <MailX className="size-10 text-muted-foreground" aria-hidden />
        <CardTitle className="text-xl">Unsubscribe from marketing emails</CardTitle>
        <CardDescription>
          Order confirmations and replies to your questions will still reach you.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {confirm.isError && (
          <p role="alert" className="text-sm text-destructive">
            {confirm.error.message}
          </p>
        )}
        <Button onClick={() => confirm.mutate()} disabled={!token || confirm.isPending}>
          {confirm.isPending && <Loader2 className="animate-spin" aria-hidden />}
          Unsubscribe
        </Button>
      </CardContent>
    </Card>
  );
}

export default function UnsubscribePage() {
  return (
    <main className="grid min-h-dvh place-items-center bg-grid px-4">
      <Suspense>
        <Unsubscribe />
      </Suspense>
    </main>
  );
}
