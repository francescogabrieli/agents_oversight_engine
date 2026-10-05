import type { DashboardToolAttempt } from "../types";

const toolLabels: Record<string, string> = {
  web_search: "Ricerca web",
  fetch_url_text: "Lettura pagina web",
  controlled_document_read: "Lettura documento",
  controlled_dataset_read: "Lettura dataset",
};

// Show the current step's persisted tool trail without exposing tool arguments.
export function ToolAttemptTimeline({ attempts }: { attempts: DashboardToolAttempt[] }) {
  if (attempts.length === 0) return null;
  return (
    <article className="recent-tool-history" aria-label="Tentativi degli strumenti nel passo attuale">
      <p className="review-diagnostic-title">Tentativi degli strumenti in questo passo</p>
      <ol className="tool-history-list">
        {attempts.map((attempt) => (
          <li className="tool-history-entry" key={attempt.request_id}>
            <div className="tool-history-heading">
              <strong>{toolLabels[attempt.tool_name] ?? attempt.tool_name}</strong>
              <span className={`badge ${attempt.success ? "badge-success" : "badge-warning"}`}>
                {attempt.success ? "completato" : "senza esito"}
              </span>
              <span className="tool-history-reach">
                {attempt.reached_backend === true
                  ? "servizio contattato"
                  : attempt.reached_backend === false
                    ? "gestito localmente"
                    : "esecuzione non determinabile"}
              </span>
            </div>
            <p>{attempt.summary}</p>
            {attempt.request_reason ? <p className="tool-history-reason">Richiesta: {attempt.request_reason}</p> : null}
            {attempt.error_message ? <p className="tool-history-error">{attempt.error_message}</p> : null}
            {attempt.new_observation_count > 0 ? (
              <small>Nuovi riscontri registrati: {attempt.new_observation_count}</small>
            ) : null}
          </li>
        ))}
      </ol>
    </article>
  );
}
