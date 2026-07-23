ActorResult
    │
    ▼
ExternalOperationProposal normalizzata dal runtime
    │
    ▼
OPA preflight
    │
    ├── static hard violation ────────────────→ STOP
    │
    ├── output non ancora execution-ready ───→ recovery locale
    │
    └── governed external candidate
                 │
                 ▼
        semantic judge obbligatorio
                 │
                 ▼
       evidence + trajectory attivi
       risk + behavioral shadow
                 │
                 ▼
            OPA finale
                 │
       ┌─────────┼───────────┬──────────┐
       ▼         ▼           ▼          ▼
     allow   revise_action   HITL       stop