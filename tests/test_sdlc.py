import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("sdlc", Path(__file__).parents[1] / "src/sdlc.py")
sdlc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sdlc)


class ContractTests(unittest.TestCase):
    def test_contract_and_stage_plan(self):
        contract = sdlc.load_contract(Path(__file__).parents[1] / "examples/minimal/ci.nix.json")
        self.assertEqual(sdlc.plan(contract, "candidate"), [("nix", ["build", ".#checks.x86_64-linux.candidate"])])

    def test_unknown_schema_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ci.json"
            path.write_text(json.dumps({"schemaVersion": 2, "stages": {}}))
            with self.assertRaises(ValueError):
                sdlc.load_contract(path)


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
                sdlc.changelog("finalize")
                self.assertIn("Unreleased (minor)", (Path(temp) / "CHANGELOG.md").read_text())
                self.assertFalse(frag.exists())
            finally:
                sdlc.ROOT = root


if __name__ == "__main__":
    unittest.main()
