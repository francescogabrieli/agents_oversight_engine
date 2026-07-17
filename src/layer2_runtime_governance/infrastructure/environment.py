from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


# Resolve repository-local environment files from the infrastructure boundary.
def candidate_env_paths() -> tuple[Path, ...]:
    repo_root = Path(__file__).resolve().parents[3]
    return (repo_root / ".env", repo_root / "src" / ".env")


# Read local development settings once while leaving process variables authoritative.
@lru_cache(maxsize=1)
def load_env_file_values() -> dict[str, str]:
    env_values: dict[str, str] = {}
    for candidate in candidate_env_paths():
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


# Resolve one setting with process environment precedence over local files.
def get_env_value(name: str, default: str | None = None) -> str | None:
    process_value = os.getenv(name)
    if process_value is not None:
        return process_value
    return load_env_file_values().get(name, default)


__all__ = ["candidate_env_paths", "get_env_value", "load_env_file_values"]
