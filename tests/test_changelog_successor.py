"""Real Git regression for successor changelog generation and fragment consumption."""

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout

SDLC_PATH = Path(__file__).resolve().parents[1] / "src" / "sdlc.py"
SPEC = importlib.util.spec_from_file_location("sdlc_successor_canary", SDLC_PATH)
sdlc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sdlc)


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


class ChangelogSuccessorTests(unittest.TestCase):
    def test_successor_aggregates_two_patch_fragments_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git(root, "init", "-q")
            git(root, "config", "user.name", "release test")
            git(root, "config", "user.email", "release-test@example.com")

            original_history = (
                "# Changelog\n\n"
                "## [1.2.0] - 2026-10-10\n\n"
                "- **feature**: Previously published release\n"
            )
            (root / "CHANGELOG.md").write_text(original_history)
            manifest_dir = root / ".sdlc"
            manifest_dir.mkdir()
            prior_source = "a" * 40
            (manifest_dir / "release.json").write_text(
                json.dumps(
                    {"schema": 1, "version": "1.2.0",
                     "source_main_sha": prior_source}
                ) + "\n"
            )
            changes = root / ".changes"
            changes.mkdir()
            entries = (
                ("b-fragment-docs.json", "docs",
                 "Explain changelog fragment authoring and consumption"),
                ("a-history-test.json", "test",
                 "Verify successor changelog history and multi-fragment output"),
            )
            for name, kind, summary in entries:
                (changes / name).write_text(
                    json.dumps(
                        {"type": kind, "semver": "patch", "summary": summary},
                        sort_keys=True,
                    ) + "\n"
                )
            git(root, "add", ".")
            git(root, "commit", "-qm", "two patch fragments after v1.2.0")
            source = git(root, "rev-parse", "HEAD")

            original_root = sdlc.ROOT
            try:
                sdlc.ROOT = root
                with redirect_stdout(io.StringIO()):
                    manifest = sdlc.release_snapshot(
                        source, date="2026-10-11"
                    )
                self.assertEqual(manifest["version"], "1.2.1")
                self.assertEqual(manifest["prior_released_boundary"], prior_source)
                self.assertEqual(manifest["source_main_sha"], source)
                self.assertEqual(
                    [item["path"] for item in manifest["fragments"]],
                    [".changes/a-history-test.json",
                     ".changes/b-fragment-docs.json"],
                )
                self.assertFalse(list(changes.glob("*.json")))

                result = (root / "CHANGELOG.md").read_text()
                new_heading = "## [1.2.1] - 2026-10-11"
                old_heading = "## [1.2.0] - 2026-10-10"
                self.assertEqual(result.count(new_heading), 1)
                self.assertEqual(result.count(old_heading), 1)
                self.assertLess(result.index(new_heading), result.index(old_heading))
                self.assertLess(
                    result.index("**test**: Verify successor changelog"),
                    result.index("**docs**: Explain changelog"),
                )
                self.assertIn("- **feature**: Previously published release", result)
                self.assertEqual(
                    json.loads((manifest_dir / "release.json").read_text()),
                    manifest,
                )

                git(root, "add", "-A")
                git(root, "commit", "-qm", "generated successor release")
                generated = git(root, "rev-parse", "HEAD")
                self.assertTrue(sdlc.verify_release_tree(source, generated, manifest))
            finally:
                sdlc.ROOT = original_root


if __name__ == "__main__":
    unittest.main()
