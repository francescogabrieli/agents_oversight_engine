from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .contracts import (
    ControlledCorpusConfig,
    ControlledDatasetDefinition,
    ControlledDocumentDefinition,
)


# Load the controlled-corpus registry from the single configuration source.
def load_controlled_corpus_config(
    *,
    config_path: Path | None = None,
) -> ControlledCorpusConfig:
    resolved_path = config_path or get_default_controlled_corpus_config_path()
    if not resolved_path.exists():
        raise FileNotFoundError(
            f"Controlled corpus configuration does not exist: {resolved_path}"
        )
    return _load_controlled_corpus_config_cached(resolved_path.resolve())


# Cache a parsed static registry rather than rereading it for every tool request.
@lru_cache(maxsize=8)
def _load_controlled_corpus_config_cached(
    resolved_path: Path,
) -> ControlledCorpusConfig:
    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Controlled corpus configuration must be a JSON object.")
    repository_root = Path(__file__).resolve().parents[4]
    documents = _parse_documents(
        payload.get("documents", []),
        repository_root=repository_root,
    )
    datasets = _parse_datasets(
        payload.get("datasets", []),
        repository_root=repository_root,
    )
    return ControlledCorpusConfig(documents=documents, datasets=datasets)


# Return the repository-owned registry used by the default evaluation corpus.
def get_default_controlled_corpus_config_path() -> Path:
    repository_root = Path(__file__).resolve().parents[4]
    return repository_root / "configs" / "layer2_controlled_corpus.json"


# Parse all document entries and reject duplicate IDs before runtime execution.
def _parse_documents(
    payload: object,
    *,
    repository_root: Path,
) -> dict[str, ControlledDocumentDefinition]:
    if not isinstance(payload, list):
        raise ValueError("Controlled corpus documents must be a JSON array.")
    documents: dict[str, ControlledDocumentDefinition] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each controlled document entry must be a JSON object.")
        document_id = _required_text(item.get("document_id"), "document_id")
        if document_id in documents:
            raise ValueError(f"Controlled document ID is duplicated: {document_id}")
        documents[document_id] = ControlledDocumentDefinition(
            document_id=document_id,
            title=_required_text(item.get("title"), "document.title"),
            description=_required_text(
                item.get("description"),
                "document.description",
            ),
            path=_resolve_corpus_file(
                item.get("path"),
                repository_root=repository_root,
            ),
        )
    return documents


# Parse all dataset entries and preserve their registry-owned row limits.
def _parse_datasets(
    payload: object,
    *,
    repository_root: Path,
) -> dict[str, ControlledDatasetDefinition]:
    if not isinstance(payload, list):
        raise ValueError("Controlled corpus datasets must be a JSON array.")
    datasets: dict[str, ControlledDatasetDefinition] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each controlled dataset entry must be a JSON object.")
        dataset_id = _required_text(item.get("dataset_id"), "dataset_id")
        if dataset_id in datasets:
            raise ValueError(f"Controlled dataset ID is duplicated: {dataset_id}")
        max_rows = item.get("max_rows", 50)
        if not isinstance(max_rows, int) or not 1 <= max_rows <= 500:
            raise ValueError("Controlled dataset max_rows must be an integer from 1 to 500.")
        datasets[dataset_id] = ControlledDatasetDefinition(
            dataset_id=dataset_id,
            title=_required_text(item.get("title"), "dataset.title"),
            description=_required_text(
                item.get("description"),
                "dataset.description",
            ),
            schema_version=_required_text(
                item.get("schema_version"),
                "dataset.schema_version",
            ),
            path=_resolve_corpus_file(
                item.get("path"),
                repository_root=repository_root,
            ),
            max_rows=max_rows,
        )
    return datasets


# Resolve only repository-owned evaluation files and reject traversal or arbitrary paths.
def _resolve_corpus_file(
    value: object,
    *,
    repository_root: Path,
) -> Path:
    relative_path = Path(_required_text(value, "resource.path"))
    if relative_path.is_absolute():
        raise ValueError("Controlled corpus resource paths must be relative.")
    corpus_root = (repository_root / "evaluation_corpus").resolve()
    candidate = (repository_root / relative_path).resolve()
    if corpus_root not in candidate.parents:
        raise ValueError("Controlled corpus resource path is outside evaluation_corpus.")
    if not candidate.is_file():
        raise ValueError(f"Controlled corpus resource does not exist: {relative_path}")
    return candidate


# Require meaningful string configuration values at the administrative boundary.
def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Controlled corpus configuration requires non-empty {field_name}.")
    return value.strip()


__all__ = [
    "get_default_controlled_corpus_config_path",
    "load_controlled_corpus_config",
]
