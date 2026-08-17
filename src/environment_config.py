from __future__ import annotations

import os
import warnings
from functools import lru_cache
from pathlib import Path


# Resolve the authoritative root environment file and the legacy source fallback.
def candidate_env_paths() -> tuple[Path, ...]:
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / ".env", repo_root / "src" / ".env")


# Read local development settings once while leaving process variables authoritative.
@lru_cache(maxsize=1)
def load_env_file_values() -> dict[str, str]:
    env_values: dict[str, str] = {}
    root_env_path, legacy_env_path = candidate_env_paths()
    if legacy_env_path.exists():
        warnings.warn(
            "src/.env is deprecated; move local settings to the repository root .env.",
            DeprecationWarning,
            stacklevel=2,
        )

    # Read the legacy file first so root .env stays authoritative.
    for candidate in (legacy_env_path, root_env_path):
        if not candidate.exists():
            continue
        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key:
                env_values[key] = value
    return env_values


# Resolve one setting with process-environment precedence over local files.
def get_env_value(name: str, default: str | None = None) -> str | None:
    process_value = os.getenv(name)
    if process_value is not None:
        return process_value
    return load_env_file_values().get(name, default)


__all__ = ["candidate_env_paths", "get_env_value", "load_env_file_values"]
