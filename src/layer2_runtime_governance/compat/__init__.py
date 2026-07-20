"""Temporary migration aliases for retired Layer 2 import paths.

Import directly from the owning capability package instead. This namespace will
be removed after external callers have completed their migration.
"""

import warnings


# Keep the migration boundary visible to callers without affecting normal runtime paths.
warnings.warn(
    "layer2_runtime_governance.compat is temporary; import from the owning capability package.",
    DeprecationWarning,
    stacklevel=2,
)
