from typing import Any


class ActorExecutionError(RuntimeError):
    """Raised when an actor provider cannot produce a valid execution outcome."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "protocol_error",
        error_category: str = "protocol",
        recoverable: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.error_category = error_category
        self.recoverable = recoverable
        self.details = details if details is not None else {}


__all__ = ["ActorExecutionError"]
