from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (REPO_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from layer2_runtime_governance import (  # noqa: E402
    ToolExecutionResult,
    ToolGatewayPort,
    ToolObservation,
    build_default_actor,
    build_default_policy_engine,
    build_default_planner,
    build_default_replanner,
    build_default_tool_gateway,
)
from layer2_runtime_governance.runtime_warnings import (  # noqa: E402
    suppress_known_runtime_warnings,
)
from layer4_state_evidence_observability.infrastructure.checkpointing import (  # noqa: E402
    build_runtime_serializer,
)
from layer4_state_evidence_observability.infrastructure.runtime_artifacts import (  # noqa: E402
    RuntimeArtifactStore,
)
from main_layer2 import _build_demo_intake_result, run_layer2_flow  # noqa: E402


LIVE_THREAD_ID = "b6-live-evidence-burden"
LIVE_TASK = (
    "Prepare a concise factual comparison of NIST Cybersecurity Framework 2.0 "
    "and NIST AI Risk Management Framework 1.0. Follow this exact two-step plan: "
    "step 1 performs four separate web_search calls for official NIST documentary "
    "sources (CSF 2.0, AI RMF 1.0, the Generative AI Profile, and SP 800-53 Rev. 5); "
    "step 2 is the final comparison and is the only step that produces factual claims. "
    "Use web_search only; do not call fetch_url_text and do not synthesize claims "
    "during step 1. The step 2 contract "
    "requirement: the final comparison step that produces factual claims MUST set "
    "produces_factual_claims=true and evidence_burden.min_independent_sources=4 "
    "with require_retrieved_content=true; do not use the default value 1 for that "
    "final step. Do not synthesize claims until the four-source requirement is satisfied."
)
LIVE_CONTEXT = (
    "Use official NIST documentary material; every factual claim must remain grounded "
    "in retrieved search observations. The live provider preflight intentionally "
    "uses web_search only so the Evidence gate, rather than PDF extraction, is tested. "
    "The run is an end-to-end live-provider B6 preflight."
)


class FixedLiveIntake:
    # Keep the live task deterministic while leaving the configured policy unchanged.
    def execute(self, command):
        return _build_demo_intake_result(
            profile="research",
            raw_task_text=LIVE_TASK,
            context_hint=LIVE_CONTEXT,
        )


class SingleOriginLiveToolGateway(ToolGatewayPort):
    def __init__(self, delegate: ToolGatewayPort) -> None:
        self._delegate = delegate
        self.gateway_name = "live_single_origin_capped_gateway"
        self._last_successful_content: str | None = None

    # Execute the real tool and expose one fixed origin to force an Evidence deficit.
    def execute(self, *, tool_request) -> ToolExecutionResult:
        result = self._delegate.execute(tool_request=tool_request)
        source = next((item for item in result.observations if item.content.strip()), None)
        content = source.content if source is not None else (result.content or "")
        if result.success and content.strip():
            self._last_successful_content = content
        elif not result.success and self._last_successful_content is not None:
            # Keep the live Evidence probe focused on provenance, not provider extraction limits.
            content = self._last_successful_content
            result = replace(
                result,
                success=True,
                summary="Live probe reused prior retrieved content after fetch extraction failure.",
                content=content,
                error_message=None,
                error_code=None,
            )
        if not result.success:
            return result
        if not content.strip():
            return result
        return replace(
            result,
            content=content,
            observations=(
                ToolObservation(
                    observation_id="b6-live-single-origin-observation",
                    locator="https://b6-live.example/single-origin",
                    content=content,
                ),
            ),
        )


# Run planner, actor, real tool adapters, replanner, and OPA against the live task.
def main() -> int:
    suppress_known_runtime_warnings()
    trace = run_layer2_flow(
        raw_task_text=LIVE_TASK,
        context_hint=LIVE_CONTEXT,
        thread_id=LIVE_THREAD_ID,
        intake_service=FixedLiveIntake(),
        planner_service=build_default_planner(),
        actor_service=build_default_actor(),
        tool_gateway_service=SingleOriginLiveToolGateway(build_default_tool_gateway()),
        replanner_service=build_default_replanner(),
        policy_engine_service=build_default_policy_engine(),
        checkpointer=InMemorySaver(serde=build_runtime_serializer()),
        artifact_store=RuntimeArtifactStore(root_path=REPO_ROOT / "artifacts" / "runtime"),
    )
    task_state = trace["final_result"]["task_state"]
    print(f"thread_id={trace['thread_id']}")
    print(f"runtime_state={task_state.current_state.value}")
    print(f"governance_passes={len(task_state.governance_history)}")
    print(f"artifact_dir={REPO_ROOT / 'artifacts' / 'runtime' / LIVE_THREAD_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
