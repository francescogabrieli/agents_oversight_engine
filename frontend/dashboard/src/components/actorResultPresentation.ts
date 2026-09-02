// The actor's own words are the missing half of a review: the reason code names the
// policy that fired, this names what the agent was doing when it fired. Labels mirror
// _ACTOR_RESULT_LABELS in layer4 observability/governance_narrative.py so the dashboard
// and the exported narrative read the same way.

const ACTOR_RESULT_LABELS: Record<string, string> = {
  propose_action: "L'agente ha proposto un'azione",
  intermediate_output: "L'agente ha prodotto un risultato intermedio",
  step_completed: "L'agente ha dichiarato il passo completato",
  ask_clarification: "L'agente ha chiesto un chiarimento",
  blocked: "L'agente si è dichiarato bloccato",
  needs_replan: "L'agente ha chiesto di ripianificare",
};

export function actorResultLabel(resultType: string | null | undefined): string | null {
  if (!resultType) return null;
  return ACTOR_RESULT_LABELS[resultType] ?? `L'agente ha risposto: ${resultType}`;
}

// The blocking reason is a free-form field the actor fills in; some runs put a
// placeholder token there instead of a real question, which is itself worth showing
// but not worth labelling as the agent's motivation.
export function isPlaceholderBlockingReason(reason: string | null | undefined): boolean {
  if (!reason) return true;
  return /^\s*(placeholder|n\/?a|none|nessun[ao]?)/i.test(reason);
}
