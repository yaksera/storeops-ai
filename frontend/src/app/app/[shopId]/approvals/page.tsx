"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCheck, Inbox, Keyboard } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { ProposalCard, type Edits } from "@/components/approvals/proposal-card";
import { EmptyState, ErrorState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { hasRole, useShop } from "@/components/shop-context";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useNow } from "@/hooks/use-live-ops";
import { useShopEvents } from "@/hooks/use-shop-events";
import { pendingKey, type Proposal, type ProposalList } from "@/lib/agents";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type Tab = "pending" | "history";

export default function ApprovalsPage() {
  const shop = useShop();
  const now = useNow(15_000);
  const queryClient = useQueryClient();
  const canDecide = hasRole(shop.role, "admin");
  const [tab, setTab] = useState<Tab>("pending");
  const [focus, setFocus] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<string | null>(null);
  const [edits, setEdits] = useState<Edits>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const cardRefs = useRef<(HTMLElement | null)[]>([]);

  const listKey = ["shop", shop.id, "proposals", tab];
  const list = useQuery({
    queryKey: listKey,
    queryFn: () =>
      api<ProposalList>(
        `/api/shops/${shop.id}/proposals?${tab === "pending" ? "status=proposed&" : ""}limit=100`,
      ),
  });
  const items = (list.data?.items ?? []).filter((p) => (tab === "pending" ? true : p.status !== "proposed"));

  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["shop", shop.id, "proposals"] });
    void queryClient.invalidateQueries({ queryKey: pendingKey(shop.id) });
  }, [queryClient, shop.id]);
  useShopEvents(shop.id, ["proposal.created", "proposal.updated"], refresh);

  const decide = useMutation({
    mutationFn: async (args: { proposal: Proposal; approve: boolean; edits?: Edits }) => {
      setBusyId(args.proposal.id);
      const path = `/api/shops/${shop.id}/proposals/${args.proposal.id}/${args.approve ? "approve" : "reject"}`;
      return api<Proposal>(path, {
        json: args.edits && Object.keys(args.edits).length ? { edits: args.edits } : {},
      });
    },
    onSuccess: (result) => {
      if (result.status === "executed") toast.success(`Done: ${result.title}`);
      else if (result.status === "rejected") toast(`Rejected: ${result.title}`);
      else if (result.status === "failed") toast.error(result.error ?? "Action failed");
      else if (result.status === "approved") toast.success(`Approved: ${result.title}`);
      else toast(`${result.title}: ${result.status}`);
      setEditing(null);
      setEdits({});
      setSelected((s) => {
        const next = new Set(s);
        next.delete(result.id);
        return next;
      });
      refresh();
    },
    onError: (error) => toast.error(error.message),
    onSettled: () => setBusyId(null),
  });

  const bulk = useMutation({
    mutationFn: (ids: string[]) =>
      api<{ approved: string[]; errors: Record<string, string> }>(
        `/api/shops/${shop.id}/proposals/bulk-approve`,
        {
          json: { ids },
        },
      ),
    onSuccess: (res) => {
      toast.success(`Approved ${res.approved.length} action${res.approved.length === 1 ? "" : "s"}`);
      const failures = Object.values(res.errors);
      if (failures.length) toast.error(failures[0]);
      setSelected(new Set());
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });

  const current = items[Math.min(focus, Math.max(0, items.length - 1))];

  // Keyboard shortcuts: j/k move, x select, a approve, e edit, r reject.
  useEffect(() => {
    if (tab !== "pending" || !canDecide) return;
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      if (target.closest("input, textarea, select, [contenteditable=true]") || event.metaKey || event.ctrlKey)
        return;
      if (!items.length) return;
      const key = event.key.toLowerCase();
      if (key === "j" || key === "k") {
        event.preventDefault();
        const next = Math.max(0, Math.min(items.length - 1, focus + (key === "j" ? 1 : -1)));
        setFocus(next);
        cardRefs.current[next]?.focus();
        return;
      }
      if (!current || decide.isPending) return;
      if (key === "a")
        decide.mutate({
          proposal: current,
          approve: true,
          edits: editing === current.id ? edits : undefined,
        });
      if (key === "r") decide.mutate({ proposal: current, approve: false });
      if (key === "e" && current.action_type !== "hold_order") {
        setEditing(editing === current.id ? null : current.id);
        setEdits({});
      }
      if (key === "x") {
        setSelected((s) => {
          const next = new Set(s);
          if (next.has(current.id)) next.delete(current.id);
          else next.add(current.id);
          return next;
        });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab, canDecide, items, focus, current, decide, editing, edits]);

  return (
    <>
      <PageHeader
        title="Approvals"
        description="Actions your agents want to take that need a human decision."
        actions={
          tab === "pending" &&
          canDecide &&
          selected.size > 0 && (
            <Button onClick={() => bulk.mutate([...selected])} disabled={bulk.isPending}>
              <CheckCheck aria-hidden /> Approve selected ({selected.size})
            </Button>
          )
        }
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div role="tablist" aria-label="Approval views" className="inline-flex rounded-lg border bg-card p-1">
          {(["pending", "history"] as const).map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              onClick={() => {
                setTab(t);
                setFocus(0);
                setSelected(new Set());
              }}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm",
                tab === t
                  ? "bg-accent font-medium text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t === "pending"
                ? `Pending${list.data && tab === "pending" ? ` (${list.data.pending})` : ""}`
                : "History"}
            </button>
          ))}
        </div>
        {tab === "pending" && canDecide && (
          <p className="hidden items-center gap-2 text-xs text-muted-foreground md:flex">
            <Keyboard className="size-3.5" aria-hidden />
            <kbd className="rounded border px-1">J</kbd>/<kbd className="rounded border px-1">K</kbd> move ·{" "}
            <kbd className="rounded border px-1">A</kbd> approve ·{" "}
            <kbd className="rounded border px-1">E</kbd> edit · <kbd className="rounded border px-1">R</kbd>{" "}
            reject · <kbd className="rounded border px-1">X</kbd> select
          </p>
        )}
      </div>

      {list.isPending ? (
        <div className="grid gap-4">
          <Skeleton className="h-48 rounded-xl" />
          <Skeleton className="h-48 rounded-xl" />
        </div>
      ) : list.isError ? (
        <ErrorState message={list.error.message} onRetry={() => list.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title={tab === "pending" ? "You're all caught up" : "No decisions yet"}
          description={
            tab === "pending"
              ? "New proposals from your agents will appear here the moment they're drafted."
              : "Approved, rejected and expired actions will be listed here."
          }
        />
      ) : (
        <div className="grid max-w-4xl gap-4">
          {!canDecide && tab === "pending" && (
            <p className="text-sm text-muted-foreground">
              You have view-only access; an admin or owner can decide.
            </p>
          )}
          {items.map((proposal, index) => (
            <ProposalCard
              key={proposal.id}
              ref={(el) => {
                cardRefs.current[index] = el;
              }}
              proposal={proposal}
              focused={tab === "pending" && index === focus}
              selected={selected.has(proposal.id)}
              editing={editing === proposal.id}
              edits={editing === proposal.id ? edits : {}}
              busy={busyId === proposal.id}
              canDecide={canDecide}
              now={now}
              onFocus={() => setFocus(index)}
              onSelect={(on) =>
                setSelected((s) => {
                  const next = new Set(s);
                  if (on) next.add(proposal.id);
                  else next.delete(proposal.id);
                  return next;
                })
              }
              onEditToggle={() => {
                setEditing(editing === proposal.id ? null : proposal.id);
                setEdits({});
              }}
              onEdit={(field, value) => setEdits((e) => ({ ...e, [field]: value }))}
              onApprove={() =>
                decide.mutate({ proposal, approve: true, edits: editing === proposal.id ? edits : undefined })
              }
              onReject={() => decide.mutate({ proposal, approve: false })}
            />
          ))}
        </div>
      )}
    </>
  );
}
