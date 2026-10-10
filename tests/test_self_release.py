from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SelfReleaseDogfoodTests(unittest.TestCase):
    def test_main_push_has_one_reusable_reconciliation_caller(self):
        caller = (ROOT / ".github/workflows/self-release.yml").read_text()
        reconcile = (ROOT / ".github/workflows/release-reconcile.yml").read_text()
        self.assertIn("push:\n    branches: [main]", caller)
        self.assertIn("uses: ./.github/workflows/release.yml", caller)
        self.assertIn('merged_sha: ""', caller)
        self.assertIn("secrets: inherit", caller)
        self.assertNotIn("git push", caller)
        self.assertNotIn("gh release", caller)
        self.assertNotIn("  push:\n", reconcile)

    def test_integration_branch_has_no_canary_fragment_before_publication_activation(self):
        self.assertFalse((ROOT / ".changes/self-release-dogfood.json").exists())

    def test_reusable_orchestration_has_no_retired_publish_input(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertNotIn("publish_version", workflow)
        self.assertIn("if: inputs.merged_sha == ''", workflow)
        self.assertIn("if: inputs.merged_sha != ''", workflow)

    def test_only_leaf_reconciler_owns_recovery_serialization(self):
        caller = (ROOT / ".github/workflows/release.yml").read_text()
        leaf = (ROOT / ".github/workflows/release-reconcile.yml").read_text()
        publisher = (ROOT / ".github/workflows/release-publish.yml").read_text()
        self.assertNotIn("sdlc-release-reconcile-${{ github.repository }}", caller)
        self.assertIn("sdlc-release-reconcile-${{ github.repository }}", leaf)
        self.assertIn("cancel-in-progress: false", leaf)
        self.assertNotIn("cancel-in-progress: true", leaf)
        self.assertIn("sdlc-release-publish-${{ github.repository }}", publisher)


if __name__ == "__main__":
    unittest.main()
