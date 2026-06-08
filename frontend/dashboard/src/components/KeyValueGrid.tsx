type KeyValueGridProps = {
  title: string;
  values: Array<{ label: string; value: string | number | null | undefined }>;
};

export function KeyValueGrid({ title, values }: KeyValueGridProps) {
  return (
    <section className="panel-card">
      <div className="card-body">
        <h2 className="panel-title">{title}</h2>
        <div className="grid gap-3 md:grid-cols-2">
          {values.map((entry) => (
            <div key={entry.label} className="data-chip">
              <div className="data-chip-label">{entry.label}</div>
              <div className="data-chip-value">
                {entry.value === null || entry.value === undefined || entry.value === ""
                  ? "n/a"
                  : String(entry.value)}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
