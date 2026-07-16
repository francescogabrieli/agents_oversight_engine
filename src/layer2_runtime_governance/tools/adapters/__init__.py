"""Concrete backend adapters hidden behind the governed Tool Gateway."""

from .http_fetch import HttpFetchAdapter
from .mcp import McpToolAdapter
from .openai_search import OpenAIWebSearchAdapter

__all__ = ["HttpFetchAdapter", "McpToolAdapter", "OpenAIWebSearchAdapter"]
