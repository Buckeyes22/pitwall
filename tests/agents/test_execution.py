"""Recursive execution and version authority tests."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

from pitwall.agents import (
    execution,
)


class ExecutionDescriptorTests(unittest.TestCase):
    def test_artifact_descriptor_uses_the_absolute_running_interpreter(self) -> None:
        descriptor = execution.child_execution({})
        self.assertEqual("artifact", descriptor.mode)
        self.assertEqual((os.path.abspath(sys.executable), "-m", "pitwall.agents"), descriptor.argv)

    def test_artifact_version_comes_from_distribution_metadata(self) -> None:
        with mock.patch.object(execution.metadata, "version", return_value="0.12.0") as version:
            self.assertEqual("0.12.0", execution.distribution_version({}))
        version.assert_called_once_with("pitwall")


if __name__ == "__main__":
    unittest.main()
