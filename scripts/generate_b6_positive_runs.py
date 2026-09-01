from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (REPO_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from layer2_runtime_governance import build_default_policy_engine  # noqa: E402
from layer4_state_evidence_observability.infrastructure.runtime_artifacts import (  # noqa: E402
    RuntimeArtifactStore,
)
from tests.b6_positive_fixtures import (  # noqa: E402
    run_b6_evidence_run,
    run_b6_protocol_replan_run,
    run_b6_trajectory_run,
)


B6_EVIDENCE_OFFICIAL_THREAD_ID = "b6-evidence-burden-insufficient-v2"
B6_TRAJECTORY_OFFICIAL_THREAD_ID = "b6-trajectory-replan-ceiling-v2"
B6_PROTOCOL_REPLAN_OFFICIAL_THREAD_ID = "b6-trajectory-protocol-replan-ceiling-v2"


# Generate the deterministic official artifacts through the deployed OPA adapter.
def generate_official_b6_runs(*, artifact_root: Path) -> dict[str, dict[str, Any]]:
    policy_engine = build_default_policy_engine()
    return {
        B6_EVIDENCE_OFFICIAL_THREAD_ID: run_b6_evidence_run(
            artifact_root=artifact_root,
            thread_id=B6_EVIDENCE_OFFICIAL_THREAD_ID,
            policy_engine_service=policy_engine,
        ),
        B6_TRAJECTORY_OFFICIAL_THREAD_ID: run_b6_trajectory_run(
            artifact_root=artifact_root,
            thread_id=B6_TRAJECTORY_OFFICIAL_THREAD_ID,
            policy_engine_service=policy_engine,
        ),
        B6_PROTOCOL_REPLAN_OFFICIAL_THREAD_ID: run_b6_protocol_replan_run(
            artifact_root=artifact_root,
            thread_id=B6_PROTOCOL_REPLAN_OFFICIAL_THREAD_ID,
            policy_engine_service=policy_engine,
        ),
    }


# Generate both versioned B6 artifacts under the active runtime corpus.
def main() -> int:
    artifact_root = REPO_ROOT / "artifacts" / "runtime"
    artifact_root.mkdir(parents=True, exist_ok=True)
    generated = generate_official_b6_runs(artifact_root=artifact_root)

    store = RuntimeArtifactStore(root_path=artifact_root)
    for thread_id in generated:
        paths = store.build_paths(thread_id)
        print(f"{thread_id}: {paths.thread_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
