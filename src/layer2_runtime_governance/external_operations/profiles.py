from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class CapabilityConsequenceProfile:
    # Profiles are reviewable configuration facts, not adapter-declared risk scores.
    profile_id: str
    profile_version: str
    family: str
    target_criticality: str
    scope: str
    compensation_mechanism: str | None
    allowed_statement_classes: tuple[str, ...] = ()


# Load the versioned consequence profiles from repository configuration.
def load_capability_consequence_profiles(
    *,
    config_path: Path | None = None,
) -> dict[str, CapabilityConsequenceProfile]:
    resolved = config_path or _default_profile_path()
    return _load_profiles_cached(resolved.resolve())


# Cache immutable profile configuration across governance turns.
@lru_cache(maxsize=4)
def _load_profiles_cached(
    resolved_path: Path,
) -> dict[str, CapabilityConsequenceProfile]:
    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    version = str(payload["profile_version"])
    return {
        str(profile_id): CapabilityConsequenceProfile(
            profile_id=str(profile_id),
            profile_version=version,
            family=str(profile["family"]),
            target_criticality=str(profile.get("target_criticality", "unknown")),
            scope=str(profile.get("scope", "unknown")),
            compensation_mechanism=(
                str(profile["compensation_mechanism"])
                if profile.get("compensation_mechanism")
                else None
            ),
            allowed_statement_classes=tuple(
                str(item) for item in profile.get("allowed_statement_classes", [])
            ),
        )
        for profile_id, profile in payload["profiles"].items()
    }


# Resolve the repository-local configuration path.
def _default_profile_path() -> Path:
    return Path(__file__).resolve().parents[3] / "configs" / "layer2_external_operation_profiles.json"


__all__ = ["CapabilityConsequenceProfile", "load_capability_consequence_profiles"]
