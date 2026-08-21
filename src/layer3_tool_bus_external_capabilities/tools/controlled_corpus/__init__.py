"""Read-only controlled documents and datasets used by bounded evaluations."""

from .adapter import ControlledCorpusAdapter
from .contracts import (
    ControlledCorpusConfig,
    ControlledDatasetDefinition,
    ControlledDocumentDefinition,
)
from .registry import (
    get_default_controlled_corpus_config_path,
    load_controlled_corpus_config,
)

__all__ = [
    "ControlledCorpusAdapter",
    "ControlledCorpusConfig",
    "ControlledDatasetDefinition",
    "ControlledDocumentDefinition",
    "get_default_controlled_corpus_config_path",
    "load_controlled_corpus_config",
]
