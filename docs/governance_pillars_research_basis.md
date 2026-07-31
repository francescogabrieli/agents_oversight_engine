# Governance pillars: research basis

## 1. Purpose and scope

This note records the theoretical and experimental basis for the thesis's central
architectural contribution: an auditable Human Oversight and HITL engine applied to an
autonomous agent runtime. It focuses on Evidence Integrity & Provenance, Trajectory
Health, the Governed External Semantic Judge, reason-scoped human intervention, and the
separation between signal production and OPA-owned final decision. It distinguishes
reported literature results, repository design inferences, and hypotheses still to be
tested in this proof of concept. The literature does not prove this architecture.

## 2. Architectural separation

| Pillar | Unit of analysis | Exclusive question | Output | Forbidden responsibility |
|---|---|---|---|---|
| Evidence Integrity & Provenance | complete observation manifest and actor selection | Is support observable, integral and reconstructible? | deterministic structural facts/digests | semantic entailment or directives |
| Trajectory Health | run state transitions | Is execution progressing, stalled, looping or recovering? | deterministic liveness facts | judging source content or payload semantics |
| Governed External Semantic Judge | exact external payload and semantic evidence base | Is this digest-bound operation semantically ready? | typed verdict/findings/claim assessments | runtime directive or authorization |
| OPA | policy input and pillar facts | Which directive applies? | final directive | semantic assessment generation |

These are epistemic/support integrity, procedural/execution health, and semantic external-action fitness—not three estimates of one risk probability.

## 3. Evidence Integrity & Provenance

AttributionBench defines attribution evaluation as whether generated claims are fully supported by cited evidence and reports that the task remains difficult: in its binary setting a fine-tuned GPT-3.5 reaches about 80% macro-F1, with errors including mismatch between model-visible and annotator-visible information. [Li et al. (2024)](https://aclanthology.org/2024.findings-acl.886/) therefore motivates separating citation existence/provenance from semantic support. We infer that this runtime should deterministically validate manifest integrity and give the semantic comparison to the Judge, which sees the complete semantic observation base. AttributionBench neither evaluates governed-external runtime nor prescribes OPA, Trajectory, or this decomposition.

FActScore decomposes text into atomic facts and measures the share supported by a knowledge source, showing why a global binary judgment hides mixed support. [Min et al. (2023)](https://aclanthology.org/2023.emnlp-main.741/) We infer that future `ClaimEvidenceLink` contracts are appropriate; this slice instead makes Judge claim assessments observable without implementing FActScore. The paper does not address policy, recipients, external effects, or HITL.

Concrete translation: [evidence.py](../src/layer2_runtime_governance/domain/evidence.py)
builds the canonical manifest and digest primitives;
[evidence_service.py](../src/layer2_runtime_governance/signal_services/evidence_service.py)
computes lineage, known-step, title-only and immutable
candidate/snapshot/selection binding facts;
[evidence.rego](../configs/opa/layer2_governance/pillars/evidence.rego) uses only
structural gaps; the Judge owns claim support.

## 4. Trajectory Health

TRAJECT-Bench distinguishes final accuracy from tool-use trajectory quality, including tool selection, parameterization, order and dependencies. [He et al. (2025)](https://arxiv.org/abs/2510.04550) Agent-as-a-Judge likewise motivates attention to step-wise agent behaviour rather than final outcome alone. [Zhuge et al. (2024)](https://arxiv.org/abs/2410.10934) We infer deterministic observations of replan basis, state delta, recovery and revision churn. High turn count is not degradation without an absent observable delta. Neither work specifies our liveness policy, Rego thresholds, or deterministic implementation.

Concrete translation: [trajectory_service.py](../src/layer2_runtime_governance/signal_services/trajectory_service.py) does not contain point confidence and identifies resubmission on the pair payload digest + Evidence-selection digest, so corrected attribution is not mislabeled as unchanged payload churn. [trajectory.rego](../configs/opa/layer2_governance/pillars/trajectory.rego) distinguishes an actual stalled replan request from unchanged or previously rejected candidate resubmission. All three can block Judge admission, but OPA exposes separate reasons and none grants execution authority.

## 5. Governed External Semantic Judge

G-Eval studies explicit criteria and structured LLM evaluation and reports stronger human alignment than its evaluated baselines, while position/repetition research cautions that LLM judges can be biased or unstable. [Liu et al. (2023)](https://aclanthology.org/2023.emnlp-main.153), [Shi et al. (2025)](https://aclanthology.org/2025.ijcnlp-long.18/) We infer a narrow, typed, digest-bound rubric, prompt/model metadata, a real `uncertain` verdict, and no direct authorization. These papers do not establish that one judge is sufficient for high-stakes governance; transfer to this runtime remains an evaluation hypothesis.

The context separates authoritative task data, non-authoritative plan/step summaries, and complete bounded typed observations. It excludes process dynamics. Completeness describes coverage of the canonical observation universe, while truncation/extractability remains an item-level limitation; the latter must not silently relabel the entire evidence base as omitted. `unsafe` is for materially harmful/deceptive/coercive/unauthorized effects; normal unsupported claims are `needs_revision`, and genuinely incomplete relevant context is `uncertain`.

## 6. Governance, accountability and human oversight

NIST AI RMF 1.0 and its Generative AI Profile motivate documented roles, measurement, testing, accountability and human oversight. [NIST AI RMF 1.0 (2023)](https://doi.org/10.6028/NIST.AI.100-1), [NIST Generative AI Profile (2024)](https://doi.org/10.6028/NIST.AI.600-1) We infer the signal-producer/PDP/reviewer separation and fail-closed handling. NIST does not mandate OPA, Rego, LangGraph, or these three pillars; OPA as final PDP is a PoC design choice.

## 7. Design-decision matrix

| Decision ID | Architectural decision | Primary source | Source-supported observation | Project inference | Limitation | Implemented in |
|---|---|---|---|---|---|---|
| D1 | Deterministic evidence | AttributionBench | citation support is hard | structural checks stay non-semantic | different setting | [evidence_service.py](../src/layer2_runtime_governance/signal_services/evidence_service.py) |
| D2 | Semantic grounding in Judge | FActScore | mixed atomic support matters | typed claim findings | not FActScore | [contracts.py](../src/layer2_runtime_governance/judge/contracts.py) |
| D3 | Complete evidence base | AttributionBench | visibility mismatch causes errors | all observations, selected marked | context limits remain | [judge_service.py](../src/layer2_runtime_governance/signal_services/judge_service.py) |
| D4 | Selected/all separation | AttributionBench | evidence access matters | actor reliance is not universe | no causal proof | [prompts.py](../src/layer2_runtime_governance/judge/prompts.py) |
| D5 | Claim-level findings | FActScore | global binary hides detail | typed statuses | no atomic extractor | [service.py](../src/layer2_runtime_governance/judge/service.py) |
| D6 | Separate trajectory | TRAJECT-Bench | path quality differs from outcome | deterministic liveness facts | benchmark differs | [trajectory_service.py](../src/layer2_runtime_governance/signal_services/trajectory_service.py) |
| D7 | Turns insufficient | Agent-as-a-Judge | steps matter | delta, not count, drives health | no threshold prescribed | [trajectory.rego](../configs/opa/layer2_governance/pillars/trajectory.rego) |
| D8 | Typed Judge rubric | G-Eval | criteria/structured evaluation help | typed verdicts/findings | judge bias remains | [contracts.py](../src/layer2_runtime_governance/judge/contracts.py) |
| D9 | Judge non-authoritative | Judging the Judges | LLM judging has bias/variance | trace and OPA separation | pointwise transfer uncertain | [main.rego](../configs/opa/layer2_governance/main.rego) |
| D10 | OPA final authority | NIST RMF | accountability/roles matter | PDP owns directive | OPA not prescribed | [opa_pipeline.py](../src/layer2_runtime_governance/governance_components/opa_pipeline.py) |
| D11 | Conservative HITL | NIST GenAI Profile | oversight matters | unavailable/uncertain → review | no exact mapping supplied | [main.rego](../configs/opa/layer2_governance/main.rego) |
| D12 | Digest binding | G-Eval | evaluator scope must be explicit | candidate and assessment bind exact payload, complete observation snapshot, and actor selection | not execution proof | [judge_service.py](../src/layer2_runtime_governance/signal_services/judge_service.py) |

## 8. Research hypotheses and future evaluation

- H1: separated pillars improve failure localization over a monolithic LLM gate.
- H2: complete observations reduce false `unsupported` relative to selected refs only.
- H3: Trajectory finds churn and pathological replans invisible in final output.
- H4: typed rubric, digest binding and OPA ownership reduce Judge causal ambiguity.
- H5: deterministic-before-Judge staging, restricted to exact `STEP_COMPLETED`
  governed-external candidates, reduces unnecessary LLM calls.

Future ablations: remove Evidence, Trajectory, or Judge; selected-only versus complete Judge evidence; monolithic LLM gate; and simultaneous versus staged admission. These are proposals, not completed benchmark results.

## 9. Bibliography

Li, Y., Yue, X., Liao, Z., and Sun, H. “AttributionBench: How Hard is Automatic Attribution Evaluation?” *Findings of ACL 2024*, 2024, pp. 14919–14935. DOI: https://doi.org/10.18653/v1/2024.findings-acl.886. Official URL: https://aclanthology.org/2024.findings-acl.886/

Min, S., Krishna, K., Lyu, X., Lewis, M., Yih, W.-t., Koh, P., Iyyer, M., Zettlemoyer, L., and Hajishirzi, H. “FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation.” *EMNLP 2023*, 2023, pp. 12076–12100. DOI: https://doi.org/10.18653/v1/2023.emnlp-main.741. Official URL: https://aclanthology.org/2023.emnlp-main.741/

He, P. et al. “TRAJECT-Bench: A Trajectory-Aware Benchmark for Evaluating Agentic Tool Use.” *arXiv:2510.04550*, 2025. DOI: not assigned. Official URL: https://arxiv.org/abs/2510.04550

Zhuge, M. et al. “Agent-as-a-Judge: Evaluate Agents with Agents.” *arXiv:2410.10934*, 2024. DOI: not assigned. Official URL: https://arxiv.org/abs/2410.10934

Liu, Y., Iter, D., Xu, Y., Wang, S., Xu, R., and Zhu, C. “G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment.” *EMNLP 2023*, 2023, pp. 2511–2522. DOI: https://doi.org/10.18653/v1/2023.emnlp-main.153. Official URL: https://aclanthology.org/2023.emnlp-main.153/

Shi, L., Ma, C., Liang, W., Diao, X., Ma, W., and Vosoughi, S. “Judging the Judges: A Systematic Study of Position Bias in LLM-as-a-Judge.” *IJCNLP-AACL 2025*, 2025, pp. 292–314. DOI: https://doi.org/10.18653/v1/2025.ijcnlp-long.18. Official URL: https://aclanthology.org/2025.ijcnlp-long.18/

Tabassi, E. *Artificial Intelligence Risk Management Framework (AI RMF 1.0).* NIST AI 100-1, 2023. DOI: https://doi.org/10.6028/NIST.AI.100-1. Official URL: https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10

Autio, C. et al. *Artificial Intelligence Risk Management Framework: Generative Artificial Intelligence Profile.* NIST AI 600-1, 2024. DOI: https://doi.org/10.6028/NIST.AI.600-1. Official URL: https://www.nist.gov/publications/artificial-intelligence-risk-management-generative-artificial-intelligence
