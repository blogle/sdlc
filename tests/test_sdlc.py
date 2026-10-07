import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

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

    def test_finalize_consumes_only_selected_merge_fragments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = sdlc.ROOT
            sdlc.ROOT = Path(temp)
            try:
                changes = Path(temp) / ".changes"
                changes.mkdir()
                first = changes / "first.json"
                second = changes / "second.json"
                first.write_text(json.dumps({"type": "feature", "semver": "minor", "summary": "first PR"}))
                second.write_text(json.dumps({"type": "fix", "semver": "patch", "summary": "second PR"}))
                sdlc.changelog("finalize", version="0.1.0", date="2026-10-05", selected=[".changes/first.json"])
                self.assertFalse(first.exists())
                self.assertTrue(second.exists())
                changelog = (Path(temp) / "CHANGELOG.md").read_text()
                self.assertIn("first PR", changelog)
                self.assertNotIn("second PR", changelog)
            finally:
                sdlc.ROOT = root

    def test_plan_and_finalize_are_scoped_to_one_pr_and_reject_stale_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = sdlc.ROOT
            sdlc.ROOT = Path(temp)
            try:
                changes = Path(temp) / ".changes"
                changes.mkdir()
                first = changes / "first.json"
                second = changes / "second.json"
                first.write_text(json.dumps({"type": "feature", "semver": "minor", "summary": "first PR"}))
                second.write_text(json.dumps({"type": "breaking", "semver": "major", "summary": "second PR"}))
                output = io.StringIO()
                with redirect_stdout(output):
                    sdlc.changelog("plan", json_output=True, selected=[".changes/first.json"])
                self.assertEqual(json.loads(output.getvalue()), {"bump": "minor", "release": True, "version": "0.1.0"})
                with self.assertRaises(ValueError):
                    sdlc.fragments([".changes/already-consumed.json"])
            finally:
                sdlc.ROOT = root


class ConsumerWorkflowTests(unittest.TestCase):
    def test_stable_required_checks_are_local_always_gates(self):
        workflow = (Path(__file__).parents[1] / "examples/minimal/.github/workflows/ci.yml").read_text()
        self.assertIn("name: sdlc / pr-fast", workflow)
        self.assertIn("needs: pr-fast", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn('test \"${{ needs.pr-fast.result }}\" = success', workflow)
        self.assertIn("name: sdlc / candidate", workflow)
        self.assertIn("needs: candidate", workflow)
        self.assertIn("if: always() && (github.event_name == 'merge_group' || startsWith(github.event.pull_request.head.ref, 'mergify/merge-queue/'))", workflow)
        self.assertIn('test \"${{ needs.candidate.result }}\" = success', workflow)

if __name__ == "__main__":
    unittest.main()
