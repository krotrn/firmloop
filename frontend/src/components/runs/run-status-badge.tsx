import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

interface StatusStyle {
  label: string;
  variant: BadgeVariant;
  className?: string;
}

const working = (label: string): StatusStyle => ({
  label,
  variant: "outline",
  className: "motion-safe:animate-pulse",
});

const waiting: StatusStyle = {
  label: "Needs you",
  variant: "outline",
  className:
    "border-amber-500/40 bg-amber-500/15 text-amber-700 dark:text-amber-400",
};

const STATUS_STYLES: Record<string, StatusStyle> = {
  pending: working("Queued"),
  understanding: working("Understanding"),
  awaiting_answer: waiting,
  awaiting_confirmation: waiting,
  generating_tests: working("Writing tests"),
  coding: working("Coding"),
  checking: working("Checking"),
  native_testing: working("Testing"),
  building: working("Building"),
  flashing: working("Flashing"),
  verifying: working("Verifying"),
  succeeded: { label: "Succeeded", variant: "default" },
  failed: { label: "Failed", variant: "destructive" },
  cancelled: { label: "Cancelled", variant: "secondary" },
};

export function RunStatusBadge({
  status,
  className,
}: {
  status: string;
  className?: string;
}) {
  const style = Object.hasOwn(STATUS_STYLES, status)
    ? STATUS_STYLES[status]
    : undefined;
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
