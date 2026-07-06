from __future__ import annotations

import re
import time
from urllib.error import HTTPError

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential


_RETRYABLE_STATUS_CODES = {429, 503}


# Keep tenacity sleep patchable in tests without changing runtime behavior.
def _sleep(seconds: float) -> None:
    time.sleep(seconds)


# Retry only transient provider failures that are common during live demos.
def _is_retryable_llm_exception(exc: BaseException) -> bool:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and status_code in _RETRYABLE_STATUS_CODES:
        return True

    response = getattr(exc, "response", None)
    response_status_code = getattr(response, "status_code", None)
    if isinstance(response_status_code, int) and response_status_code in _RETRYABLE_STATUS_CODES:
        return True

    if isinstance(exc, HTTPError) and exc.code in _RETRYABLE_STATUS_CODES:
        return True

    message = f"{exc.__class__.__name__}: {exc}".lower()
    if "rate limit" in message:
        return True

    return re.search(r"\b(?:429|503)\b", message) is not None


llm_retry = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    retry=retry_if_exception(_is_retryable_llm_exception),
    sleep=_sleep,
)


__all__ = ["llm_retry"]
