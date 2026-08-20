from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (REPO_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from layer4_state_evidence_observability.infrastructure.runtime_artifacts import (  # noqa: E402
    RuntimeArtifactStore,
)
from tests.b6_positive_fixtures import (  # noqa: E402
    B6_EVIDENCE_THREAD_ID,
    B6_TRAJECTORY_THREAD_ID,
    run_b6_evidence_run,
    run_b6_trajectory_run,
)


# Generate both deterministic B6 artifacts under the active runtime corpus.
def main() -> int:
    artifact_root = REPO_ROOT / "artifacts" / "runtime"
    artifact_root.mkdir(parents=True, exist_ok=True)
    run_b6_evidence_run(artifact_root=artifact_root)
    run_b6_trajectory_run(artifact_root=artifact_root)

    store = RuntimeArtifactStore(root_path=artifact_root)
    for thread_id in (B6_EVIDENCE_THREAD_ID, B6_TRAJECTORY_THREAD_ID):
        paths = store.build_paths(thread_id)
        print(f"{thread_id}: {paths.thread_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
