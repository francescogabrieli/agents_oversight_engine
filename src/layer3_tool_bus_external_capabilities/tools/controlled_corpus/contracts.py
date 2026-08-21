from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ControlledDocumentDefinition:
    # A document is selected by a stable identifier, never by an actor-provided path.
    document_id: str
    title: str
    description: str
    path: Path


@dataclass(frozen=True)
class ControlledDatasetDefinition:
    # A dataset is a small, versioned CSV snapshot with a registry-owned row ceiling.
    dataset_id: str
    title: str
    description: str
    schema_version: str
    path: Path
    max_rows: int


@dataclass(frozen=True)
class ControlledCorpusConfig:
    # The immutable registry keeps evaluation fixtures explicit and auditable.
    documents: dict[str, ControlledDocumentDefinition]
    datasets: dict[str, ControlledDatasetDefinition]


__all__ = [
    "ControlledCorpusConfig",
    "ControlledDatasetDefinition",
    "ControlledDocumentDefinition",
]
