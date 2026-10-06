import { cn } from "@/lib/utils";

const tones = {
  live: "bg-success",
  busy: "bg-primary",
  warn: "bg-warning",
  error: "bg-destructive",
  idle: "bg-muted-foreground/60",
} as const;

export type StatusTone = keyof typeof tones;

export function StatusDot({
  tone = "live",
  pulse = false,
  className,
  label,
}: {
  tone?: StatusTone;
  pulse?: boolean;
  className?: string;
  label?: string;
}) {
  return (
    <span
      className={cn("relative inline-flex size-2 shrink-0", className)}
      role={label ? "img" : undefined}
      aria-label={label}
    >
      {pulse && <span className={cn("absolute inset-0 animate-pulse-ring rounded-full", tones[tone])} />}
      <span className={cn("relative inline-flex size-2 rounded-full", tones[tone])} />
    </span>
  );
}
