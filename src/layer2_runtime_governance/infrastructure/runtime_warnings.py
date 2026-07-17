"""Targeted dependency-warning filters owned by runtime infrastructure."""

from __future__ import annotations

import warnings


# Suppress the known Python 3.14 compatibility warning emitted by langchain_core.
def suppress_known_runtime_warnings() -> None:
    warnings.filterwarnings(
        "ignore",
        message=r"Core Pydantic V1 functionality isn't compatible with Python 3\.14 or greater\.",
        category=UserWarning,
        module=r"langchain_core\._api\.deprecation",
    )


__all__ = ["suppress_known_runtime_warnings"]
