"""Preflight check that the running OPA PDP actually serves the on-disk policy bundle.

The runtime's own version-mismatch gate (`main.rego`) only catches a bundle whose
*declared* `policy_bundle_version` differs from what Python expects. It cannot catch a
PDP that is silently serving stale rule *content* under an unchanged version string --
which is exactly what happens when `opa run --watch` fails to detect a file change on a
Windows bind mount: the process keeps running, the version string in `data.json` never
moved, and every decision looks legitimate while every rule it evaluates is the old one.

This script compares the exact source OPA reports it loaded (`GET /v1/policies`, one
entry per `.rego` module) and the exact `policy_config` document it is serving
(`GET /v1/data/policy_config`) against the files on disk under
`configs/opa/layer2_governance`. A mismatch anywhere means the PDP must be restarted
before starting or resuming a run against it.

Usage:
    .venv/Scripts/python.exe scripts/verify_opa_bundle.py
    .venv/Scripts/python.exe scripts/verify_opa_bundle.py --base-url http://127.0.0.1:8181

Exit code 0 means the loaded bundle matches disk exactly. Any other outcome (mismatch,
unreachable PDP, missing module) exits 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_ROOT = REPO_ROOT / "configs" / "opa" / "layer2_governance"

# The compose service mounts the same directory at this container path; OPA reports
# module ids relative to it. A bare `opa run --watch <dir>` invocation from the repo
# root instead reports ids relative to that directory's own name -- both are handled.
_KNOWN_MOUNT_PREFIXES = ("policy/", "layer2_governance/")


@dataclass(frozen=True)
class BundleMismatch:
    kind: str  # "missing_locally" | "missing_remotely" | "content_differs" | "data_differs"
    detail: str


def _fetch_json(url: str, *, timeout_seconds: float) -> dict:
    with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310 (local trusted PDP)
        return json.loads(response.read().decode("utf-8"))


def _local_rego_files() -> dict[str, str]:
    # Keyed by path relative to POLICY_ROOT, forward-slash normalized, so it matches
    # whichever mount-prefix convention the running OPA process used.
    return {
        # newline="" disables universal-newline translation: OPA reports the exact raw
        # bytes it read, and on a Windows checkout with CRLF line endings, the default
        # text-mode read would silently normalize to LF and report every file as stale.
        str(path.relative_to(POLICY_ROOT)).replace("\\", "/"): path.read_text(encoding="utf-8", newline="")
        for path in sorted(POLICY_ROOT.rglob("*.rego"))
    }


def _strip_known_prefix(module_id: str) -> str:
    for prefix in _KNOWN_MOUNT_PREFIXES:
        if module_id.startswith(prefix):
            return module_id[len(prefix):]
    return module_id


def _diff_policies(*, local: dict[str, str], remote: list[dict]) -> list[BundleMismatch]:
    mismatches: list[BundleMismatch] = []
    remote_by_relpath = {_strip_known_prefix(item["id"]): item.get("raw", "") for item in remote}

    for relpath, local_source in local.items():
        if relpath not in remote_by_relpath:
            mismatches.append(
                BundleMismatch("missing_remotely", f"{relpath} is on disk but was not reported by OPA")
            )
            continue
        remote_source = remote_by_relpath[relpath]
        if remote_source != local_source:
            mismatches.append(
                BundleMismatch(
                    "content_differs",
                    f"{relpath}: the loaded module text differs from the file on disk "
                    "(the PDP is serving a stale version)",
                )
            )

    for relpath in remote_by_relpath:
        if relpath not in local:
            mismatches.append(
                BundleMismatch("missing_locally", f"{relpath} was reported by OPA but does not exist on disk")
            )

    return mismatches


def _diff_policy_config(*, local: dict, remote: dict) -> list[BundleMismatch]:
    if local == remote:
        return []
    return [
        BundleMismatch(
            "data_differs",
            "data.policy_config differs between disk and the loaded PDP "
            f"(disk={json.dumps(local, sort_keys=True)}; "
            f"loaded={json.dumps(remote, sort_keys=True)})",
        )
    ]


def verify_bundle(*, base_url: str, timeout_seconds: float) -> list[BundleMismatch]:
    try:
        policies_response = _fetch_json(f"{base_url}/v1/policies", timeout_seconds=timeout_seconds)
        data_response = _fetch_json(f"{base_url}/v1/data/policy_config", timeout_seconds=timeout_seconds)
    except (URLError, TimeoutError, OSError) as exc:
        return [BundleMismatch("unreachable", f"Could not reach OPA at {base_url}: {exc}")]

    local_policies = _local_rego_files()
    remote_policies = policies_response.get("result", [])
    local_data = json.loads((POLICY_ROOT / "data.json").read_text(encoding="utf-8"))["policy_config"]
    remote_data = data_response.get("result", {})

    return _diff_policies(local=local_policies, remote=remote_policies) + _diff_policy_config(
        local=local_data, remote=remote_data
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8181")
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    args = parser.parse_args()

    mismatches = verify_bundle(base_url=args.base_url, timeout_seconds=args.timeout_seconds)
    if not mismatches:
        print(f"OK: the OPA bundle at {args.base_url} matches {POLICY_ROOT} exactly.")
        return 0

    print(f"MISMATCH: the OPA bundle at {args.base_url} does not match {POLICY_ROOT}.")
    for item in mismatches:
        print(f"  [{item.kind}] {item.detail}")
    print("\nRestart the PDP (docker compose restart, or kill and re-run `opa run --watch`) "
          "before starting or resuming any thread.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
