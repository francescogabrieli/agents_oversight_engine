# Repository Documentation Index

This folder contains the current documentation, historical design references, research foundations, workflow details, presentations, and guidelines for the **Auditable Human Oversight & Human-in-the-Loop Engine for Autonomous AI Agents**.

---

## Folder Structure

```text
docs/
├── README.md                          <- Index and sitemap of repository documentation
├── architecture/                      <- System Architecture & Concrete E2E Walkthroughs
│   ├── architecturev3.md              <- [Canonical] Primary architecture specification (v3)
│   ├── e2e_real_task_walkthrough_it.md<- Detailed Italian step-by-step E2E execution trace (Email task)
│   ├── architecture_analysis.md       <- Architectural breakdown and module alignment
│   ├── governance_components.md       <- Governance node internal components & OPA coupling
│   ├── governance_components.excalidraw<- Excalidraw architecture diagram
│   ├── layer2_modularization.md       <- Layer 2 modular monolith & scaling roadmap
│   ├── opa_first_governance.md        <- OPA-first PDP / PEP runtime ownership split
│   └── policy_service_architecture.md <- Deployable Policy Decision Point architecture
├── contracts_and_policy/              <- Governance Contracts, Research & Policy Rationale
│   ├── pillars_riferimento_completo.md<- [Canonical] Full four-pillar field/threshold reference, with diagrams
│   ├── pillars_prima_e_dopo.md        <- Pillar before/after narrative across this session's fixes
│   ├── governance_hitl_contract.md    <- Reason-scoped Human-in-the-Loop decision contract
│   ├── governance_pillars_research_basis.md <- Academic research foundations for oversight pillars
│   ├── tool_gateway_strategy.md       <- Read-only Tool Gateway registry and security strategy
│   └── framework_selection_rationale.md <- Technical evaluation and framework selection rationale
├── workflow/                          <- Runtime Trajectories & Request Sequences
│   ├── flow_details.md                <- Detailed step-by-step Layer 2 runtime flow
│   ├── workflow.txt                   <- Textual execution trace & step breakdown
│   ├── governed_request_sequence.md   <- External capability request sequence lifecycle
│   └── governed_request_sequence.mmd  <- Mermaid sequence diagram source
├── presentations/                     <- Visual Pitch Decks & Slide Presentations
│   ├── confidence_risk_trajectory_deck.html <- Historical HTML presentation: prior score-based oversight model
│   ├── current_runtime_flow_deck.html <- HTML presentation: Current Runtime Execution Flow
│   ├── planner_use_cases.html         <- HTML presentation: Multi-Step Planner Use Cases
│   └── governed_json_contracts/       <- Presentation deck assets & PPTX source
└── guidelines/                        <- Development References & Tutorials
    ├── plan-and-execute.ipynb         <- Jupyter tutorial reference for Plan-and-Execute pattern
    └── plan-and-execute.jpg           <- Visual reference diagram for Plan-and-Execute pattern
```

---

## Primary Design References

For development, testing, and thesis work, the primary reference documents are:

1. **Architecture (v3)**: [`docs/architecture/architecturev3.md`](architecture/architecturev3.md)
2. **Training Plan & Roadmap**: [`training_plan.md`](../training_plan.md)
3. **Repository Rules & Guidelines**: [`AGENTS.md`](../AGENTS.md)

## Operational and supporting references

- [Implementation map](agent-reference/implementation-map.md),
  [runtime contracts](agent-reference/runtime-contracts.md), and
  [operations](agent-reference/operations.md): maintained repository contracts.
- [Evaluation](evaluation/): campaign designs, rubrics and recorded conclusions.
- [Dashboard](dashboard/): human-review interface notes.
- [Thesis figures](../figures/README.md): selected illustrations and sources.
- [Publication review](repository-publication-review.md): cleanup scope and privacy findings.

Presentation deliverables and their editorial sources remain in `presentations/`.
Machine-specific generation prompts, job manifests and transient generation state
are local working material and are excluded from version control.
