import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("sdlc", Path(__file__).parents[1] / "src/sdlc.py")
sdlc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sdlc)


class ChangelogTests(unittest.TestCase):
    def test_zero_fragments_and_finalize_compacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = sdlc.ROOT
            sdlc.ROOT = Path(temp)
            try:
                (Path(temp) / ".changes").mkdir()
                sdlc.changelog("finalize")
                frag = Path(temp) / ".changes/feature.json"
                frag.write_text(json.dumps({"type": "feature", "semver": "minor", "summary": "Added a capability"}))
                sdlc.changelog("finalize", version="0.1.0", date="2026-10-03")
                self.assertIn("## [0.1.0] - 2026-10-03", (Path(temp) / "CHANGELOG.md").read_text())
                self.assertFalse(frag.exists())
            finally:
                sdlc.ROOT = root

    def test_aggregate_bump_is_max_semver(self):
        entries = [(Path("patch.json"), {"type": "fix", "semver": "patch", "summary": "x"}), (Path("major.json"), {"type": "breaking", "semver": "major", "summary": "y"})]
        self.assertEqual(sdlc.bump_intent(entries), "major")


if __name__ == "__main__":
    unittest.main()
