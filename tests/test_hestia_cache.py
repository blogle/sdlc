"""The reusable workflow is the sole CI stage definition."""

from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class SharedCiContractTests(unittest.TestCase):
    def test_reusable_workflow_defines_fast_verify_and_policy_jobs(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("workflow_call:", workflow)
        self.assertIn("merge_group:", workflow)
        self.assertIn("checks.x86_64-linux.sdlc-fast", workflow)
        self.assertIn("checks.x86_64-linux.sdlc-verify", workflow)
        self.assertIn("nix run .#sdlc -- install --check", workflow)

    def test_full_verification_runs_only_for_merge_queue_candidates(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        verify = workflow.split("  verify:", 1)[1].split("  policy:", 1)[0]
        self.assertIn("github.event_name == 'merge_group'", verify)
        self.assertIn("mergify/merge-queue/", verify)

    def test_mergify_queues_after_fast_check_and_merges_after_verify(self):
        policy = (ROOT / ".mergify.yml").read_text()
        self.assertIn('check-success = "sdlc / check"', policy)
        self.assertIn('check-success = "sdlc / verify"', policy)
        self.assertIn("label != release", policy)
