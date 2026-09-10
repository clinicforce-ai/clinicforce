"""Centralized, safe logging for ClinicForce application events."""

from __future__ import annotations

import logging
import traceback
from typing import Any


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
# HTTPX includes query parameters in its INFO message. FHIR patient searches
# contain demographics, so only warnings/errors may reach application logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("clinicforce")


def log_info(component: str, message: str, *args: Any) -> None:
    """Record a successful application event."""
    logger.info("🟢 [%s]: " + message, component, *args)


def log_error(component: str, cause: Exception) -> None:
    """Record a failed integration without adding request data to the message."""
    logger.error("❌ [%s]: %s\n%s", component, cause, traceback.format_exc())


def log_warning(component: str, message: str, *args: Any) -> None:
    """Record a non-fatal application warning."""
    logger.warning("❌ [%s]: " + message, component, *args)
