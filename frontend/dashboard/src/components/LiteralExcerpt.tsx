// Highlight exact text only: numerical equivalence and claim validation belong to the runtime/reviewer.
export function LiteralExcerpt({ text, value }: { text: string; value: string }) {
  if (!value) return <>{text}</>;
  const parts = text.split(value);
  return <>{parts.map((part, index) => <span key={index}>
    {index > 0 ? <mark>{value}</mark> : null}{part}
  </span>)}</>;
}
