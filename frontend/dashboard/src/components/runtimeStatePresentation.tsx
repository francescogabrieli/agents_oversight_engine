export type BadgeVariant =
  | "badge-waiting"
  | "badge-completed"
  | "badge-aborted"
  | "badge-info"
  | "badge-default"
  | "badge-neutral";

export const RUNTIME_STATE_BADGE_VARIANTS: Record<string, BadgeVariant> = {
  waiting_human_review: "badge-waiting",
  completed: "badge-completed",
  review_completed: "badge-completed",
  aborted: "badge-aborted",
};

// Resolve one runtime state into the shared badge presentation.
export function runtimeStateBadgeClass(runtimeState: string | null) {
  return `badge ${RUNTIME_STATE_BADGE_VARIANTS[runtimeState ?? ""] ?? "badge-default"}`;
}

// Resolve one runtime state into the inbox marker presentation.
export function runtimeStateRowClass(runtimeState: string | null) {
  const suffix = RUNTIME_STATE_BADGE_VARIANTS[runtimeState ?? ""] ?? "badge-default";
  return `inbox-row-status inbox-row-status-${suffix.replace("badge-", "")}`;
}

// Render the canonical icon for one runtime state.
export function RuntimeStateIcon({ state }: { state: string | null }) {
  if (state === "waiting_human_review") {
    return (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
      </svg>
    );
  }
  if (state === "completed" || state === "review_completed") {
    return (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="20 6 9 17 4 12"/>
      </svg>
    );
  }
  if (state === "aborted") {
    return (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
      </svg>
    );
  }
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/>
    </svg>
  );
}
