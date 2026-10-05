"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, OctagonPause, ShieldCheck, Trash2, UserPlus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ErrorState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { hasRole, useShop } from "@/components/shop-context";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, type Member, type Role, type ShopSettings } from "@/lib/api";
import { formatMoney } from "@/lib/utils";

function useSettings(shopId: string) {
  return useQuery({
    queryKey: ["shop", shopId, "settings"],
    queryFn: () => api<ShopSettings>(`/api/shops/${shopId}/settings`),
  });
}

function GuardrailsCard() {
  const shop = useShop();
  const queryClient = useQueryClient();
  const settings = useSettings(shop.id);
  const canEdit = hasRole(shop.role, "admin");
  const update = useMutation({
    mutationFn: (patch: Partial<ShopSettings>) =>
      api<ShopSettings>(`/api/shops/${shop.id}/settings`, { method: "PATCH", json: patch }),
    onSuccess: (data) => {
      queryClient.setQueryData(["shop", shop.id, "settings"], data);
      toast.success("Settings saved");
    },
    onError: (error) => toast.error(error.message),
  });

  if (settings.isPending) {
    return (
      <Card>
        <CardContent className="grid gap-4">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-16" />
          <Skeleton className="h-16" />
        </CardContent>
      </Card>
    );
  }
  if (settings.isError)
    return <ErrorState message={settings.error.message} onRetry={() => settings.refetch()} />;
  const s = settings.data;

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const text = (name: string) => {
      const value = String(form.get(name) ?? "").trim();
      return value === "" ? null : value;
    };
    update.mutate({
      fraud_threshold: Number(form.get("fraud_threshold")),
      max_discount_pct: Number(form.get("max_discount_pct")),
      refund_ceiling_minor: Math.round(Number(form.get("refund_ceiling")) * 100),
      alert_email: text("alert_email"),
      support_email: text("support_email"),
      sender_name: text("sender_name"),
      physical_address: text("physical_address"),
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4 text-primary" aria-hidden /> Guardrails
        </CardTitle>
        <CardDescription>Limits every agent must respect, whatever its autonomy level.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6">
        <div className="grid gap-3 sm:grid-cols-2">
          <ToggleRow
            id="kill_switch"
            icon={<OctagonPause className="size-4 text-destructive" aria-hidden />}
            title="Kill switch"
            description="Immediately stops every agent action for this store."
            checked={s.kill_switch}
            disabled={!canEdit || update.isPending}
            onChange={(value) => update.mutate({ kill_switch: value })}
          />
          <ToggleRow
            id="dry_run"
            title="Dry-run mode"
            description="Agents decide and log, but never execute side effects."
            checked={s.dry_run}
            disabled={!canEdit || update.isPending}
            onChange={(value) => update.mutate({ dry_run: value })}
          />
        </div>

        <form onSubmit={onSubmit} className="grid gap-4" aria-label="Guardrail limits">
          <fieldset disabled={!canEdit || update.isPending} className="grid gap-4 sm:grid-cols-3">
            <Field label="Fraud hold threshold" hint="Score 0–100">
              <Input
                name="fraud_threshold"
                type="number"
                min={0}
                max={100}
                defaultValue={s.fraud_threshold}
                required
              />
            </Field>
            <Field label="Max discount" hint="Percent, 10% ceiling">
              <Input
                name="max_discount_pct"
                type="number"
                min={0}
                max={10}
                defaultValue={s.max_discount_pct}
                required
              />
            </Field>
            <Field
              label="Refund ceiling"
              hint={`Currently ${formatMoney(s.refund_ceiling_minor, shop.currency)}`}
            >
              <Input
                name="refund_ceiling"
                type="number"
                min={0}
                step="0.01"
                defaultValue={(s.refund_ceiling_minor / 100).toFixed(2)}
                required
              />
            </Field>
            <Field label="Alert email">
              <Input
                name="alert_email"
                type="email"
                defaultValue={s.alert_email ?? ""}
                placeholder="ops@store.example"
              />
            </Field>
            <Field label="Support email">
              <Input
                name="support_email"
                type="email"
                defaultValue={s.support_email ?? ""}
                placeholder="help@store.example"
              />
            </Field>
            <Field label="Sender name">
              <Input name="sender_name" defaultValue={s.sender_name ?? ""} placeholder={shop.name} />
            </Field>
            <div className="sm:col-span-3">
              <Field label="Physical mailing address" hint="Included in marketing email footers (CAN-SPAM).">
                <Input
                  name="physical_address"
                  defaultValue={s.physical_address ?? ""}
                  placeholder="123 Trailhead Way, Boulder, CO"
                />
              </Field>
            </div>
          </fieldset>
          {canEdit ? (
            <div>
              <Button type="submit" disabled={update.isPending}>
                {update.isPending && <Loader2 className="animate-spin" aria-hidden />}
                Save limits
              </Button>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">You have view-only access to this store.</p>
          )}
        </form>
      </CardContent>
    </Card>
  );
}

function ToggleRow(props: {
  id: string;
  title: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  icon?: React.ReactNode;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-lg border bg-background/30 p-4">
      <div className="grid gap-1">
        <Label htmlFor={props.id} className="gap-2">
          {props.icon}
          {props.title}
        </Label>
        <p className="text-sm text-muted-foreground">{props.description}</p>
      </div>
      <Switch
        id={props.id}
        checked={props.checked}
        disabled={props.disabled}
        onCheckedChange={props.onChange}
      />
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactElement }) {
  return (
    <label className="grid gap-2 text-sm">
      <span className="font-medium">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </label>
  );
}

const roles: Role[] = ["viewer", "admin", "owner"];

function TeamCard() {
  const shop = useShop();
  const queryClient = useQueryClient();
  const isOwner = hasRole(shop.role, "owner");
  const key = ["shop", shop.id, "members"];
  const members = useQuery({ queryKey: key, queryFn: () => api<Member[]>(`/api/shops/${shop.id}/members`) });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");

  const refresh = () => queryClient.invalidateQueries({ queryKey: key });
  const add = useMutation({
    mutationFn: () => api<Member>(`/api/shops/${shop.id}/members`, { json: { email, role } }),
    onSuccess: () => {
      setEmail("");
      toast.success("Member added");
      return refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const changeRole = useMutation({
    mutationFn: (vars: { userId: string; role: Role }) =>
      api<Member>(`/api/shops/${shop.id}/members/${vars.userId}`, {
        method: "PATCH",
        json: { role: vars.role },
      }),
    onSuccess: refresh,
    onError: (error) => toast.error(error.message),
  });
  const remove = useMutation({
    mutationFn: (userId: string) => api(`/api/shops/${shop.id}/members/${userId}`, { method: "DELETE" }),
    onSuccess: refresh,
    onError: (error) => toast.error(error.message),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Team</CardTitle>
        <CardDescription>
          Owners manage members and billing, admins approve actions, viewers watch.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {members.isPending ? (
          <div className="grid gap-2">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : members.isError ? (
          <ErrorState message={members.error.message} onRetry={() => members.refetch()} />
        ) : (
          <ul className="divide-y rounded-lg border">
            {members.data.map((m) => (
              <li key={m.user_id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{m.full_name || m.email}</p>
                  <p className="truncate text-xs text-muted-foreground">{m.email}</p>
                </div>
                {isOwner ? (
                  <select
                    aria-label={`Role for ${m.email}`}
                    className="h-8 rounded-md border border-input bg-background px-2 text-sm"
                    value={m.role}
                    onChange={(e) => changeRole.mutate({ userId: m.user_id, role: e.target.value as Role })}
                  >
                    {roles.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Badge variant="secondary">{m.role}</Badge>
                )}
                {isOwner && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Remove ${m.email}`}
                    onClick={() => remove.mutate(m.user_id)}
                  >
                    <Trash2 />
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {isOwner && (
          <form
            className="flex flex-wrap gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              add.mutate();
            }}
          >
            <Input
              type="email"
              required
              placeholder="teammate@store.example"
              aria-label="Teammate email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="min-w-56 flex-1"
            />
            <select
              aria-label="Role for new member"
              className="h-9 rounded-md border border-input bg-background px-2 text-sm"
              value={role}
              onChange={(e) => setRole(e.target.value as Role)}
            >
              {roles.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <Button type="submit" variant="secondary" disabled={add.isPending}>
              <UserPlus aria-hidden /> Add member
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" description="Guardrails and team access for this store." />
      <div className="grid max-w-4xl gap-6">
        <GuardrailsCard />
        <TeamCard />
      </div>
    </>
  );
}
