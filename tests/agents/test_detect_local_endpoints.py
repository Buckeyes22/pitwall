"""Security contracts for the local-endpoint discovery helper."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[2] / "tools" / "agents" / "detect_local_endpoints.py"
SPEC = importlib.util.spec_from_file_location("detect_local_endpoints", TOOL)
assert SPEC is not None and SPEC.loader is not None
detect_local_endpoints = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = detect_local_endpoints
SPEC.loader.exec_module(detect_local_endpoints)


class DetectLocalEndpointsTests(unittest.TestCase):
    def test_missing_key_warning_never_echoes_the_caller_supplied_name(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(detect_local_endpoints, "KNOWN_PORTS", {}),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = detect_local_endpoints.main(
                ["--host", "192.0.2.1", "--api-key-env", "PRIVATE_PASSWORD_ENV"]
            )

        self.assertEqual(1, result)
        self.assertNotIn("PRIVATE_PASSWORD_ENV", stdout.getvalue() + stderr.getvalue())
        self.assertIn("requested API-key environment variable is not set", stderr.getvalue())

    def test_attach_suggestions_use_a_public_placeholder(self) -> None:
        endpoint = detect_local_endpoints.Endpoint(
            url="http://127.0.0.1:8000/v1",
            port=8000,
            auth="required",
            models=["org/model"],
        )

        suggestions = detect_local_endpoints._suggest(endpoint, authenticated=True)

        self.assertTrue(all("YOUR_KEY_ENV" in line for line in suggestions[:2]))


if __name__ == "__main__":
    unittest.main()
