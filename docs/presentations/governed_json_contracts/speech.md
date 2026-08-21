## Slide 1: Il flusso governato, finalmente snello

Questa presentazione definisce il contratto end-to-end del runtime dopo lo snellimento. L’obiettivo non è soltanto mostrare un diagramma: vogliamo sapere esattamente quale oggetto passa da un componente al successivo, chi lo produce e chi ha autorità per decidere.

Il principio guida è semplice: meno metadati interpretativi, più fatti concreti. Il controllo resta forte perché OPA, HITL, digest e tracciamento rimangono espliciti.

## Slide 2: Le regole architetturali rimaste

Il planner descrive cosa va fatto, senza scegliere tool o livelli di evidenza. L’actor lavora sul passo corrente, mentre il Tool Gateway rimane l’unico punto di accesso alle capability.

Il Judge valuta la semantica di una proposta esterna, ma non decide la traiettoria. OPA resta l’unica autorità della direttiva e l’approvazione umana è sempre legata al digest esatto dell’operazione.

## Slide 3: Caso guida: ricerca e invio di una vera email

Useremo una task concreta: cercare informazioni sugli ultimi modelli AI, produrre una sintesi con fonti e inviarla a un destinatario reale.

La ricerca è read-only. L’invio, invece, attraversa proposta immutabile, Judge, OPA e HITL. Solo dopo l’approvazione del digest corrente l’executor può inviare.

## Slide 4: Ingresso grezzo → IntakeResult

Layer 1 trasforma il testo libero in `IntakeResult`. Il contratto separa il contenuto canonico della task dai metadati di parsing e tracciamento.

Questo è il primo confine stabile: Layer 2 non deve reinterpretare la richiesta originale, ma partire da una forma già validata.

## Slide 5: Dentro TaskSpec: obiettivo e intento

`TaskSpec` descrive obiettivo, contesto, vincoli, sensibilità e criteri di successo. `execution_intent` distingue la sola proposta dall’intenzione di eseguire dopo governance, senza concedere autorità.

L’effetto concreto non viene duplicato in un secondo JSON: emerge dal piano e dalla proposta tipizzata prodotta dall’actor. La policy rimane controllata dal sistema.

## Slide 6: IntakeResult → input del Planner

Il planner riceve soltanto ciò che gli serve per costruire una traiettoria semantica: task, policy, qualità dell’intake e stato runtime.

Non riceve il compito di scegliere la decisione di governance e non deve produrre payload eseguibili. Questa separazione evita che il piano diventi un secondo policy engine.

## Slide 7: Output del Planner: piano puramente semantico

Ogni `PlanStep` contiene identificatore, obiettivo, razionale, risultato atteso e stato. La sequenza per il nostro esempio è ricerca, sintesi e preparazione della proposta email.

Non compaiono rank, precision ceiling, tool call o destinatari eseguibili. Il planner descrive la traiettoria, mentre gli altri componenti applicano i propri contratti.

## Slide 8: Planner → Actor: ActorTask

`ActorTask` è il contesto operativo del singolo turno. Contiene task autorizzata, piano completo, passo corrente, osservazioni, cronologia, scratchpad audit-safe, validazioni, contatori e feedback di governance.

Il piano completo permette reasoning globale; l’actor può però committare soltanto il passo corrente. Ogni gruppo risponde a una domanda precisa senza duplicare il planner.

## Slide 9: Output dell’Actor: ActorResult

`ActorResult` usa un envelope comune e payload condizionali. `result_type` indica se l’actor richiede un tool, produce un risultato locale, completa il passo, chiede chiarimenti o richiede replanning.

Tool request, step validation e proposta esterna sono oggetti tipizzati. Questo mantiene l’output compatto e verificabile.

## Slide 10: Actor → Tool Gateway: ToolRequest

Una `ToolRequest` identifica il passo, la capability, la ragione e soltanto i parametri pertinenti. Nel caso di `web_search` useremo query, limite ed obiettivo estrattivo.

L’actor non chiama direttamente HTTP o MCP. Il Gateway applica registro, allowlist, read-only e normalizzazione.

## Slide 11: Tool Gateway → runtime: ToolExecutionResult

Il risultato conserva richiesta, successo, sintesi, contenuto, fonti e diagnostica. Troncamento e limitata estraibilità sono fatti espliciti, non fallimenti opachi.

Un `403` o un byte limit non deve causare un loop terminale: il runtime può usare una sintesi citata, provare una fonte alternativa o chiedere supporto umano.

## Slide 12: Dal risultato a ToolObservation

Ogni adapter restituisce `ToolObservation` minimali dal risultato reale della propria azione: identificatore, contenuto e locator opzionale.

Il runtime valida e registra queste osservazioni senza attribuire qualità, autorità o significati dedotti da parole chiave. La risposta aggregata non viene clonata su tutti i link.

## Slide 13: Osservazioni minime, interpretazione contestuale

Goal, contesto, vincoli e criteri di successo restano istruzioni. Le osservazioni runtime nascono soltanto da azioni concrete.

I riferimenti selezionati dall’actor sono opzionali ma devono esistere. OPA ne controlla l’integrità; per una proposta esterna il Judge interpreta semanticamente contenuto, contesto e digest.

## Slide 14: Completion dello step: StepValidation

La confidence dell’actor è rimasta. Serve come autovalutazione, insieme ai riferimenti alle evidenze, al razionale e all’incertezza residua.

Non è però una direttiva. Una confidence bassa, da sola, non forza recovery; OPA la considera nel contesto di evidenza e progresso.

## Slide 15: Dallo stato ai pilastri: fatti, non giudizi duplicati

Il pillar evidence espone fatti su riferimenti, contenuto, fonti ed estraibilità. Il pillar trajectory osserva progresso, nuovi tool call, avanzamento del piano, digest e failure ripetuti.

Risk e behavioral rimangono osservabili ma shadow-only. Non competono con OPA per decidere la traiettoria.

## Slide 16: Tutto entra in OPA

Il runtime costruisce un input versionato con fatti di base, requisiti dei pilastri, risultati e indisponibilità. OPA applica precedenze e invarianti e restituisce un output altrettanto tipizzato.

Python normalizza e applica; il Judge valuta semanticamente; OPA decide. Per l’email reale, la direttiva porta alla revisione umana del digest corrente.

## Slide 17: La proposta di invio: ExternalOperationProposal

Prima del Judge e di OPA, la bozza diventa una proposta normalizzata. Identità, target, payload, effetto, provenienza e capability profile sono espliciti.

Il digest SHA-256 lega tutte le valutazioni a quel contenuto preciso. Modificare destinatario, oggetto o corpo significa creare un nuovo digest.

## Slide 18: LLM-as-Judge: JudgeContext

Il Judge riceve un contesto semantico limitato: obiettivo autorizzato, vincoli, passo, proposta esatta, evidenze selezionate e fatti sulla destinazione.

Non riceve il compito di combinare policy, risk o behavioral. Questo impedisce al Judge di diventare un’autorità parallela.

## Slide 19: LLM-as-Judge: JudgeAssessment

Il Judge restituisce verdict, severità, confidence e finding tipizzati. L’assessment dichiara anche quale digest è stato valutato.

Un verdict `safe` significa che non sono emersi problemi semantici rilevanti; non equivale all’autorizzazione dell’invio. La direttiva resta di OPA.

## Slide 20: HITL sul digest corrente

La richiesta di revisione contiene la proposta, il digest, le motivazioni OPA e i finding del Judge. La decisione umana può approvare, modificare, chiedere evidenza, ripianificare o abortire.

Se il revisore modifica la bozza nasce un nuovo digest. Judge, governance e approvazioni precedenti vengono invalidati e il percorso riparte sul nuovo contenuto.

## Slide 21: Resume ed esecuzione esterna

L’executor riceve digest corrente e digest autorizzato. L’operazione parte soltanto se coincidono e se l’intento consente l’esecuzione.

L’idempotency key e il registro durevole garantiscono at-most-once. La receipt conserva esito, riferimento esterno, connector e diagnostica.

## Slide 22: Trace v6: una proiezione osservabile

Il trace v6 ricompone intake, piano, actor, tool, evidenze, governance, HITL e operazione esterna in una proiezione read-only.

Serve per audit, debugging ed evaluation, ma non influenza il runtime. Include anche `termination_reason` quando la run termina; i trace legacy vengono normalizzati soltanto al confine dell’evaluation service.

## Slide 23: Cosa è stato rimosso e cosa è rimasto

Sono stati rimossi ranking universali, precision ceiling, support rank e autorità Python duplicate. Anche `StepValidation.next_action` non controlla più il routing.

Restano gli elementi che producono controllo reale: confidence dell’actor, evidence refs, attributi concreti, anti-loop di traiettoria, OPA, Judge semantico e HITL digest-bound.

## Slide 24: Matrice definitiva dei contratti

La matrice finale risponde a quattro domande per ogni confine: quale contratto, chi lo produce, chi lo consuma e chi decide.

La conclusione è volutamente semplice: contratti piccoli, fatti concreti, una sola decisione. È questa separazione a rendere la run contemporaneamente controllata e snella.
