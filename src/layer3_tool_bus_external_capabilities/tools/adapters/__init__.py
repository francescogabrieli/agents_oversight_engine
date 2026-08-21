"""Concrete backend adapters hidden behind the governed Tool Gateway."""

from ..controlled_corpus import ControlledCorpusAdapter
from .http_fetch import HttpFetchAdapter
from .mcp import McpToolAdapter
from .openai_search import OpenAIWebSearchAdapter

__all__ = [
    "ControlledCorpusAdapter",
    "HttpFetchAdapter",
    "McpToolAdapter",
    "OpenAIWebSearchAdapter",
]
