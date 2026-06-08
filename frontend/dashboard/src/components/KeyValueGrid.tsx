type KeyValueGridProps = {
  title: string;
  values: Array<{ label: string; value: string | number | null | undefined }>;
};

export function KeyValueGrid({ title, values }: KeyValueGridProps) {
  // filter out entirely empty values so the grid doesn't look padded with n/a tiles
  const entries = values.filter(
    (e) => e.value !== null && e.value !== undefined && e.value !== "",
  );

  return (
    <div className="card">
      <div className="card-header">
        <h2 className="card-title">{title}</h2>
      </div>
      {entries.length === 0 ? (
        <div className="card-body" style={{ color: "var(--text-muted)", fontSize: "0.82rem" }}>
          No data available.
        </div>
      ) : (
        <div className="kv-grid kv-grid-2col" style={{ borderRadius: 0, border: "none", borderTop: "1px solid var(--border-soft)" }}>
          {entries.map((entry) => (
            <div key={entry.label} className="kv-entry">
              <div className="kv-label">{entry.label}</div>
              <div className="kv-value">{String(entry.value)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

