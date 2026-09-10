// Link only explicit web locators; opaque evidence identifiers remain inert text.
export function SourceReference({ locator }: { locator: string }) {
  let href: string | null = null;
  try {
    const url = new URL(locator);
    if (url.protocol === "http:" || url.protocol === "https:") href = url.href;
  } catch { /* Non-web resource identifiers are not navigable URLs. */ }
  return href
    ? <a className="review-evidence-locator" href={href} target="_blank" rel="noopener noreferrer">{locator} ↗</a>
    : <span className="review-evidence-locator">{locator}</span>;
}
