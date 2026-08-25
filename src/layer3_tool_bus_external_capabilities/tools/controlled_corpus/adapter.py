from __future__ import annotations

import csv
import json
from io import StringIO
from pathlib import Path

from ..contracts import (
    RetrievalKind,
    ToolErrorCode,
    ToolExecutionResult,
    ToolName,
    ToolObservation,
    ToolRequest,
)
from ..models import ToolDefinition
from ..text import truncate_text
from .contracts import ControlledCorpusConfig, ControlledDatasetDefinition
from .registry import load_controlled_corpus_config


class ControlledCorpusAdapter:
    # Serve only configuration-registered documents and CSV snapshots.

    # Select either an injected immutable registry or the default configuration-owned registry.
    def __init__(
        self,
        *,
        config: ControlledCorpusConfig | None = None,
        config_path: Path | None = None,
    ) -> None:
        if config is not None and config_path is not None:
            raise ValueError("Provide a controlled corpus config or config_path, not both.")
        self._config = config or load_controlled_corpus_config(config_path=config_path)

    # Dispatch a local-runtime request without allowing a raw filesystem operation.
    def execute(
        self,
        *,
        tool_request: ToolRequest,
        definition: ToolDefinition,
    ) -> ToolExecutionResult:
        if tool_request.tool_name == ToolName.CONTROLLED_DOCUMENT_READ:
            return self._read_document(tool_request=tool_request, definition=definition)
        if tool_request.tool_name == ToolName.CONTROLLED_DATASET_READ:
            return self._read_dataset(tool_request=tool_request, definition=definition)
        return _failure(
            tool_request,
            "The requested local runtime tool is not implemented by the controlled corpus.",
            ToolErrorCode.UNAVAILABLE,
        )

    # Return a bounded, explicitly labelled controlled document as concrete evidence.
    def _read_document(
        self,
        *,
        tool_request: ToolRequest,
        definition: ToolDefinition,
    ) -> ToolExecutionResult:
        document_id = tool_request.document_id or ""
        document = self._config.documents.get(document_id)
        if document is None:
            return _failure(
                tool_request,
                "The requested controlled document is not registered.",
                ToolErrorCode.FORBIDDEN,
            )
        try:
            text, byte_truncated = _read_bounded_utf8(
                document.path,
                max_bytes=definition.max_bytes,
            )
        except OSError as exc:
            return _failure(
                tool_request,
                f"The controlled document could not be read ({type(exc).__name__}).",
                ToolErrorCode.UNAVAILABLE,
            )
        rendered_content = _document_content(
            document_id=document.document_id,
            title=document.title,
            text=text,
        )
        content = truncate_text(rendered_content, definition.max_content_chars)
        content_truncated = byte_truncated or len(content or "") < len(rendered_content)
        return ToolExecutionResult(
            request=tool_request,
            success=True,
            summary=f"Controlled document '{document_id}' read successfully.",
            content=content,
            observations=(
                ToolObservation(
                    observation_id=f"{tool_request.request_id}:document",
                    locator=f"controlled-document://{document_id}",
                    content=content or "",
                    retrieval_kind=RetrievalKind.CONTROLLED_RESOURCE,
                ),
            ),
            truncated=content_truncated,
            extractability_limited=content_truncated,
        )

    # Return a complete bounded CSV snapshot rather than accepting an arbitrary query language.
    def _read_dataset(
        self,
        *,
        tool_request: ToolRequest,
        definition: ToolDefinition,
    ) -> ToolExecutionResult:
        dataset_id = tool_request.dataset_id or ""
        dataset = self._config.datasets.get(dataset_id)
        if dataset is None:
            return _failure(
                tool_request,
                "The requested controlled dataset is not registered.",
                ToolErrorCode.FORBIDDEN,
            )
        try:
            text, byte_truncated = _read_bounded_utf8(
                dataset.path,
                max_bytes=definition.max_bytes,
            )
        except OSError as exc:
            return _failure(
                tool_request,
                f"The controlled dataset could not be read ({type(exc).__name__}).",
                ToolErrorCode.UNAVAILABLE,
            )
        if byte_truncated:
            return _failure(
                tool_request,
                "The controlled dataset exceeded its configured byte limit.",
                ToolErrorCode.BYTE_LIMIT,
            )
        try:
            rows, columns = _parse_csv(text)
        except csv.Error as exc:
            return _failure(
                tool_request,
                f"The controlled dataset is not valid CSV ({type(exc).__name__}).",
                ToolErrorCode.UNAVAILABLE,
            )
        requested_rows = tool_request.limit if tool_request.limit is not None else dataset.max_rows
        if requested_rows < 1:
            return _failure(
                tool_request,
                "The controlled dataset row limit must be positive.",
                ToolErrorCode.FORBIDDEN,
            )
        content, content_truncated = _serialize_dataset(
            dataset=dataset,
            columns=columns,
            rows=rows,
            requested_rows=min(requested_rows, dataset.max_rows),
            max_content_chars=definition.max_content_chars,
        )
        return ToolExecutionResult(
            request=tool_request,
            success=True,
            summary=(
                f"Controlled dataset '{dataset_id}' returned "
                f"{json.loads(content)['returned_rows']} of {len(rows)} rows."
            ),
            content=content,
            observations=(
                ToolObservation(
                    observation_id=f"{tool_request.request_id}:dataset",
                    locator=f"controlled-dataset://{dataset_id}",
                    content=content,
                    retrieval_kind=RetrievalKind.CONTROLLED_RESOURCE,
                ),
            ),
            truncated=content_truncated,
            extractability_limited=content_truncated,
        )


# Read a small text resource with a hard byte ceiling before decoding it.
def _read_bounded_utf8(path: Path, *, max_bytes: int) -> tuple[str, bool]:
    with path.open("rb") as handle:
        raw_content = handle.read(max_bytes + 1)
    byte_truncated = len(raw_content) > max_bytes
    return raw_content[:max_bytes].decode("utf-8", errors="replace"), byte_truncated


# Label local documents so they cannot be mistaken for public documentary sources.
def _document_content(*, document_id: str, title: str, text: str) -> str:
    return (
        "Controlled evaluation document (not a public web source)\n"
        f"document_id: {document_id}\n"
        f"title: {title}\n\n"
        f"{text.strip()}"
    )


# Parse raw CSV without inferring types or altering the controlled source values.
def _parse_csv(text: str) -> tuple[list[dict[str, str]], list[str]]:
    reader = csv.DictReader(StringIO(text))
    if not reader.fieldnames:
        raise csv.Error("CSV header is missing.")
    raw_columns = list(reader.fieldnames)
    columns = [column.strip() for column in raw_columns]
    if not all(columns) or len(columns) != len(set(columns)):
        raise csv.Error("CSV header contains an empty or duplicate column.")
    rows = [
        {
            normalized_column: row.get(raw_column, "") or ""
            for raw_column, normalized_column in zip(raw_columns, columns, strict=True)
        }
        for row in reader
    ]
    return rows, columns


# Serialize a valid JSON snapshot and reduce rows, never slice JSON text mid-document.
def _serialize_dataset(
    *,
    dataset: ControlledDatasetDefinition,
    columns: list[str],
    rows: list[dict[str, str]],
    requested_rows: int,
    max_content_chars: int,
) -> tuple[str, bool]:
    selected_rows = rows[:requested_rows]
    content = _dataset_json(
        dataset=dataset,
        columns=columns,
        total_rows=len(rows),
        selected_rows=selected_rows,
    )
    while len(content) > max_content_chars and selected_rows:
        selected_rows = selected_rows[:-1]
        content = _dataset_json(
            dataset=dataset,
            columns=columns,
            total_rows=len(rows),
            selected_rows=selected_rows,
        )
    return content, len(selected_rows) < min(len(rows), requested_rows)


# Build one deterministic observation payload that retains row and schema provenance.
def _dataset_json(
    *,
    dataset: ControlledDatasetDefinition,
    columns: list[str],
    total_rows: int,
    selected_rows: list[dict[str, str]],
) -> str:
    return json.dumps(
        {
            "source_type": "controlled_evaluation_dataset",
            "dataset_id": dataset.dataset_id,
            "title": dataset.title,
            "schema_version": dataset.schema_version,
            "columns": columns,
            "total_rows": total_rows,
            "returned_rows": len(selected_rows),
            "rows_omitted": total_rows - len(selected_rows),
            "rows": selected_rows,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


# Return typed tool failures instead of leaking local filesystem errors into runtime control flow.
def _failure(
    tool_request: ToolRequest,
    message: str,
    error_code: ToolErrorCode,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        request=tool_request,
        success=False,
        summary=message,
        error_message=message,
        error_code=error_code,
    )


__all__ = ["ControlledCorpusAdapter"]
