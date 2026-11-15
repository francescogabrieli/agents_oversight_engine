# Auditable Human Oversight for Autonomous AI Agents

Thesis-oriented proof of concept for controlled task intake, LangGraph runtime
orchestration, OPA-owned governance decisions, human review, trace observability,
and offline evaluation.

The central contribution is an auditable oversight and Human-in-the-Loop control
engine around an autonomous planner/actor runtime. OPA/Rego owns governance
directives; typed human decisions are bound to exact execution candidates, and
persisted evidence and traces support accountability and offline evaluation.

## Repository guide

| Location | Contents |
| --- | --- |
| [`src/`](src/) | Five architectural layers and CLI entry points |
| [`configs/`](configs/) | Policy bundles, tool registries and evaluation configuration |
| [`frontend/dashboard/`](frontend/dashboard/) | Human-review dashboard |
| [`docs/`](docs/README.md) | Architecture, contracts, operations and research references |
| [`figures/`](figures/README.md) | Selected thesis figures and editable sources |
| [`evaluation_corpus/`](evaluation_corpus/README.md) | Controlled evaluation material |
| [`notebooks/`](notebooks/README.md) | Offline analysis and thesis plotting |
| [`tests/`](tests/) | Runtime, policy, contract and architecture checks |

Start with the [architecture](docs/architecture/architecturev3.md),
[operations guide](docs/agent-reference/operations.md), and
[repository instructions](AGENTS.md).
The [publication review](docs/repository-publication-review.md) records the scope
and remaining privacy considerations of the repository cleanup.

The canonical task pool and execution protocol for the thesis evaluation are in
[evaluation_task_pool.md](evaluation_task_pool.md).

## Supported Python

The temporarily supported interpreter range is **Python >=3.12,<3.14**. Python
**3.13** is the recommended development version and is selected by
`.python-version` for compatible version managers. Python 3.14 is currently
unsupported because the active LangChain/Pydantic V1 compatibility path emits a
runtime warning on 3.14.

Create a Python 3.13 virtual environment, then install production dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-production.txt
```

For tests and schema validation, install the development dependencies instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

The current checkout does not introduce packaging metadata solely to declare the
version range. See `AGENTS.md` for architecture, runtime, and test guidance.
