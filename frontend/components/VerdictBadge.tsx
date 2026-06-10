import { verdictClasses } from "@/lib/report";
import type { Verdict } from "@/lib/types";

interface Props {
  verdict: Verdict | null | undefined;
  size?: "sm" | "md" | "lg";
}

const SIZES = {
  sm: "px-2 py-0.5 text-xs",
  md: "px-3 py-1 text-sm",
  lg: "px-4 py-1.5 text-base",
} as const;

export function VerdictBadge({ verdict, size = "md" }: Props) {
  return (
    <span
      className={`inline-flex items-center rounded-full border font-semibold tracking-wide ${SIZES[size]} ${verdictClasses(verdict)}`}
    >
      {verdict ?? "—"}
    </span>
  );
}
