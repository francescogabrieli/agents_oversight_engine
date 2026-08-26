type KeyValueGridProps = {
  title: string;
  values: Array<{ label: string; value: string | number | null | undefined }>;
};

const MONO_RE = /^(thread_|req_|step_|run_|[a-z][a-z0-9]*(?:_[a-z0-9]+){2,}$)/;
const STATUS_BADGE: Record<string, BadgeVariant> = {
  ...RUNTIME_STATE_BADGE_VARIANTS,
  yes:                  "badge-completed",
  no:                   "badge-neutral",
  sì:                   "badge-completed",
  escalate:             "badge-waiting",
  uncertain:            "badge-info",
  pending:              "badge-waiting",
};

function isLongValue(value: string) {
  return value.length > 55 || value.includes(" ") || value.includes("\n");
}

function isMono(value: string) {
  return MONO_RE.test(value) || /^[\d.]+$/.test(value);
}

function ValueCell({ raw }: { raw: string }) {
  const trimmed = raw.trim().toLowerCase();
  const badgeVariant = STATUS_BADGE[trimmed];
  if (badgeVariant) {
    return <span className={`badge ${badgeVariant}`}>{raw}</span>;
  }
  if (isMono(raw)) {
    return <div className="kv-value kv-value-mono">{raw}</div>;
  }
  return <div className="kv-value">{raw}</div>;
}

export function KeyValueGrid({ title, values }: KeyValueGridProps) {
  const entries = values.filter(
    (e) => e.value !== null && e.value !== undefined && e.value !== "",
  );

  return (
    <div className="kv-section">
      <p className="kv-section-title">{title}</p>
      {entries.length === 0 ? (
        <div className="kv-grid">
          <div className="kv-empty">Nessun dato disponibile.</div>
        </div>
      ) : (
        <div className="kv-grid kv-grid-2col">
          {entries.map((entry) => {
            const raw = String(entry.value);
            const full = isLongValue(raw);
            return (
              <div
                key={entry.label}
                className={full ? "kv-entry kv-entry-full" : "kv-entry"}
              >
                <div className="kv-label">{entry.label}</div>
                <ValueCell raw={raw} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
import {
  RUNTIME_STATE_BADGE_VARIANTS,
  type BadgeVariant,
} from "./runtimeStatePresentation";
