"""Stable application entry point and test-patch compatibility façade."""

from __future__ import annotations

import sys

from app.api import application as _application

# Existing deployments and tests import and patch ``app.main``.  Alias this
# module object to the implementation so those seams stay live during the
# incremental router and service extraction.
sys.modules[__name__] = _application
setattr(sys.modules[__package__], "main", _application)
