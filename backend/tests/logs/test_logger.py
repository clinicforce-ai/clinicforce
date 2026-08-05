"""Unit tests for centralized ClinicForce logging."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.logs import logger as logging_helpers  # noqa: E402


class LoggerTests(unittest.TestCase):
    def test_log_info_includes_component_and_arguments(self) -> None:
        with patch.object(logging_helpers.logger, "info") as info:
            logging_helpers.log_info("Ingestion", "Processed %d resources", 2)

        info.assert_called_once_with("🟢 [%s]: Processed %d resources", "Ingestion", 2)

    def test_log_warning_includes_component_and_arguments(self) -> None:
        with patch.object(logging_helpers.logger, "warning") as warning:
            logging_helpers.log_warning("Secure Sharing", "Invalid credentials for %s", "manifest-1")

        warning.assert_called_once_with(
            "❌ [%s]: Invalid credentials for %s", "Secure Sharing", "manifest-1"
        )

    def test_log_error_includes_the_active_traceback(self) -> None:
        with patch.object(logging_helpers.logger, "error") as error:
            try:
                raise ValueError("unavailable")
            except ValueError as exc:
                logging_helpers.log_error("Database", exc)

        template, component, cause, traceback_text = error.call_args.args
        self.assertEqual(template, "❌ [%s]: %s\n%s")
        self.assertEqual(component, "Database")
        self.assertEqual(str(cause), "unavailable")
        self.assertIn("ValueError: unavailable", traceback_text)
