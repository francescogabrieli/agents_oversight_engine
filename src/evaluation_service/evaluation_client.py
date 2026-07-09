from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class OPAEvaluationClient:
    # OPAEvaluationClient is a small HTTP adapter for the offline evaluation policy.
    base_url: str = "http://127.0.0.1:8181"
    decision_path: str = "/v1/data/layer2/evaluation/compliance"
    timeout_seconds: float = 5.0

    # Evaluate one runtime-independent EvaluationInput against OPA.
    def evaluate(self, *, evaluation_input: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self.base_url.rstrip("/") + "/" + self.decision_path.lstrip("/"),
            data=json.dumps({"input": evaluation_input}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:  # pragma: no cover
            raise RuntimeError(f"OPA evaluation failed with HTTP {exc.code}: {exc.reason}") from exc
        except URLError as exc:  # pragma: no cover
            raise RuntimeError(f"OPA evaluation failed: {exc.reason}") from exc
        parsed = json.loads(raw_body)
        result = parsed.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("OPA evaluation response did not contain an object result.")
        return result
