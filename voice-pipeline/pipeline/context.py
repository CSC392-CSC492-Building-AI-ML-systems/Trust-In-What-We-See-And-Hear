"""The call context contract, shared with the backend.

It is defined once, in backend/app/modules/calls/context.py. That file only
uses the standard library, so the pipeline imports it rather than keeping a
copy that could drift.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import sys
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parents[2] / "backend")
if _BACKEND not in sys.path:
    sys.path.append(_BACKEND)

from app.modules.calls.context import (  # noqa: E402
    CallContext,
    CallContextValidationError,
)

__all__ = ["CallContext", "CallContextValidationError"]
