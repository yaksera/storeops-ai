import { LogoMark } from "@/components/brand";

export function FullPageSpinner({ label }: { label: string }) {
  return (
    <div className="grid min-h-dvh place-items-center bg-grid" role="status" aria-live="polite">
      <div className="flex flex-col items-center gap-4 text-sm text-muted-foreground">
        <LogoMark className="size-10 animate-pulse" />
        {label}
      </div>
    </div>
  );
}
