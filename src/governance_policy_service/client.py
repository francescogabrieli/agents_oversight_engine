from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class _HttpResponse(Protocol):
    # Describe only the response surface needed by the service client.
    def read(self) -> bytes: ...

    # Support urllib responses and lightweight test doubles as context managers.
    def __enter__(self) -> "_HttpResponse": ...

    # Preserve the standard context-manager exit contract.
    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: ...


HttpOpener = Callable[..., _HttpResponse]


@dataclass(frozen=True)
class OPAClientSettings:
    # Keep network concerns outside the LangGraph governance domain.
    base_url: str
    timeout_seconds: float = 5.0


class PolicyServiceError(RuntimeError):
    """Raised when the remote policy decision point violates its HTTP contract."""


class OPADecisionClient:
    # The client is JSON-only and has no dependency on runtime state or dataclasses.
    def __init__(
        self,
        *,
        settings: OPAClientSettings,
        opener: HttpOpener = urlopen,
    ) -> None:
        self._settings = settings
        self._opener = opener

    # Evaluate one OPA data path and unwrap its standard `result` envelope.
    def evaluate(self, *, path: str, input_document: dict[str, Any]) -> Any:
        request = Request(
            self._build_url(path),
            data=json.dumps({"input": input_document}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._settings.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:  # pragma: no cover - exercised by integration tests.
            raise PolicyServiceError(
                f"Policy service request failed with HTTP {exc.code}: {exc.reason}"
            ) from exc
        except URLError as exc:  # pragma: no cover - exercised by integration tests.
            raise PolicyServiceError(f"Policy service request failed: {exc.reason}") from exc
        except Exception as exc:  # pragma: no cover - defensive transport boundary.
            raise PolicyServiceError(f"Policy service request failed: {exc}") from exc

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise PolicyServiceError(f"Policy service returned invalid JSON: {exc}") from exc
        if not isinstance(payload, dict) or "result" not in payload:
            raise PolicyServiceError("Policy service response did not contain a result field.")
        return payload["result"]

    # Compose a stable endpoint URL while accepting configured paths with or without a slash.
    def _build_url(self, path: str) -> str:
        if not path.strip():
            raise ValueError("Policy service path must not be empty.")
        return self._settings.base_url.rstrip("/") + "/" + path.lstrip("/")


__all__ = [
    "OPAClientSettings",
    "OPADecisionClient",
    "PolicyServiceError",
]
