"use client";

import { AlertTriangle, Info, OctagonAlert, Radio } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { EmptyState } from "@/components/empty-state";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { ActivityData, LiveEvent, Severity } from "@/lib/live";
import { cn, timeAgo } from "@/lib/utils";

const SEVERITY: Record<Severity, { icon: typeof Info; className: string; label: string }> = {
  info: { icon: Info, className: "text-muted-foreground", label: "Info" },
  warning: { icon: AlertTriangle, className: "text-warning", label: "Warning" },
  critical: { icon: OctagonAlert, className: "text-destructive", label: "Critical" },
};

export function ActivityStream({ items, now }: { items: LiveEvent<ActivityData>[]; now: number }) {
  return (
    <Card className="gap-2 pb-2">
      <CardHeader>
        <CardTitle>Activity</CardTitle>
        <CardDescription>What happened, and why it matters.</CardDescription>
      </CardHeader>
      <CardContent className="px-2">
        {items.length === 0 ? (
          <EmptyState
            icon={Radio}
            title="Listening for events"
            description="Store events and agent actions will stream in here."
          />
        ) : (
          <ol aria-live="polite" aria-relevant="additions" className="max-h-[30rem] overflow-y-auto">
            <AnimatePresence initial={false}>
              {items.map((item) => {
                const severity = SEVERITY[item.data.severity] ?? SEVERITY.info;
                const Icon = severity.icon;
                return (
                  <motion.li
                    key={item.id}
                    layout
                    initial={{ opacity: 0, y: -6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.25 }}
                    className="flex gap-3 rounded-lg px-3 py-2"
                  >
                    <Icon
                      className={cn("mt-0.5 size-4 shrink-0", severity.className)}
                      aria-label={severity.label}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm leading-snug">{item.data.title}</p>
                      {item.data.detail && (
                        <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                          {item.data.detail}
                        </p>
                      )}
                    </div>
                    <time dateTime={item.at} className="shrink-0 text-xs text-muted-foreground">
                      {timeAgo(item.at, now)}
                    </time>
                  </motion.li>
                );
              })}
            </AnimatePresence>
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
