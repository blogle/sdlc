import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from src import release_gate, sdlc


PUBLISHER_SPEC = importlib.util.spec_from_file_location("release_publisher", Path(__file__).parents[1] / "src/release_publisher.py")
publisher = importlib.util.module_from_spec(PUBLISHER_SPEC)
PUBLISHER_SPEC.loader.exec_module(publisher)


class ReleaseIntegrationTests(unittest.TestCase):
    def test_historical_snapshot_replay_uses_only_reachable_version_tags(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            changes = root / ".changes"
            changes.mkdir()
            (changes / "feature.json").write_text(
                '{"semver":"minor","summary":"feature","type":"feature"}\n'
            )
            self._init_git(root)
            self._git(temp, "add", ".")
            self._git(temp, "commit", "-qm", "source without tags")
            source = self._git(temp, "rev-parse", "HEAD")

            old_root = sdlc.ROOT
            sdlc.ROOT = root
            try:
                with self._quiet():
                    original = sdlc.release_snapshot(source)
                self.assertEqual(original["version"], "0.1.0")
                self._git(temp, "add", "CHANGELOG.md", ".sdlc/release.json", ".changes")
                self._git(temp, "commit", "-qm", "release candidate")
                candidate = self._git(temp, "rev-parse", "HEAD")
                self._git(temp, "tag", "-a", "v0.1.0", "-m", "published release", candidate)

                self._git(temp, "switch", "-c", "unrelated", source)
                (root / "unrelated.txt").write_text("unrelated history\n")
                self._git(temp, "add", "unrelated.txt")
                self._git(temp, "commit", "-qm", "unrelated release history")
                unrelated = self._git(temp, "rev-parse", "HEAD")
                self._git(temp, "tag", "-a", "v9.8.7", "-m", "unrelated version", unrelated)
                self._git(temp, "switch", "--detach", source)

                def git_temp(*args, raw=False):
                    try:
                        result = subprocess.run(
                            ["git", *args], cwd=temp, check=True, capture_output=True
                        )
                    except subprocess.CalledProcessError as exc:
                        raise release_gate.GateError(str(exc)) from exc
                    return result.stdout if raw else result.stdout.decode().strip()

                replay = release_gate.replay_snapshot(source, git=git_temp)
                self.assertEqual(replay, original)
                self.assertEqual(replay["version"], "0.1.0")
            finally:
                sdlc.ROOT = old_root

    def test_reachable_prior_annotated_tag_influences_snapshot_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._init_git(root)
            (root / "base.txt").write_text("base\n")
            self._git(temp, "add", "base.txt")
            self._git(temp, "commit", "-qm", "base")
            prior = self._git(temp, "rev-parse", "HEAD")
            self._git(temp, "tag", "-a", "v2.3.0", "-m", "prior release", prior)
            changes = root / ".changes"
            changes.mkdir()
            (changes / "fix.json").write_text(
                '{"semver":"patch","summary":"fix","type":"fix"}\n'
            )
            self._git(temp, "add", ".changes/fix.json")
            self._git(temp, "commit", "-qm", "source after prior release")
            source = self._git(temp, "rev-parse", "HEAD")

            old_root = sdlc.ROOT
            sdlc.ROOT = root
            try:
                with self._quiet():
                    manifest = sdlc.release_snapshot(source, date="2026-11-12")
                self.assertEqual(manifest["version"], "2.3.1")
            finally:
                sdlc.ROOT = old_root

    @staticmethod
    def _init_git(root):
        ReleaseIntegrationTests._git(root, "init", "-q")
        ReleaseIntegrationTests._git(root, "config", "user.name", "integration")
        ReleaseIntegrationTests._git(root, "config", "user.email", "integration@example.com")

    def test_generator_gate_and_publisher_replay_one_merged_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            changes = root / ".changes"
            changes.mkdir()
            (changes / "feature.json").write_text('{"semver":"minor","summary":"feature","type":"feature"}\n')
            self._git(temp, "init", "-q")
            self._git(temp, "config", "user.name", "integration")
            self._git(temp, "config", "user.email", "integration@example.com")
            self._git(temp, "add", ".")
            self._git(temp, "commit", "-qm", "source")
            source = self._git(temp, "rev-parse", "HEAD")

            old_root = sdlc.ROOT
            sdlc.ROOT = root
            try:
                with self._quiet():
                    manifest = sdlc.release_snapshot(source)
                self._git(temp, "add", "CHANGELOG.md", ".sdlc/release.json", ".changes")
                self._git(temp, "commit", "-qm", "release candidate")
                release_sha = self._git(temp, "rev-parse", "HEAD")

                def git_temp(*args, raw=False):
                    try:
                        result = subprocess.run(["git", *args], cwd=temp, check=True, capture_output=True)
                    except subprocess.CalledProcessError as exc:
                        raise release_gate.GateError(str(exc)) from exc
                    return result.stdout if raw else result.stdout.decode().strip()

                replay = release_gate.replay_snapshot(source, git=git_temp)
                self.assertEqual(replay, manifest)
                release_gate.verify_candidate(source, release_sha, manifest, git=git_temp, planner=lambda _: replay)
                result = subprocess.run(
                    ["python3", str(Path(__file__).parents[1] / "src/release_publisher.py"), release_sha],
                    cwd=temp, check=True, capture_output=True, text=True,
                )
                self.assertEqual(json.loads(result.stdout)["commit"], release_sha)
            finally:
                sdlc.ROOT = old_root

    @staticmethod
    def _git(cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    @staticmethod
    def _quiet():
        import contextlib
        import io
        return contextlib.redirect_stdout(io.StringIO())


if __name__ == "__main__":
    unittest.main()
