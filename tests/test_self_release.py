"""The SDLC repo must exercise its published release interface itself."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SelfReleaseDogfoodTests(unittest.TestCase):
    def test_self_release_calls_shared_workflow_without_custom_release_logic(self):
        source = (ROOT / ".github/workflows/self-release.yml").read_text()
        self.assertIn("push:\n    branches: [main]", source)
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("uses: ./.github/workflows/release.yml", source)
        self.assertIn("secrets: inherit", source)
        self.assertIn("publish_version: ${{ inputs.publish_version || '' }}", source)
        self.assertNotIn("git push", source)
        self.assertNotIn("gh release create", source)

    def test_self_release_publishes_existing_tag_without_rebuild(self):
        recipe = (ROOT / "justfile").read_text().split("release-publish version:", 1)
        self.assertEqual(len(recipe), 2, "SDLC must provide its consumer release-publish hook")
        content = recipe[1]
        self.assertIn('git rev-parse --verify "refs/tags/v{{version}}"', content)
        self.assertIn('gh release view "v{{version}}"', content)
        self.assertIn('gh release create "v{{version}}" --verify-tag', content)
        self.assertNotIn("nix build", content)
        self.assertNotIn("cargo build", content)
