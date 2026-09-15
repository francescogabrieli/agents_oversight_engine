# AGENTS.md

## Purpose

This repository contains a Master's thesis proof of concept whose central
architectural contribution is an **auditable Human Oversight and Human-in-the-Loop
control engine for autonomous AI agents**.

The autonomous planner/actor/tool runtime is essential: it is the real system being
supervised and the experimental substrate that makes oversight meaningful. The thesis
contribution is not the agent alone, but the control architecture that observes its
execution, evaluates bounded signals, applies policy, escalates at justified
boundaries, binds human decisions to exact candidates, and preserves replayable
accountability.

The project is not a generic chatbot. Its priorities, in contribution order, are:

- auditable oversight and policy enforcement;
- reason-scoped, typed Human-in-the-Loop intervention;
- uncertainty-aware escalation and bounded external-action governance;
- evidence, trajectory, semantic assessment, traceability, and replay;
- evaluation through human-intervention, feedback, and uncertainty metrics;
- an autonomous plan-and-execute agent runtime as the supervised substrate;
- controlled task intake and external-capability execution.

The current design references are:

- `docs/architecture/architecturev3.md`
- `training_plan.md`


## Required reference reading

This file is the entry point for repository instructions. The three documents below
are extensions of this operational contract, not optional background. Read the
applicable document completely before changing the corresponding area; cross-layer
work requires every applicable reference. Paths in these documents are relative to
the repository root unless written as Markdown links.

| Task | Required reference |
| --- | --- |
| Change implementation, ownership, dependencies, contracts, or architecture documentation | [Implementation map](docs/agent-reference/implementation-map.md): active baseline, canonical owners, compatibility surfaces, trace schema and dashboard contracts |
| Change intake, planner/actor, tools, governance, evidence, judge, HITL, persistence, or runtime observability | [Runtime contracts](docs/agent-reference/runtime-contracts.md): detailed invariants, reason-scoped authorization, recovery rules and regression rationale |
| Run/debug the application or campaigns, change providers/environment, checkpoints, deployment or execution commands | [Operations](docs/agent-reference/operations.md): entrypoints, environment, thinking configuration, debugging and commands |

Read the design references named above when evaluating architectural changes.
For offline evaluation, also read
[src/layer5_evaluation_continuous_improvement/README.md](src/layer5_evaluation_continuous_improvement/README.md).
For the planner/executor pattern, use
[plan-and-execute.ipynb](docs/guidelines/plan-and-execute.ipynb).

Maintain each detailed rule in its owning reference. Update this entry point when
shared rules, reference routing, or the essential boundaries below change. The
Change Policy applies to this whole instruction set; a detail-only update belongs
in its reference rather than being copied back into this file.

## Essential boundaries

- Layer 1 owns intake outside LangGraph. Layer 2 owns orchestration and enforcement;
  Layer 3 owns tools/connectors; Layer 4 owns evidence, state persistence and traces.
  Layer 5 consumes exported traces offline; Continuous Improvement remains deferred.
- OPA/Rego alone owns final governance directives, scoring and thresholds.
  Python produces typed facts, validates directives and enforces them. Evidence and
  trajectory are active; operational risk and behavioral scoring remain shadow-only.
- Keep a credible semantic planner and step-bounded actor. Bind tools through the
  runtime gateway. Actors and governance never call connectors or MCP directly.
- HITL uses typed LangGraph interrupt/resume. Only a reason-scoped capability
  approval bound to the exact candidate/digest can authorize external execution.
  Edits invalidate approval. Reconciliation never re-authorizes an external effect.
- The CLI owns runtime execution/resume. The dashboard validates and enqueues human
  decisions; its inbox and artifact snapshots cannot reconstruct runtime state.
- Preserve auditable evidence, immutable decisions, digest bindings and durable
  checkpoints. Do not expose hidden chain-of-thought as audit evidence.
- Preserve the closed `layer2_trace.v6` contract. Post-release additions stay optional;
  missing historical data stays unknown. Incompatible changes require a version
  bump together with the offline importer.
- Before official runs, verify the loaded OPA module sources and policy data against
  disk using `scripts/verify_opa_bundle.py`; a version string does not prove reload.
- Evaluation must retain failed, interrupted, skipped and not-started runs. Undefined
  rates remain null/N/D. Plots consume canonical Layer 5 metrics without recomputing
  rates or inventing sample data.

## Coding Standards

### General

- Prefer clear, explicit code over clever code.
- Keep architecture-aligned naming.
- Avoid hidden behavior and implicit coupling.
- Preserve separation of concerns between:
  - state models
  - runtime logic
  - graph wiring
  - infrastructure concerns

### Maintainability and Modularity

- Organize the implementation by bounded layer: Layer 2 owns domain/runtime/planning/execution/governance/HITL orchestration; Layer 3 owns Tool Bus and external capabilities; Layer 4 owns state, evidence, persistence, and observability.
- Keep one composition root responsible for selecting concrete adapters and wiring dependencies. Domain and application modules must not construct infrastructure clients directly.
- Dependencies must point inward: domain models know no frameworks; application services depend on domain contracts and ports; adapters depend on ports; LangGraph wiring composes them.
- Keep LangGraph nodes thin. A node may adapt graph state, call one application service, and record the result, but business rules belong in domain or application modules.
- Define ports beside the capability that consumes them and implement adapters in an `adapters` or `infrastructure` package.
- Prefer feature-local modules over broad utility modules. Do not create generic `utils.py`, `helpers.py`, or `common.py` dumping grounds.
- Split files when they combine more than one independently testable responsibility, require unrelated dependencies, or force readers to cross provider, parsing, policy, and domain concerns in one module.
- Treat roughly 400-500 lines as a review signal, not an automatic failure. A larger file needs one cohesive responsibility and an explicit reason to remain whole.
- Keep package depth shallow. Use at most two meaningful levels inside a capability unless an adapter family genuinely requires another level.
- Public package APIs must be explicit through small `__init__.py` files. Never import the entire runtime graph merely to expose a domain type.
- Preserve backward-compatible facades during incremental refactors, mark them as compatibility surfaces, and remove them only after callers and tests migrate.
- Avoid circular imports through contract placement and dependency direction, not through scattered runtime imports or `Any` casts.
- Configuration loading, environment access, network I/O, subprocesses, and database access are infrastructure concerns and must stay outside pure domain modules.
- External adapters must expose bounded timeouts, structured failures, and deterministic normalization into typed domain results.
- Shared mutable singletons are forbidden. Runtime services must be safe to construct per worker and explicit about stateful dependencies.
- Every new capability package must include a short README or package-level documentation covering its responsibility, public entry point, dependencies, and extension procedure.
- Mirror production package boundaries in tests so each capability can be tested without booting the full LangGraph runtime.
- Add architecture tests for important forbidden dependencies. Directory structure alone is not considered an enforced boundary.
- Refactor by vertical slice: add characterization tests, introduce the new module, keep a compatibility facade, migrate callers, then remove dead code.
- Do not extract a network microservice solely to reduce file size. Require an independent lifecycle, scaling profile, security boundary, ownership boundary, or failure policy.

### Canonical Ownership and Reuse

- Search the repository before adding a model, parser, validator, normalizer, serializer, factory, configuration loader, prompt projection, or test fixture. Extend the canonical implementation when the responsibility already exists.
- Every invariant must have one authoritative owner. Other modules call or import that owner; they must not reproduce the same rule with locally renamed helpers.
- Repeated validation, normalization, serialization, dependency construction, environment parsing, response-envelope construction, or state-transition logic is actionable duplication and must be consolidated in the feature that owns the invariant.
- Keep distinct code only when the semantics, trust boundary, or failure policy is genuinely different. For example, strict live-request validation and permissive checkpoint recovery may remain separate, but the reason must be documented and covered by tests.
- Do not create generic abstractions merely because two short blocks look similar. Extract around a stable domain concept, shared invariant, or repeated change axis; incidental syntactic similarity is not sufficient.
- Prefer a small typed function or service with explicit inputs over inheritance, global registries, implicit hooks, or copy-and-edit variants.
- Configuration must have one canonical source of truth. Variants should be bounded overlays or generated artifacts, not complete copied registries that can drift independently.
- Production definitions must not be duplicated in Python defaults and configuration files. Fail clearly when the canonical configuration is missing or invalid.
- Test modules must reuse shared builders, stubs, fixtures, and factories from feature-aligned test support modules. Do not copy a helper into a second test file.
- Frontend presentation mappings, labels, state classes, and icons must be centralized when more than one component renders the same domain state.

### Dead Code and API Lifecycle

- Do not add speculative code for possible future use. Every new symbol, field, branch, configuration key, export, and dependency must have a current caller and a test or a documented external contract.
- Remove superseded implementations in the same vertical slice after all callers migrate. Do not leave old private helpers, fallback branches, aliases, configuration fields, or commented-out code behind.
- A public export is not evidence that code is alive. Exported symbols must be referenced by the repository or explicitly documented and tested as compatibility surfaces for external consumers.
- Compatibility facades must be narrow, clearly identified as compatibility surfaces, and protected by a test that explains why the legacy import path is still required. Remove the facade once the documented migration condition is satisfied.
- When removing a capability, update its code, tests, exports, configuration, examples, READMEs, architecture documentation, and debug scripts together so documentation cannot keep dead concepts alive.
- Required protocol and framework callback parameters that are intentionally unused should use underscore-prefixed names when the signature permits it. If an exact keyword name is required, use a narrow `# noqa` and make the protocol reason evident from the surrounding contract.
- Do not silence dead-code or lint findings broadly. Ignore markers must be local to the intentional compatibility or protocol seam and must never hide an entire file or category.
- Delete unused dependencies and imports immediately. A dependency is justified only by an active production path, development gate, or test.

### Mandatory Change Workflow

- Before editing, locate the canonical owner and all callers with repository search. Check production code, tests, configuration, frontend code, documentation, and package exports.
- Add or identify characterization tests before consolidating behavior whose semantics are not already explicit.
- Implement the new canonical surface, migrate every caller, then remove the displaced implementation and stale exports in the same change.
- Re-run repository search for the removed symbol, copied block, retired path, and old configuration key. Zero stale references are required unless a documented compatibility test intentionally retains one.
- Review the final diff for new near-duplicates, pass-through wrappers, unused fields, unreachable branches, copied test setup, and documentation drift. A green test suite alone is not sufficient evidence of maintainability.
- Keep changes cohesive. If cleanup reveals a separate architectural problem, report or isolate it instead of mixing unrelated redesign into the same refactor.
- Preserve intentional compatibility, checkpoint recovery, and adapter protocol seams, but identify them explicitly in the handoff so they are not mistaken for accidental duplication or dead code later.

### Required Quality Gates

- Every code-changing task must finish with `ruff check src tests`.
- Every code-changing task must run `vulture src tests --min-confidence 80`; investigate every result and remove it or document the narrow protocol/compatibility reason for retaining it.
- Run `pytest -q tests` for repository-wide refactors. For a genuinely isolated change, focused tests are acceptable during iteration, but the final handoff must state exactly what was and was not run.
- When `frontend/dashboard` changes, run both `npm test -- --run` and `npm run build` from that directory.
- Run `git diff --check` and resolve whitespace errors. Platform line-ending advisories alone are not failures.
- Run architecture tests whenever dependency direction, composition, public facades, package ownership, or layer boundaries change.
- Do not claim that cleanup is complete while a high-confidence dead-code finding, exact duplicate, stale reference, or unexplained compatibility shim remains.
- The final handoff must list the canonical surfaces introduced, dead surfaces removed, intentional repetitions retained, and the exact validation results.

### Comments

- Prefer short `#` comments over docstrings for internal implementation explanation.
- Keep comments focused on why the code exists or what contract it enforces.
- Avoid low-value comments that restate the code mechanically.
- Prefer one clear explanatory comment over duplicated comment-plus-docstring blocks when the code is internal.
- Every new code addition must include short `#` comments above each new `def` and on non-obvious lines or blocks where readability would otherwise suffer.

### Typing

- Prefer strong typed models over anonymous dictionaries where contracts matter.
- Use enums instead of free-form strings for stable runtime markers and decision types.
- Use `dict[str, Any]` only as an extension field, not as the primary domain contract.

### Dataclasses and Models

- Layer 2 runtime state uses dataclasses.
- Layer 1 canonical payloads use Pydantic models.
- Preserve this split unless there is a strong architectural reason to change it.


## Change Policy

When making changes:

1. preserve alignment with `docs/architecture/architecturev3.md`
2. preserve alignment with `training_plan.md`
3. prefer evolving existing contracts over adding parallel ad-hoc ones
4. keep runtime behavior explainable
5. update tests when contracts change
6. keep the implementation compact, but not ambiguous
7. update `AGENTS.md` whenever the repository behavior, architecture, contracts, commands, workflows, or conventions materially change

When a change introduces an important architectural step, debugging aid, workflow clarification, or visual explanation that could be useful later in the thesis, it is acceptable and encouraged to produce supporting documentation material inside `docs/`.

This can include:

- markdown notes
- diagrams
- HTML visualizations
- HTML slide decks
- interactive HTML documents or prototypes
- screenshots or other visual references
- other thesis-useful documentation artifacts

This does **not** mean producing documentation for every minor change. The threshold should be pragmatic:

- create documentation when it materially improves explainability, traceability, or thesis reuse
- do not create documentation for routine edits that add little long-term value

Avoid:

- introducing broad untyped dictionaries where stable models already exist
- mixing graph wiring and business logic
- silently changing runtime contracts without updating tests

`AGENTS.md` is not optional documentation. It must be treated as a living operational contract for the repository. If a change makes any section inaccurate, that file must be updated in the same change set.


## Recommended Next Steps

The current most relevant near-term evolution path is:

1. keep Layer 1 stable and explicit
2. keep the Layer 1 -> Layer 2 bootstrap-planner-actor-tool-replanner contract stable
3. harden the governance contract and policy bundle boundaries before adding more policy dimensions
4. calibrate active evidence/trajectory and shadow operational consequence facts against exported traces
5. harden the standalone HITL dashboard and richer operator workflows on top of the existing `interrupt / resume` path
6. calibrate governed-external judge findings and revision-loop thresholds against real traces before widening the tool surface
7. strengthen observability and Evidence Integrity & Provenance before widening the tool surface
