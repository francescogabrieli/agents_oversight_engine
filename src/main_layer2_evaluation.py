from __future__ import annotations

import warnings

from evaluation_service.main import main as _evaluation_service_main


# Retain the historical entrypoint without importing the live Layer 2 runtime.
def main() -> int:
    warnings.warn(
        "main_layer2_evaluation.py is deprecated; use python -m evaluation_service.main.",
        DeprecationWarning,
        stacklevel=2,
    )
    _evaluation_service_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
