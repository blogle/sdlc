import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from src import sdlc


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.modules["sdlc"] = sdlc
import release_publisher


def git(directory, *arguments):
    return subprocess.run(
        ["git", *arguments], cwd=directory, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


class GoDifferentialTests(unittest.TestCase):
    def test_snapshot_bytes_and_release_identity_match_python_oracle(self):
        executable = shutil.which("sdlc")
        if executable is None:
            self.skipTest("compiled sdlc binary is not on PATH")
        with tempfile.TemporaryDirectory() as temporary:
            original_directory = Path.cwd()
            source_root = Path(temporary) / "source"
            python_root = Path(temporary) / "python"
            source_root.mkdir()
            git(source_root, "init", "-q")
            git(source_root, "config", "user.name", "Parity Test")
            git(source_root, "config", "user.email", "parity@example.invalid")
            (source_root / "CHANGELOG.md").write_bytes(b"# Changelog\n")
            (source_root / ".changes").mkdir()
            (source_root / ".changes/fix.json").write_text(
                json.dumps({"type": "fix", "semver": "patch", "summary": "Unicode: café"}) + "\n"
            )
            git(source_root, "add", ".")
            git(source_root, "commit", "-qm", "source")
            source_sha = git(source_root, "rev-parse", "HEAD")
            git(source_root, "tag", "v1.2.3")
            git(source_root, "clone", "--no-hardlinks", str(source_root), str(python_root))

            date = "2026-10-26"
            result = subprocess.run(
                [executable, "release", "snapshot", "--source-sha", source_sha, "--date", date, "--json"],
                cwd=source_root, check=True, capture_output=True, text=True,
            )
            go_result = json.loads(result.stdout)
            self.assertTrue(go_result["release"])

            old_root = sdlc.ROOT
            try:
                sdlc.ROOT = python_root
                python_manifest = sdlc.release_snapshot(source_sha, date)
            finally:
                sdlc.ROOT = old_root
            self.assertEqual(go_result["manifest"], python_manifest)
            for relative in ("CHANGELOG.md", ".sdlc/release.json"):
                self.assertEqual((source_root / relative).read_bytes(), (python_root / relative).read_bytes(), relative)
            self.assertFalse((source_root / ".changes/fix.json").exists())
            self.assertFalse((python_root / ".changes/fix.json").exists())

            identities = []
            for directory in (source_root, python_root):
                git(directory, "config", "user.name", "Parity Test")
                git(directory, "config", "user.email", "parity@example.invalid")
                git(directory, "add", ".")
                subprocess.run(
                    ["git", "commit", "-qm", "release"], cwd=directory, check=True,
                    env=os.environ | {
                        "GIT_AUTHOR_DATE": "2026-10-26T12:00:00Z",
                        "GIT_COMMITTER_DATE": "2026-10-26T12:00:00Z",
                    },
                )
                merged_sha = git(directory, "rev-parse", "HEAD")
                try:
                    os.chdir(directory)
                    sdlc.ROOT = directory
                    self.assertTrue(sdlc.verify_release_tree(source_sha, merged_sha, python_manifest))
                    legacy_identity = release_publisher.validate_checkout(merged_sha)
                    expected = json.dumps(legacy_identity.__dict__, sort_keys=True, default=list)
                    actual = subprocess.run(
                        [executable, "release", "publish", merged_sha], cwd=directory,
                        check=True, capture_output=True, text=True,
                    ).stdout.strip()
                    self.assertEqual(actual, expected)
                    identities.append(actual)
                finally:
                    os.chdir(original_directory)
                    sdlc.ROOT = old_root
            self.assertEqual(identities[0], identities[1])

    def test_consumer_adapters_match_python_and_leave_json5_untouched(self):
        executable = shutil.which("sdlc")
        if executable is None:
            self.skipTest("compiled sdlc binary is not on PATH")
        revision = "a" * 40
        lock = {
            "nodes": {
                "root": {"inputs": {"sdlc": "sdlc"}},
                "sdlc": {
                    "original": {"type": "github", "owner": "blogle", "repo": "sdlc", "ref": "v2"},
                    "locked": {"rev": revision},
                },
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            go_root, python_root = Path(temporary) / "go", Path(temporary) / "python"
            go_root.mkdir()
            python_root.mkdir()
            native = "// keep formatting\n{ extends: ['github>blogle/sdlc#v1'], customManagers: [], }\n"
            for directory in (go_root, python_root):
                (directory / "flake.lock").write_text(json.dumps(lock))
                (directory / "renovate.json5").write_text(native)
            subprocess.run([executable, "install"], cwd=go_root, check=True, capture_output=True, text=True)

            old_root, old_contract = sdlc.ROOT, sdlc.CONTRACT_ROOT
            try:
                sdlc.ROOT = python_root
                sdlc.CONTRACT_ROOT = ROOT
                sdlc.install_adapters()
                sdlc.install_adapters(check=True)
            finally:
                sdlc.ROOT, sdlc.CONTRACT_ROOT = old_root, old_contract

            generated = [
                ".github/workflows/sdlc.yml", ".github/workflows/sdlc-release.yml",
                ".github/workflows/sdlc-release-gate.yml", ".github/workflows/sdlc-release-merge.yml",
                ".github/workflows/sdlc-cache-gc.yml", ".mergify.yml",
            ]
            for relative in generated:
                self.assertEqual((go_root / relative).read_bytes(), (python_root / relative).read_bytes(), relative)
            self.assertEqual((go_root / "renovate.json5").read_bytes(), native.encode())
            self.assertEqual((python_root / "renovate.json5").read_bytes(), native.encode())
            self.assertFalse((go_root / "renovate.json").exists())
            self.assertFalse((python_root / "renovate.json").exists())
            subprocess.run([executable, "install", "--check"], cwd=go_root, check=True, capture_output=True, text=True)
