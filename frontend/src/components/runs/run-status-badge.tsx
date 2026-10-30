import { Badge } from "@/components/ui/badge";
import { isTerminal, isWaiting, isWorking } from "@/lib/run-status";
import { cn } from "@/lib/utils";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

interface StatusStyle {
  label: string;
  variant: BadgeVariant;
  className?: string;
}

const LABELS: Record<string, string> = {
  pending: "Queued",
  understanding: "Understanding",
  generating_tests: "Writing tests",
  coding: "Coding",
  checking: "Checking",
  native_testing: "Testing",
  building: "Building",
  flashing: "Flashing",
  verifying: "Verifying",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
};

const TERMINAL_VARIANTS: Record<string, BadgeVariant> = {
  succeeded: "default",
  failed: "destructive",
  cancelled: "secondary",
};

function styleFor(status: string): StatusStyle | undefined {
  if (isWorking(status)) {
    return {
      label: LABELS[status] ?? status,
      variant: "outline",
      className: "motion-safe:animate-pulse",
    };
  }
  if (isWaiting(status)) {
    return {
      label: "Needs you",
      variant: "outline",
      className:
        "border-amber-500/40 bg-amber-500/15 text-amber-700 dark:text-amber-400",
    };
  }
  if (isTerminal(status)) {
    return {
      label: LABELS[status] ?? status,
      variant: TERMINAL_VARIANTS[status] ?? "outline",
    };
  }
  return undefined;
}

export function RunStatusBadge({
  status,
  className,
}: {
  status: string;
  className?: string;
}) {
  const style = styleFor(status);
  if (!style) {
    return (
      <Badge variant="outline" className={className}>
        {status}
      </Badge>
    );
  }
  return (
    <Badge variant={style.variant} className={cn(style.className, className)}>
      {style.label}
    </Badge>
  );
}
