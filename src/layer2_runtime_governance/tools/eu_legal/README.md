# EU legal capability

This package owns source-specific URL recognition, anti-bot detection, CELLAR
fallback retrieval, and evidence support hints for official EU legal documents.
The generic HTTP adapter invokes its public fallback function but does not
classify EU sources itself. Add further EU source behavior here and return only
typed `ToolExecutionResult` / `ToolObservation` contracts.
