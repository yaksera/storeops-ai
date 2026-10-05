"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { Download, ScrollText } from "lucide-react";
import { useState } from "react";

import { EmptyState, ErrorState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { useShop } from "@/components/shop-context";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import type { AuditEntry } from "@/lib/agents";
import { api } from "@/lib/api";

const ACTION_FILTERS = [
  { value: "", label: "All actions" },
  { value: "proposal.", label: "Proposals" },
  { value: "action.", label: "Executed actions" },
  { value: "guardrail.", label: "Guardrail blocks" },
  { value: "settings.", label: "Settings" },
  { value: "agent.", label: "Agent config" },
  { value: "member.", label: "Team" },
  { value: "demo.", label: "Demo controls" },
];

const ACTOR_LABEL: Record<AuditEntry["actor_type"], string> = {
  system: "System",
  agent: "Agent",
  user: "User",
};

function describe(entry: AuditEntry): string {
  const d = entry.details;
  if (typeof d.title === "string") return d.title;
  if (d.changes && typeof d.changes === "object") {
    return Object.entries(d.changes as Record<string, { from: string; to: string }>)
      .map(([k, v]) => `${k.replaceAll("_", " ")}: ${v.from} → ${v.to}`)
      .join(", ");
  }
  if (typeof d.role === "string") return `role ${d.role}`;
  if (typeof d.scenario === "string") return d.scenario.replaceAll("_", " ");
  return "";
}

const selectClass = "h-9 rounded-md border border-input bg-background px-2 text-sm";

export default function AuditPage() {
  const shop = useShop();
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [q, setQ] = useState("");
  const params = new URLSearchParams();
  if (action) params.set("action", action);
  if (actor) params.set("actor_type", actor);
  if (q.trim()) params.set("q", q.trim());

  const log = useInfiniteQuery({
    queryKey: ["shop", shop.id, "audit", params.toString()],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) => {
      const p = new URLSearchParams(params);
      p.set("limit", "50");
      if (pageParam) p.set("before_id", String(pageParam));
      return api<{ items: AuditEntry[]; next_before_id: number | null }>(`/api/shops/${shop.id}/audit?${p}`);
    },
    getNextPageParam: (last) => last.next_before_id,
  });
  const rows = log.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Every agent decision, approval, guardrail block and settings change. Append-only."
        actions={
          <Button variant="outline" size="sm" asChild>
            <a href={`/api/shops/${shop.id}/audit/export.csv?${params}`} download>
              <Download aria-hidden /> Export CSV
            </a>
          </Button>
        }
      />
      <div className="mb-4 flex flex-wrap gap-2">
        <select
          aria-label="Filter by action"
          className={selectClass}
          value={action}
          onChange={(e) => setAction(e.target.value)}
        >
          {ACTION_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by actor"
          className={selectClass}
          value={actor}
          onChange={(e) => setActor(e.target.value)}
        >
          <option value="">Any actor</option>
          <option value="agent">Agents</option>
          <option value="user">People</option>
          <option value="system">System</option>
        </select>
        <Input
          aria-label="Search titles"
          placeholder="Search…"
          className="max-w-64"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>

      {log.isPending ? (
        <Skeleton className="h-96 rounded-xl" />
      ) : log.isError ? (
        <ErrorState message={log.error.message} onRetry={() => log.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState icon={ScrollText} title="Nothing matches these filters" />
      ) : (
        <div className="overflow-x-auto rounded-xl border bg-card">
          <table className="w-full min-w-[44rem] text-sm">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2.5 font-medium">Time</th>
                <th className="px-4 py-2.5 font-medium">Actor</th>
                <th className="px-4 py-2.5 font-medium">Action</th>
                <th className="px-4 py-2.5 font-medium">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((entry) => (
                <tr key={entry.id} className="align-top">
                  <td className="px-4 py-2.5 whitespace-nowrap text-muted-foreground">
                    <time dateTime={entry.occurred_at}>
                      {new Date(entry.occurred_at).toLocaleString(undefined, {
                        dateStyle: "short",
                        timeStyle: "medium",
                      })}
                    </time>
                  </td>
                  <td className="px-4 py-2.5 whitespace-nowrap">
                    <Badge
                      variant={
                        entry.actor_type === "user"
                          ? "info"
                          : entry.actor_type === "agent"
                            ? "default"
                            : "secondary"
                      }
                    >
                      {ACTOR_LABEL[entry.actor_type]}
                    </Badge>{" "}
                    {entry.actor_type === "agent" && (
                      <span className="text-muted-foreground">{entry.actor_id?.replaceAll("_", " ")}</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs whitespace-nowrap">{entry.action}</td>
                  <td className="px-4 py-2.5">{describe(entry)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {log.hasNextPage && (
            <div className="border-t p-3 text-center">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => log.fetchNextPage()}
                disabled={log.isFetchingNextPage}
              >
                Load older entries
              </Button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
