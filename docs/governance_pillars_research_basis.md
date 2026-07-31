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

FaStFact identifies insufficient evidence collection—particularly reliance on short search-result snippets—as a source of ineffective factuality assessment, and instead retrieves document-level evidence for claim verification. [Wan et al. (2025)](https://aclanthology.org/2025.findings-emnlp.1295/) This supports the project's decision to preserve concrete observation content and to expose the complete canonical observation pool to the Judge rather than treating actor-selected references as the epistemic universe. FaStFact evaluates long-form factuality, not runtime lineage, immutable snapshot binding, external-action authorization, or this Evidence/Judge split.

A 2026 ACL survey of 134 papers and 300 metrics describes attribution, citation, and quotation as related but distinct dimensions of evidence-based generation, and emphasizes traceability and verifiability amid fragmented terminology and evaluation practice. [Schreieder et al. (2026)](https://aclanthology.org/2026.acl-long.1430/) We use that taxonomy only as current field-level support for keeping structural provenance distinct from semantic entailment; the survey does not validate this repository's specific contracts.

Concrete translation: [evidence.py](../src/layer2_runtime_governance/domain/evidence.py)
builds the canonical manifest and digest primitives;
[evidence_service.py](../src/layer2_runtime_governance/signal_services/evidence_service.py)
computes lineage, known-step, title-only and immutable
candidate/snapshot/selection binding facts;
[evidence.rego](../configs/opa/layer2_governance/pillars/evidence.rego) uses only
structural gaps; the Judge owns claim support.

## 4. Trajectory Health

TRAJECT-Bench distinguishes final accuracy from tool-use trajectory quality, including tool selection, parameterization, order and dependencies. [He et al. (2025)](https://arxiv.org/abs/2510.04550) ToolSandbox similarly evaluates stateful tool execution through intermediate and final milestones, implicit state dependencies, and arbitrary on-policy trajectories rather than only a terminal answer. [Lu et al. (2025)](https://aclanthology.org/2025.findings-naacl.65/) These results support observing transitions and state changes as first-class evaluation material.

Process Evaluation for Agentic Systems argues that outcome evaluation leaves process-level failures hidden and proposes explicit verification of protocol adherence to make agent behaviour more transparent, attributable, and auditable. [Gritta et al. (2026)](https://aclanthology.org/2026.findings-eacl.140/) Plan-RewardBench further reports that generative, discriminative, and LLM-as-a-Judge evaluators all face substantial difficulty on long-horizon agent trajectories, including robust error recovery. [Wang et al. (2026)](https://aclanthology.org/2026.acl-long.1062/) We therefore infer a deliberately narrow deterministic Trajectory pillar for observable liveness and convergence facts rather than a second semantic process judge. High turn count is not degradation without an absent observable delta. None of these works specifies our liveness policy, Rego thresholds, digest-pair resubmission rule, or deterministic implementation.

Concrete translation: [trajectory_service.py](../src/layer2_runtime_governance/signal_services/trajectory_service.py) does not contain point confidence and identifies resubmission on the pair payload digest + Evidence-selection digest, so corrected attribution is not mislabeled as unchanged payload churn. [trajectory.rego](../configs/opa/layer2_governance/pillars/trajectory.rego) distinguishes an actual stalled replan request from unchanged or previously rejected candidate resubmission. All three can block Judge admission, but OPA exposes separate reasons and none grants execution authority.

## 5. Governed External Semantic Judge

G-Eval studies explicit criteria and structured LLM evaluation and reports stronger human alignment than its evaluated baselines. [Liu et al. (2023)](https://aclanthology.org/2023.emnlp-main.153/) More recent evidence sharpens the design implication. CheckEval reports that decomposing evaluation into traceable checklist decisions improves inter-model agreement and reduces score variance relative to subjective rating protocols. [Lee et al. (2025)](https://aclanthology.org/2025.emnlp-main.796/) Yamauchi et al. find that explicit evaluation criteria and score descriptions are critical for reliability, while chain-of-thought adds little when the criteria are already clear. [Yamauchi et al. (2026)](https://aclanthology.org/2026.gem-main.19.pdf) These findings support typed dimensions and findings rather than one opaque scalar or an unconstrained narrative judgment.

Reliability remains bounded: position-bias research finds systematic judge inconsistency across models and tasks. [Shi et al. (2025)](https://aclanthology.org/2025.ijcnlp-long.18/) We infer a narrow, typed, digest-bound rubric, prompt/model metadata, a real `uncertain` verdict, and no direct authorization. These papers evaluate text-generation judgments rather than governed external operations, and they do not establish that one Judge is sufficient for high-stakes governance; transfer to this runtime remains an evaluation hypothesis.

The context separates authoritative task data, non-authoritative plan/step summaries, and complete bounded typed observations. It excludes process dynamics. Completeness describes coverage of the canonical observation universe, while truncation/extractability remains an item-level limitation; the latter must not silently relabel the entire evidence base as omitted. `unsafe` is for materially harmful/deceptive/coercive/unauthorized effects; normal unsupported claims are `needs_revision`, and genuinely incomplete relevant context is `uncertain`.

## 6. Governance, accountability and human oversight

NIST AI RMF 1.0 and its Generative AI Profile motivate documented roles, measurement, testing, accountability and human oversight. [NIST AI RMF 1.0 (2023)](https://doi.org/10.6028/NIST.AI.100-1), [NIST Generative AI Profile (2024)](https://doi.org/10.6028/NIST.AI.600-1) We infer the signal-producer/PDP/reviewer separation and fail-closed handling. NIST does not mandate OPA, Rego, LangGraph, or these three pillars; OPA as final PDP is a PoC design choice.

NIST's 2026 agentic-AI evaluation-probes project provides a particularly close contemporary analogue: it describes automated verifiers integrated into agent workflows, factual-grounding assessment against curated reference corpora, and machine-readable audit trails mapping outputs to supporting evidence. [NIST (2026)](https://www.nist.gov/programs-projects/building-evaluation-probes-agentic-ai) This independently supports evaluation-at-the-workflow-boundary and audit-artifact generation. It remains an active NIST research project rather than a normative standard, and it does not prescribe this runtime's pillar boundaries, HITL state machine, digest authorization, or OPA ownership.

## 7. Design-decision matrix

| Decision ID | Architectural decision | Primary source | Source-supported observation | Project inference | Limitation | Implemented in |
|---|---|---|---|---|---|---|
| D1 | Deterministic evidence | AttributionBench | citation support is hard | structural checks stay non-semantic | different setting | [evidence_service.py](../src/layer2_runtime_governance/signal_services/evidence_service.py) |
| D2 | Semantic grounding in Judge | FActScore | mixed atomic support matters | typed claim findings | not FActScore | [contracts.py](../src/layer2_runtime_governance/judge/contracts.py) |
| D3 | Complete evidence base | AttributionBench | visibility mismatch causes errors | all observations, selected marked | context limits remain | [judge_service.py](../src/layer2_runtime_governance/signal_services/judge_service.py) |
| D4 | Selected/all separation | AttributionBench | evidence access matters | actor reliance is not universe | no causal proof | [prompts.py](../src/layer2_runtime_governance/judge/prompts.py) |
| D5 | Claim-level findings | FActScore | global binary hides detail | typed statuses | no atomic extractor | [service.py](../src/layer2_runtime_governance/judge/service.py) |
| D6 | Separate trajectory | Process Evaluation; ToolSandbox | process and intermediate state expose failures hidden by outcome | deterministic liveness facts | neither prescribes this pillar | [trajectory_service.py](../src/layer2_runtime_governance/signal_services/trajectory_service.py) |
| D7 | Turns insufficient | TRAJECT-Bench; Plan-RewardBench | trajectory length and structure affect evaluation, but length alone is not a failure label | delta, not count, drives health | no threshold prescribed | [trajectory.rego](../configs/opa/layer2_governance/pillars/trajectory.rego) |
| D8 | Typed Judge rubric | G-Eval; CheckEval | explicit, decomposed criteria improve interpretability and reliability | typed verdicts/findings | judge bias remains | [contracts.py](../src/layer2_runtime_governance/judge/contracts.py) |
| D9 | Judge non-authoritative | Judging the Judges | LLM judging has bias/variance | trace and OPA separation | pointwise transfer uncertain | [main.rego](../configs/opa/layer2_governance/main.rego) |
| D10 | OPA final authority | NIST RMF | accountability/roles matter | PDP owns directive | OPA not prescribed | [opa_pipeline.py](../src/layer2_runtime_governance/governance_components/opa_pipeline.py) |
| D11 | Conservative HITL | NIST GenAI Profile | oversight matters | unavailable/uncertain → review | no exact mapping supplied | [main.rego](../configs/opa/layer2_governance/main.rego) |
| D12 | Digest binding | NIST evaluation probes; project inference | audit trails should connect outputs and supporting evidence | candidate and assessment bind exact payload, complete observation snapshot, and actor selection | source does not prescribe cryptographic binding | [judge_service.py](../src/layer2_runtime_governance/signal_services/judge_service.py) |

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

Lu, J. et al. “ToolSandbox: A Stateful, Conversational, Interactive Evaluation Benchmark for LLM Tool Use Capabilities.” *Findings of NAACL 2025*, 2025, pp. 1160–1183. DOI: https://doi.org/10.18653/v1/2025.findings-naacl.65. Official URL: https://aclanthology.org/2025.findings-naacl.65/

Gritta, M., Paul, D., Li, X., Shang, L., Wang, J., and Lampouras, G. “Process Evaluation for Agentic Systems.” *Findings of EACL 2026*, 2026, pp. 2678–2692. DOI: https://doi.org/10.18653/v1/2026.findings-eacl.140. Official URL: https://aclanthology.org/2026.findings-eacl.140/

Wang, J., Hu, Y., Yang, W., Pan, Z., Li, X., and Guo, L.-Z. “Aligning Agents via Planning: A Benchmark for Trajectory-Level Reward Modeling.” *ACL 2026*, 2026, pp. 23174–23200. DOI: https://doi.org/10.18653/v1/2026.acl-long.1062. Official URL: https://aclanthology.org/2026.acl-long.1062/

Liu, Y., Iter, D., Xu, Y., Wang, S., Xu, R., and Zhu, C. “G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment.” *EMNLP 2023*, 2023, pp. 2511–2522. DOI: https://doi.org/10.18653/v1/2023.emnlp-main.153. Official URL: https://aclanthology.org/2023.emnlp-main.153/

Lee, Y., Kim, J., Kim, J., Cho, H., Kang, J., Kang, P., and Kim, N. “CheckEval: A Reliable LLM-as-a-Judge Framework for Evaluating Text Generation Using Checklists.” *EMNLP 2025*, 2025, pp. 15771–15798. DOI: https://doi.org/10.18653/v1/2025.emnlp-main.796. Official URL: https://aclanthology.org/2025.emnlp-main.796/

Yamauchi, Y., Yano, T., and Oyamada, M. “An Empirical Study of LLM-as-a-Judge: How Design Choices Impact Evaluation Reliability.” *GEM 2026*, 2026, pp. 167–176. Official URL: https://aclanthology.org/2026.gem-main.19/

Shi, L., Ma, C., Liang, W., Diao, X., Ma, W., and Vosoughi, S. “Judging the Judges: A Systematic Study of Position Bias in LLM-as-a-Judge.” *IJCNLP-AACL 2025*, 2025, pp. 292–314. DOI: https://doi.org/10.18653/v1/2025.ijcnlp-long.18. Official URL: https://aclanthology.org/2025.ijcnlp-long.18/

Tabassi, E. *Artificial Intelligence Risk Management Framework (AI RMF 1.0).* NIST AI 100-1, 2023. DOI: https://doi.org/10.6028/NIST.AI.100-1. Official URL: https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10

Autio, C. et al. *Artificial Intelligence Risk Management Framework: Generative Artificial Intelligence Profile.* NIST AI 600-1, 2024. DOI: https://doi.org/10.6028/NIST.AI.600-1. Official URL: https://www.nist.gov/publications/artificial-intelligence-risk-management-generative-artificial-intelligence

Wan, Y. et al. “FaStFact: Faster, Stronger Long-Form Factuality Evaluations in LLMs.” *Findings of EMNLP 2025*, 2025. DOI: https://doi.org/10.18653/v1/2025.findings-emnlp.1295. Official URL: https://aclanthology.org/2025.findings-emnlp.1295/

Schreieder, T., Schopf, T., and Färber, M. “Attribution, Citation, and Quotation: A Survey of Evidence-based Text Generation with Large Language Models.” *ACL 2026*, 2026, pp. 30956–31000. DOI: https://doi.org/10.18653/v1/2026.acl-long.1430. Official URL: https://aclanthology.org/2026.acl-long.1430/

National Institute of Standards and Technology. “Building Evaluation Probes into Agentic AI.” NIST Information Technology Laboratory, 2026. Official URL: https://www.nist.gov/programs-projects/building-evaluation-probes-agentic-ai
