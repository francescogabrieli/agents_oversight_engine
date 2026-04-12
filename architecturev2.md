# ARCHITECTURE

## 1. TASK & CONTEXT INTAKE

### 1.1 Input
Richiesta utente / task generico da svolgere.

### 1.2 Operazioni Interne
LLM leggero oppure parser che trasforma il task generico in un JSON strutturato.

Per il PoC conviene separare:
- `task_spec`: descrizione del task
- `execution_policy`: parametri di esecuzione decisi dal sistema / supervisore

Dal task si estraggono goal, contesto, vincoli e alcune informazioni utili al controllo.
Soglia di confidenza e budget non vengono inferiti dal task, ma impostati come configurazione di esecuzione.

#### Esempio
```json
{
  "structured_task": {
    "task_spec": {
      "goal": "...",
      "context": "...",
      "constraints": [],
      "domain_type": "research",
      "domain_label": "generic_research",
      "sensitivity": "medium",
      "success_criteria": [
        "use at least 2 relevant sources",
        "produce a concise final answer"
      ]
    },
    "execution_policy": {
      "confidence_threshold": 0.65,
      "budget": {
        "max_steps": 5
      }
    }
  },
  "metadata": {
    "parse_status": "complete",
    "warnings": [],
    "missing_information": []
  }
}
```

### 1.3 Output
Output canonico del layer 1: task JSON strutturato, con separazione tra specifica del task e policy di esecuzione, più metadata di tracciabilità e qualità del parsing.

### 1.4 Framework / Librerie
Prompt + LLM API, parser Python, Pydantic.

## 2. AGENT RUNTIME / ORCHESTRATOR -> Implementazione con **Langgraph** (grafo con call_llm e tool_call in loop). Valutare eventualmente se integrare i moduli successivi (e quali) in nodi

### 2.1 Input
JSON strutturato del task.

### 2.2 Operazioni Interne
Avvio grafo Langgraph per esecuzione task, sfruttando lo state e l'esecuzione tracciabile.

