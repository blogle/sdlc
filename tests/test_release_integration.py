import importlib.util
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

from src import release_gate, sdlc


PUBLISHER_SPEC = importlib.util.spec_from_file_location("release_publisher", Path(__file__).parents[1] / "src/release_publisher.py")
publisher = importlib.util.module_from_spec(PUBLISHER_SPEC)
PUBLISHER_SPEC.loader.exec_module(publisher)


class ReleaseIntegrationTests(unittest.TestCase):
    def test_generator_gate_and_publisher_replay_one_merged_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            changes = root / ".changes"
            changes.mkdir()
            (changes / "feature.json").write_text('{"semver":"minor","summary":"feature","type":"feature"}\n')
            source_root = Path(__file__).parents[1]
            (root / "src").mkdir()
            shutil.copy(source_root / "src/sdlc.py", root / "src/sdlc.py")
            shutil.copytree(source_root / "actions/repository-policy", root / "actions/repository-policy")
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
