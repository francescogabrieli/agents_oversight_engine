# Contratti JSON definitivi del runtime governato

1. Il flusso governato, finalmente snello
2. Le regole architetturali rimaste
3. Caso guida: ricerca e invio di una vera email
4. Ingresso grezzo → `IntakeResult`
5. Dentro `TaskSpec`: obiettivo e intento
6. `IntakeResult` → input del Planner
7. Output del Planner: piano puramente semantico
8. Planner → Actor: `ActorTask`
9. Output dell’Actor: `ActorResult`
10. Actor → Tool Gateway: `ToolRequest`
11. Tool Gateway → runtime: `ToolExecutionResult`
12. Dal risultato a `ToolObservation`
13. Osservazioni minime, interpretazione contestuale
14. Completion dello step: `StepValidation`
15. Dallo stato ai pilastri: fatti, non giudizi duplicati
16. Tutto entra in OPA
17. La proposta di invio: `ExternalOperationProposal`
18. LLM-as-Judge: `JudgeContext`
19. LLM-as-Judge: `JudgeAssessment`
20. HITL sul digest corrente
21. Resume ed esecuzione esterna
22. Trace v5: una proiezione osservabile
23. Cosa è stato rimosso e cosa è rimasto
24. Matrice definitiva dei contratti
