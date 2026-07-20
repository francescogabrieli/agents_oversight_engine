# Governed AI Agent Architecture PoC

Thesis-oriented proof of concept for controlled task intake, LangGraph runtime
orchestration, OPA-owned governance decisions, human review, trace observability,
and offline evaluation.

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
