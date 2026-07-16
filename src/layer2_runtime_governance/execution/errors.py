class ActorExecutionError(RuntimeError):
    """Raised when an actor provider cannot produce a valid execution outcome."""


__all__ = ["ActorExecutionError"]
