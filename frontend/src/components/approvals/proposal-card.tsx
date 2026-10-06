"use client";

import { Check, Loader2, Pencil, X } from "lucide-react";
import { forwardRef, useState } from "react";

import { AgentIcon } from "@/components/agent-icon";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { Proposal, ProposalStatus, RiskLevel } from "@/lib/agents";
import { cn, formatMoney, timeAgo } from "@/lib/utils";

const RISK: Record<RiskLevel, { label: string; variant: "success" | "warning" | "destructive" }> = {
  low: { label: "Low risk", variant: "success" },
  medium: { label: "Medium risk", variant: "warning" },
  high: { label: "High risk", variant: "destructive" },
};

const STATUS: Record<
  ProposalStatus,
  { label: string; variant: "default" | "success" | "warning" | "destructive" | "outline" | "secondary" }
> = {
  proposed: { label: "Awaiting approval", variant: "warning" },
  approved: { label: "Approved", variant: "default" },
  executed: { label: "Done", variant: "success" },
  rejected: { label: "Rejected", variant: "outline" },
  failed: { label: "Failed", variant: "destructive" },
  expired: { label: "Expired", variant: "secondary" },
};

export type Edits = Record<string, string | number>;

function EmailPreviewBlock({
  proposal,
  editing,
  edits,
  onEdit,
}: {
  proposal: Proposal;
  editing: boolean;
  edits: Edits;
  onEdit: (field: string, value: string | number) => void;
}) {
  const email = proposal.preview.email;
  if (!email) return null;
  const isPo = proposal.action_type === "draft_po";
  const bodyField = isPo ? "body" : "text";
  const subject = String(edits.subject ?? proposal.payload.subject ?? email.subject);
  const body = String(edits[bodyField] ?? proposal.payload[bodyField] ?? email.body);
  return (
    <div className="rounded-lg border bg-background/40 text-sm">
      <div className="grid gap-1 border-b px-4 py-2.5 text-xs text-muted-foreground">
        <p>
          To: <span className="text-foreground">{email.to}</span>
        </p>
        {editing ? (
          <label className="grid gap-1">
            Subject
            <Input value={subject} onChange={(e) => onEdit("subject", e.target.value)} />
          </label>
        ) : (
          <p>
            Subject: <span className="font-medium text-foreground">{subject}</span>
          </p>
        )}
        {isPo && editing && (
          <label className="grid max-w-40 gap-1">
            Quantity
            <Input
              type="number"
              min={1}
              value={Number(edits.quantity ?? proposal.payload.quantity)}
              onChange={(e) => onEdit("quantity", Number(e.target.value))}
            />
          </label>
        )}
      </div>
      {editing ? (
        <Textarea
          aria-label="Email body"
          className="min-h-48 rounded-none border-0 font-mono text-xs"
          value={body}
          onChange={(e) => onEdit(bodyField, e.target.value)}
        />
      ) : (
        <pre className="max-h-56 overflow-auto px-4 py-3 font-sans text-sm whitespace-pre-wrap text-muted-foreground">
          {body}
        </pre>
      )}
    </div>
  );
}

function PriceBlock({ proposal }: { proposal: Proposal }) {
  const price = proposal.preview.price;
  if (!price) return null;
  const change = (price.to_minor - price.from_minor) / price.from_minor;
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-background/40 px-4 py-3 text-sm">
      <span className="tabular text-muted-foreground line-through">
        {formatMoney(price.from_minor, price.currency)}
      </span>
      <span aria-hidden>→</span>
      <span className="tabular text-lg font-semibold">{formatMoney(price.to_minor, price.currency)}</span>
      <Badge variant={change > 0 ? "info" : "warning"}>
        {change > 0 ? "+" : ""}
        {(change * 100).toFixed(0)}%
      </Badge>
    </div>
  );
}

function ReviewBlock({
  proposal,
  editing,
  edits,
  onEdit,
}: {
  proposal: Proposal;
  editing: boolean;
  edits: Edits;
  onEdit: (field: string, value: string | number) => void;
}) {
  const review = proposal.preview.review;
  if (!review) return null;
  const reply = String(edits.reply ?? proposal.payload.reply ?? "");
  return (
    <div className="grid gap-3 rounded-lg border bg-background/40 px-4 py-3 text-sm">
      <blockquote>
        <p className="font-medium">
          {"★".repeat(review.rating)}
          <span className="text-muted-foreground">{"★".repeat(5 - review.rating)}</span> {review.title}
        </p>
        <p className="mt-1 text-muted-foreground">{review.body}</p>
      </blockquote>
      {editing ? (
        <Textarea aria-label="Public reply" value={reply} onChange={(e) => onEdit("reply", e.target.value)} />
      ) : (
        <p className="border-l-2 border-primary/40 pl-3">
          <span className="text-xs text-muted-foreground">Public reply: </span>
          {reply}
        </p>
      )}
    </div>
  );
}

function FactorsBlock({ proposal }: { proposal: Proposal }) {
  const factors = proposal.preview.factors;
  const order = proposal.preview.order;
  if (!factors) return null;
  return (
    <div className="grid gap-3 rounded-lg border bg-background/40 px-4 py-3 text-sm">
      {order && (
        <p className="text-muted-foreground">
          <span className="font-medium text-foreground">{order.name}</span> ·{" "}
          {formatMoney(order.total_minor, order.currency)} · {order.customer_name ?? "Guest"}
        </p>
      )}
      <ul className="grid gap-1.5">
        {factors.map((f) => (
          <li key={f.code} className="flex items-center gap-3">
            <span className="tabular w-10 shrink-0 rounded bg-destructive/15 px-1.5 py-0.5 text-center text-xs font-medium text-destructive">
              +{f.points}
            </span>
            <span>{f.reason}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

interface Props {
  proposal: Proposal;
  focused: boolean;
  selected: boolean;
  editing: boolean;
  edits: Edits;
  busy: boolean;
  canDecide: boolean;
  now: number;
  onSelect: (selected: boolean) => void;
  onEditToggle: () => void;
  onEdit: (field: string, value: string | number) => void;
  onApprove: () => void;
  onReject: () => void;
  onFocus: () => void;
}

export const ProposalCard = forwardRef<HTMLElement, Props>(function ProposalCard(
  {
    proposal,
    focused,
    selected,
    editing,
    edits,
    busy,
    canDecide,
    now,
    onSelect,
    onEditToggle,
    onEdit,
    onApprove,
    onReject,
    onFocus,
  },
  ref,
) {
  const pending = proposal.status === "proposed";
  const editable = !["hold_order", "change_price"].includes(proposal.action_type);
  const risk = RISK[proposal.risk_level];
  const status = STATUS[proposal.status];
  const [showWhy, setShowWhy] = useState(true);

  return (
    <article
      ref={ref}
      tabIndex={-1}
      onFocus={onFocus}
      aria-label={proposal.title}
      className={cn(
        "grid gap-4 rounded-xl border bg-card p-5 transition-shadow outline-none",
        focused && "ring-2 ring-ring/60",
        selected && "border-primary/40",
      )}
    >
      <header className="flex flex-wrap items-start gap-3">
        {pending && canDecide && (
          <input
            type="checkbox"
            aria-label={`Select ${proposal.title}`}
            className="mt-2 size-4 accent-[var(--primary)]"
            checked={selected}
            onChange={(e) => onSelect(e.target.checked)}
          />
        )}
        <AgentIcon agent={proposal.agent} />
        <div className="min-w-0 flex-1">
          <p className="text-xs text-muted-foreground">
            {proposal.agent_label}
            {proposal.created_at && <> · {timeAgo(proposal.created_at, now)}</>}
          </p>
          <h2 className="mt-0.5 font-medium">{proposal.title}</h2>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge variant={risk.variant}>{risk.label}</Badge>
          {!pending && <Badge variant={status.variant}>{status.label}</Badge>}
        </div>
      </header>

      <p className="text-sm">{proposal.summary}</p>
      {showWhy && proposal.rationale && (
        <p className="border-l-2 border-primary/40 pl-3 text-sm text-muted-foreground">
          <span className="font-medium text-foreground">Why: </span>
          {proposal.rationale}
        </p>
      )}

      <FactorsBlock proposal={proposal} />
      <EmailPreviewBlock proposal={proposal} editing={editing} edits={edits} onEdit={onEdit} />
      <PriceBlock proposal={proposal} />
      <ReviewBlock proposal={proposal} editing={editing} edits={edits} onEdit={onEdit} />

      {proposal.error && <p className="text-sm text-destructive">{proposal.error}</p>}
      {proposal.result?.discount_code ? (
        <p className="text-sm text-muted-foreground">
          Discount code issued:{" "}
          <span className="font-mono text-foreground">{String(proposal.result.discount_code)}</span>
        </p>
      ) : null}

      {pending && canDecide && (
        <footer className="flex flex-wrap items-center gap-2">
          <Button onClick={onApprove} disabled={busy}>
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />}
            {editing ? "Approve with edits" : "Approve"}
            <kbd className="ml-1 hidden rounded border border-primary-foreground/30 px-1 text-[10px] sm:inline">
              A
            </kbd>
          </Button>
          {editable && (
            <Button variant="outline" onClick={onEditToggle} disabled={busy} aria-pressed={editing}>
              <Pencil aria-hidden /> {editing ? "Cancel edit" : "Edit"}
              <kbd className="ml-1 hidden rounded border px-1 text-[10px] sm:inline">E</kbd>
            </Button>
          )}
          <Button variant="ghost" onClick={onReject} disabled={busy}>
            <X aria-hidden /> Reject
            <kbd className="ml-1 hidden rounded border px-1 text-[10px] sm:inline">R</kbd>
          </Button>
          <button
            type="button"
            className="ml-auto text-xs text-muted-foreground underline-offset-4 hover:underline"
            onClick={() => setShowWhy((v) => !v)}
          >
            {showWhy ? "Hide reasoning" : "Show reasoning"}
          </button>
        </footer>
      )}
    </article>
  );
});
