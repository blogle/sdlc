import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import legacy_oracle

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(legacy_oracle.LEGACY / "src"))
import release_reconcile


class ReleaseReconcileTests(unittest.TestCase):
    def test_uses_the_exposed_ruleset_check_before_any_reconcile_mutation(self):
        with (
            patch.dict(os.environ, {"SDLC_DEFAULT_BRANCH": "main"}),
            patch.object(release_reconcile.sdlc, "ruleset_command") as ruleset,
            patch.object(release_reconcile, "_gh"),
            patch.object(release_reconcile, "_run", side_effect=["a" * 40, "", ""]),
            patch.object(release_reconcile, "_has_fragments", return_value=False),
            patch.object(release_reconcile, "_retire"),
            patch.object(release_reconcile.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")),
        ):
            self.assertEqual(release_reconcile.reconcile("blogle/example"), 0)
        ruleset.assert_called_once_with("check", "blogle/example")


if __name__ == "__main__":
    unittest.main()
