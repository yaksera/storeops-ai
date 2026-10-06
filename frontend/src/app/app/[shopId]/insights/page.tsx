"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowDownRight, ArrowUpRight, LineChart } from "lucide-react";

import { EmptyState, ErrorState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { useShop } from "@/components/shop-context";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useNow } from "@/hooks/use-live-ops";
import { api } from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";

interface InsightsData {
  anomalies: {
    id: string;
    title: string;
    body: string;
    severity: string;
    direction: "up" | "down" | null;
    change: number | null;
    at: string;
  }[];
  reviews: {
    reviews: number;
    average_rating: number | null;
    negative: number;
    top_complaints: { theme: string; count: number; example: string }[];
  };
  sentiment: Record<string, number>;
}

const SENTIMENT = [
  { key: "positive", label: "Positive", className: "bg-success" },
  { key: "neutral", label: "Neutral", className: "bg-muted-foreground/60" },
  { key: "negative", label: "Negative", className: "bg-destructive" },
];

export default function InsightsPage() {
  const shop = useShop();
  const now = useNow(60_000);
  const data = useQuery({
    queryKey: ["shop", shop.id, "insights"],
    queryFn: () => api<InsightsData>(`/api/shops/${shop.id}/insights`),
    refetchInterval: 30_000,
  });

  return (
    <>
      <PageHeader
        title="Insights"
        description="Revenue anomalies explained by the Revenue Analyst, and what reviewers are saying."
      />
      {data.isPending ? (
        <div className="grid gap-4 lg:grid-cols-5">
          <Skeleton className="h-96 rounded-xl lg:col-span-3" />
          <Skeleton className="h-96 rounded-xl lg:col-span-2" />
        </div>
      ) : data.isError ? (
        <ErrorState message={data.error.message} onRetry={() => data.refetch()} />
      ) : (
        <div className="grid gap-4 lg:grid-cols-5">
          <Card className="gap-2 lg:col-span-3">
            <CardHeader>
              <CardTitle>Anomaly timeline</CardTitle>
              <CardDescription>Sales compared with the same time last week.</CardDescription>
            </CardHeader>
            <CardContent>
              {data.data.anomalies.length === 0 ? (
                <EmptyState
                  icon={LineChart}
                  title="Nothing unusual yet"
                  description="Deviations of 40% or more from last week will appear here with a likely cause."
                />
              ) : (
                <ol className="relative grid gap-5 border-l pl-6">
                  {data.data.anomalies.map((a) => {
                    const up = a.direction === "up";
                    const Icon = up ? ArrowUpRight : ArrowDownRight;
                    return (
                      <li key={a.id} className="relative">
                        <span
                          className={cn(
                            "absolute top-0.5 -left-[2.1rem] grid size-6 place-items-center rounded-full border bg-card",
                            up ? "text-success" : "text-destructive",
                          )}
                        >
                          <Icon className="size-3.5" aria-label={up ? "Up" : "Down"} />
                        </span>
                        <p className="text-sm font-medium">{a.title}</p>
                        <p className="mt-0.5 text-sm text-muted-foreground">{a.body}</p>
                        <time dateTime={a.at} className="text-xs text-muted-foreground">
                          {timeAgo(a.at, now)}
                        </time>
                      </li>
                    );
                  })}
                </ol>
              )}
            </CardContent>
          </Card>

          <div className="grid content-start gap-4 lg:col-span-2">
            <Card className="gap-3">
              <CardHeader>
                <CardTitle>Reviews this week</CardTitle>
                <CardDescription>
                  {data.data.reviews.reviews} new
                  {data.data.reviews.average_rating !== null &&
                    ` · average ${data.data.reviews.average_rating}★`}
                  {` · ${data.data.reviews.negative} negative`}
                </CardDescription>
              </CardHeader>
              <CardContent className="grid gap-3">
                <SentimentBar sentiment={data.data.sentiment} />
                <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                  Top complaints
                </h3>
                {data.data.reviews.top_complaints.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No recurring complaints. Nice.</p>
                ) : (
                  <ol className="grid gap-2">
                    {data.data.reviews.top_complaints.map((c) => (
                      <li key={c.theme} className="rounded-lg border bg-background/40 px-3 py-2">
                        <p className="flex items-center justify-between text-sm font-medium">
                          {c.theme}
                          <span className="tabular text-muted-foreground">{c.count}</span>
                        </p>
                        <p className="truncate text-xs text-muted-foreground">“{c.example}”</p>
                      </li>
                    ))}
                  </ol>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}

function SentimentBar({ sentiment }: { sentiment: Record<string, number> }) {
  const total = SENTIMENT.reduce((sum, s) => sum + (sentiment[s.key] ?? 0), 0);
  if (total === 0) return <p className="text-sm text-muted-foreground">No reviews analysed yet.</p>;
  return (
    <div className="grid gap-2">
      <div
        className="flex h-2.5 gap-0.5 overflow-hidden rounded-full"
        role="img"
        aria-label="Review sentiment split"
      >
        {SENTIMENT.map((s) => {
          const share = (sentiment[s.key] ?? 0) / total;
          return share > 0 ? (
            <div
              key={s.key}
              className={cn("h-full first:rounded-l-full last:rounded-r-full", s.className)}
              style={{ width: `${share * 100}%` }}
            />
          ) : null;
        })}
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {SENTIMENT.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5">
            <span className={cn("size-2 rounded-full", s.className)} aria-hidden />
            {s.label} <span className="tabular text-foreground">{sentiment[s.key] ?? 0}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
