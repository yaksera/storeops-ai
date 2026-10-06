"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, CheckCircle2, Inbox, Loader2, Send, User } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { EmptyState, ErrorState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { hasRole, useShop } from "@/components/shop-context";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useNow } from "@/hooks/use-live-ops";
import { useShopEvents } from "@/hooks/use-shop-events";
import { api } from "@/lib/api";
import { cn, formatMoney, timeAgo } from "@/lib/utils";

type Queue = "handover" | "open" | "resolved";

interface TicketSummary {
  id: string;
  subject: string;
  channel: "email" | "chat" | "review";
  status: string;
  priority: number;
  customer_email: string | null;
  order_name: string | null;
  escalation_reason: string | null;
  last_message_at: string | null;
  created_at: string;
}

interface TicketDetail extends TicketSummary {
  order: {
    name: string;
    total_minor: number;
    currency: string;
    fulfillment_status: string | null;
    tracking_number: string | null;
    is_held: boolean;
  } | null;
  review: { rating: number; title: string | null; body: string } | null;
  messages: { id: string; direction: string; author_type: string; body: string; sent_at: string }[];
  draft: { proposal_id: string; text: string; summary: string } | null;
}

const QUEUES: { id: Queue; label: string }[] = [
  { id: "handover", label: "Hand-over" },
  { id: "open", label: "Open" },
  { id: "resolved", label: "Resolved" },
];

function TicketPane({ ticketId, onDone }: { ticketId: string; onDone: () => void }) {
  const shop = useShop();
  const now = useNow(30_000);
  const queryClient = useQueryClient();
  const canReply = hasRole(shop.role, "admin");
  const [text, setText] = useState<string | null>(null);
  const detail = useQuery({
    queryKey: ["shop", shop.id, "ticket", ticketId],
    queryFn: () => api<TicketDetail>(`/api/shops/${shop.id}/tickets/${ticketId}`),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["shop", shop.id, "tickets"] });
    void queryClient.invalidateQueries({ queryKey: ["shop", shop.id, "ticket", ticketId] });
  };
  const reply = useMutation({
    mutationFn: (body: { text: string; resolve: boolean }) =>
      api(`/api/shops/${shop.id}/tickets/${ticketId}/reply`, { json: body }),
    onSuccess: () => {
      toast.success("Reply sent");
      setText(null);
      refresh();
      onDone();
    },
    onError: (error) => toast.error(error.message),
  });
  const approveDraft = useMutation({
    mutationFn: (proposalId: string) =>
      api(`/api/shops/${shop.id}/proposals/${proposalId}/approve`, { json: {} }),
    onSuccess: () => {
      toast.success("Draft approved");
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });

  if (detail.isPending) return <Skeleton className="h-96 rounded-xl" />;
  if (detail.isError) return <ErrorState message={detail.error.message} onRetry={() => detail.refetch()} />;
  const t = detail.data;
  const value = text ?? t.draft?.text ?? "";

  return (
    <div className="grid content-start gap-4 rounded-xl border bg-card p-5">
      <header className="grid gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-medium">{t.subject}</h2>
          <Badge
            variant={t.status === "escalated" ? "warning" : t.status === "resolved" ? "success" : "secondary"}
          >
            {t.status}
          </Badge>
        </div>
        <p className="text-xs text-muted-foreground">
          {t.customer_email ?? "Review"} · {t.channel}
          {t.order && (
            <>
              {" "}
              · {t.order.name} · {formatMoney(t.order.total_minor, t.order.currency)} ·{" "}
              {t.order.is_held ? "on hold" : (t.order.fulfillment_status ?? "unfulfilled")}
              {t.order.tracking_number && ` · tracking ${t.order.tracking_number}`}
            </>
          )}
        </p>
        {t.escalation_reason && (
          <p className="mt-1 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
            Handed over: {t.escalation_reason}.
          </p>
        )}
      </header>

      {t.review && (
        <blockquote className="rounded-lg border bg-background/40 px-4 py-3 text-sm">
          <p className="font-medium">
            {"★".repeat(t.review.rating)}
            <span className="text-muted-foreground">{"★".repeat(5 - t.review.rating)}</span> {t.review.title}
          </p>
          <p className="mt-1 text-muted-foreground">{t.review.body}</p>
        </blockquote>
      )}

      <ol className="grid gap-3" aria-label="Conversation">
        {t.messages.map((m) => {
          const inbound = m.direction === "inbound";
          const Icon = inbound ? User : m.author_type === "agent" ? Bot : User;
          return (
            <li key={m.id} className={cn("flex gap-3", !inbound && "flex-row-reverse text-right")}>
              <span className="grid size-8 shrink-0 place-items-center rounded-full border bg-muted">
                <Icon className="size-4 text-muted-foreground" aria-hidden />
              </span>
              <div
                className={cn(
                  "max-w-[80%] rounded-xl px-4 py-2.5 text-left text-sm whitespace-pre-wrap",
                  inbound ? "bg-muted" : "bg-primary/10",
                )}
              >
                {m.body}
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {inbound ? "Customer" : m.author_type === "agent" ? "Support Agent" : "You"} ·{" "}
                  {timeAgo(m.sent_at, now)}
                </p>
              </div>
            </li>
          );
        })}
      </ol>

      {canReply && t.status !== "resolved" && t.customer_email && (
        <form
          className="grid gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            reply.mutate({ text: value, resolve: true });
          }}
        >
          {t.draft && text === null && (
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <Bot className="size-3.5" aria-hidden /> AI draft, ready to approve or edit
            </p>
          )}
          <label htmlFor="reply" className="sr-only">
            Reply
          </label>
          <Textarea
            id="reply"
            value={value}
            onChange={(e) => setText(e.target.value)}
            placeholder="Write a reply…"
            className="min-h-32"
          />
          <div className="flex flex-wrap gap-2">
            {t.draft && text === null ? (
              <Button
                type="button"
                onClick={() => approveDraft.mutate(t.draft!.proposal_id)}
                disabled={approveDraft.isPending}
              >
                {approveDraft.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden />
                ) : (
                  <CheckCircle2 aria-hidden />
                )}
                Approve AI draft
              </Button>
            ) : (
              <Button type="submit" disabled={reply.isPending || !value.trim()}>
                {reply.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />}
                Send and resolve
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              disabled={reply.isPending || !value.trim()}
              onClick={() => reply.mutate({ text: value, resolve: false })}
            >
              Send, keep open
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

export default function SupportPage() {
  const shop = useShop();
  const now = useNow(30_000);
  const queryClient = useQueryClient();
  const [queue, setQueue] = useState<Queue>("handover");
  const [selected, setSelected] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ["shop", shop.id, "tickets", queue],
    queryFn: () =>
      api<{ items: TicketSummary[]; counts: Record<Queue, number> }>(
        `/api/shops/${shop.id}/tickets?queue=${queue}`,
      ),
  });
  useShopEvents(shop.id, ["ticket.created", "ticket.updated", "proposal.updated"], () => {
    void queryClient.invalidateQueries({ queryKey: ["shop", shop.id, "tickets"] });
  });
  const items = list.data?.items ?? [];
  const active = selected && items.some((t) => t.id === selected) ? selected : (items[0]?.id ?? null);

  return (
    <>
      <PageHeader
        title="Support"
        description="Conversations the Support Agent handled, and the ones it handed to you."
      />
      <div role="tablist" aria-label="Queues" className="mb-4 inline-flex rounded-lg border bg-card p-1">
        {QUEUES.map((q) => (
          <button
            key={q.id}
            role="tab"
            aria-selected={queue === q.id}
            onClick={() => {
              setQueue(q.id);
              setSelected(null);
            }}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm",
              queue === q.id ? "bg-accent font-medium" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {q.label}
            {list.data && (
              <span className="tabular ml-1.5 text-muted-foreground">{list.data.counts[q.id]}</span>
            )}
          </button>
        ))}
      </div>

      {list.isPending ? (
        <Skeleton className="h-96 rounded-xl" />
      ) : list.isError ? (
        <ErrorState message={list.error.message} onRetry={() => list.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title={queue === "handover" ? "Nothing needs you right now" : "No conversations here"}
          description={
            queue === "handover"
              ? "Angry customers, legal language, big refunds and unclear requests land here."
              : undefined
          }
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
          <ul className="grid content-start gap-1 rounded-xl border bg-card p-2" aria-label="Tickets">
            {items.map((t) => (
              <li key={t.id}>
                <button
                  onClick={() => setSelected(t.id)}
                  aria-current={active === t.id}
                  className={cn(
                    "grid w-full gap-0.5 rounded-lg px-3 py-2.5 text-left",
                    active === t.id ? "bg-accent" : "hover:bg-accent/50",
                  )}
                >
                  <span className="flex items-center gap-2 text-sm font-medium">
                    {t.priority === 1 && (
                      <span className="size-1.5 rounded-full bg-warning" aria-label="High priority" />
                    )}
                    <span className="truncate">{t.subject}</span>
                  </span>
                  <span className="truncate text-xs text-muted-foreground">
                    {t.customer_email ?? t.channel}
                    {t.order_name && ` · ${t.order_name}`}
                    {t.last_message_at && ` · ${timeAgo(t.last_message_at, now)}`}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {active && <TicketPane key={active} ticketId={active} onDone={() => setSelected(null)} />}
        </div>
      )}
    </>
  );
}
