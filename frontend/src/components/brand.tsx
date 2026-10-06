import { cn } from "@/lib/utils";

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={cn("size-7", className)}>
      <rect width="32" height="32" rx="8" className="fill-card stroke-border" strokeWidth="1" />
      <path
        d="M8 20.5 13 15l4 3.5L24 11"
        fill="none"
        className="stroke-primary"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="24" cy="11" r="2.5" className="fill-primary" />
    </svg>
  );
}

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2 font-semibold tracking-tight", className)}>
      <LogoMark />
      <span>
        StoreOps <span className="text-primary">AI</span>
      </span>
    </span>
  );
}
