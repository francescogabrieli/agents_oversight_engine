# Evidence e Trajectory pillar — v1 (storica)

> **Stato del documento:** riferimento storico. Per il contratto corrente fare riferimento a [`evidence_trajectory_pillars_it_v2.md`](evidence_trajectory_pillars_it_v2.md). La matrice di attivazione e le regole operative qui riportate non descrivono più interamente il runtime attuale.

## Idea generale

I pillar **Evidence** e **Trajectory** forniscono a OPA fatti osservabili sulla run:

- **Evidence** controlla che le evidenze selezionate dall'Actor esistano, siano integre, tracciabili e legate esattamente al risultato proposto.
- **Trajectory** controlla che l'agente stia avanzando e non stia ripetendo replan o proposte esterne senza una nuova base osservabile.

I pillar non prendono autonomamente la decisione finale e non calcolano uno score in Python. Producono fatti e risultati diagnostici; **OPA è l'unica autorità che restituisce la direttiva finale**.

## Quando si attivano

| Evento dell'Actor | Evidence | Trajectory |
|---|---:|---:|
| `INTERMEDIATE_OUTPUT` | No | No |
| `PROPOSE_ACTION` read-only | No | No |
| `NEEDS_REPLAN` | No | Sì |
| `STEP_COMPLETED` locale | Sì | Sì |
| Candidato a operazione esterna | Sì | Sì |

## Evidence pillar

### Come viene registrata un'evidenza

Quando un tool restituisce un risultato, il runtime crea un'osservazione concreta:

```python
ToolObservation(
    observation_id="obs_17",
    content="Il documento indica che il termine è il 30 settembre.",
    locator="https://example.org/documento",
)
```

L'osservazione viene conservata nel `TaskState` insieme alla sua provenienza: tool call e plan step che l'hanno generata.

Quando completa uno step, l'Actor indica quali osservazioni ha utilizzato:

```python
StepValidation(
    actor_self_confidence=0.9,
    evidence_refs=("obs_17",),
    rationale_summary="La data è riportata in obs_17.",
)
```

### Cosa controlla

Il pillar verifica che:

1. ogni `evidence_ref` esista in `TaskState.observations`;
2. riferimenti e ID delle osservazioni siano univoci;
3. le osservazioni selezionate contengano testo concreto;
4. la provenienza sia ricostruibile fino a una tool call e a uno step conosciuto;
5. le evidenze non siano tutte semplici titoli di risultati di ricerca;
6. snapshot delle evidenze, selezione e operazione esterna coincidano con quelli associati al candidato al momento della sua creazione.

L'ultimo controllo usa digest crittografici. Se il payload o le evidenze vengono modificati dopo la creazione del candidato, i digest non coincidono e il binding risulta invalido.

### Esempio

Il runtime contiene:

```text
obs_17 -> "Il termine è il 30 settembre."
obs_18 -> "La richiesta deve essere firmata digitalmente."
```

Se l'Actor restituisce:

```text
evidence_refs = ["obs_17", "obs_18"]
```

entrambi i riferimenti esistono e possono essere ricondotti alla loro origine. Il controllo può produrre:

```text
selected_evidence_refs_valid = true
selected_observations_have_content = true
selected_observations_provenance_resolvable = true
evidence_gap = false
```

Se invece l'Actor usa `obs_99`, mai registrata dal runtime:

```text
selected_evidence_refs_valid = false
evidence_gap = true
```

OPA restituisce normalmente:

```text
REQUEST_MORE_EVIDENCE
```

Se il pillar è richiesto ma non disponibile, il comportamento è conservativo:

```text
WAIT_HUMAN_REVIEW
```

Una selezione vuota non è automaticamente invalida. Il pillar verifica l'integrità dei riferimenti scelti; non impone che ogni risultato utilizzi una fonte esterna.

### Limite del controllo

Evidence verifica **esistenza, provenienza e binding**, non il significato delle affermazioni.

Se `obs_17` dice «aumento del 3%» e l'Actor scrive «aumento del 30%», il riferimento può essere strutturalmente valido. Per i candidati esterni, la compatibilità semantica è valutata dal **Judge**.

## Trajectory pillar

Trajectory osserva il percorso complessivo della run, non soltanto l'ultimo output. Raccoglie fatti come:

- turni dell'Actor, step completati e osservazioni raccolte;
- tool call riuscite e fallite;
- numero di replan;
- presenza di una motivazione per il replan;
- nuovi risultati, osservazioni, step completati o interventi umani dall'ultimo replan;
- revisioni della stessa operazione esterna;
- ripresentazione dello stesso candidato o di un payload già respinto.

### Esempio di replan

Al primo blocco, l'Actor restituisce:

```text
NEEDS_REPLAN
reason = "La fonte richiesta non è disponibile."
```

È il primo replan ed esiste una motivazione. Trajectory produce:

```text
replan_request_admissible = true
```

OPA restituisce `REPLAN`.

Se, subito dopo, l'Actor richiede nuovamente un replan senza nuove tool call, osservazioni, progressi o decisioni umane:

```text
observable_change_since_last_replan = false
replan_request_stalled = true
```

OPA restituisce `WAIT_HUMAN_REVIEW`, perché ulteriori replan automatici non hanno una nuova base osservabile.

### Esempio di proposta esterna

L'Actor propone l'invio di un'email. Il Judge richiede una revisione e il runtime registra il tentativo. Se l'Actor ripresenta lo stesso payload e la stessa selezione di evidenze senza modifiche sostanziali, Trajectory rileva:

```text
unchanged_resubmission = true
```

OPA restituisce `WAIT_HUMAN_REVIEW`. Anche il superamento del limite configurato di revisioni porta alla revisione umana quando il Judge continua a richiedere modifiche.

## Sintesi del flusso

```text
Actor raggiunge un confine semantico o operativo
                    |
        +-----------+-----------+
        |                       |
   Evidence                  Trajectory
 integrità delle prove     salute del percorso
        |                       |
        +-----------+-----------+
                    |
                   OPA
                    |
 CONTINUE / REQUEST_MORE_EVIDENCE / REPLAN / WAIT_HUMAN_REVIEW
```

In breve: **Evidence protegge il legame tra risultato e prove; Trajectory protegge il processo da ripetizioni e stalli; OPA decide come il runtime deve proseguire.**
