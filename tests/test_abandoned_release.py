"""Exact committed retirement, draft exception, and successor on real Git trees."""
import json
from argparse import Namespace
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import abandoned_release as retirement
import release_reconcile
import release_publisher
import release_gate
import sdlc


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


class AbandonedReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        old_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, old_cwd)
        old_root = sdlc.ROOT
        sdlc.ROOT = self.root
        self.addCleanup(setattr, sdlc, "ROOT", old_root)
        git("init", "-q")
        git("config", "user.name", "release test")
        git("config", "user.email", "test@example.com")
        (self.root / "CHANGELOG.md").write_text("# Changelog\n")
        (self.root / ".changes").mkdir()
        (self.root / ".changes/first.json").write_text('{"type":"feature","semver":"minor","summary":"Initial feature"}\n')
        git("add", ".")
        git("commit", "-qm", "initial source")
        self.source = git("rev-parse", "HEAD")
        self.manifest = sdlc.release_snapshot(self.source, date="2026-11-01")
        self.assertEqual(self.manifest["version"], "0.1.0")
        git("add", "-A")
        git("commit", "-qm", "draft predecessor")
        self.merged = git("rev-parse", "HEAD")
        git("tag", "-a", "v0.1.0", "-m", "draft")
        (self.root / ".changes/next.json").write_text('{"type":"fix","semver":"patch","summary":"Successor fix"}\n')
        git("add", ".")
        git("commit", "-qm", "successor fragment")
        self.unmarked = git("rev-parse", "HEAD")
        self.marker = {"schema": 1, "version": "0.1.0", "source_main_sha": self.source,
                       "merged_sha": self.merged, "reason": "Publication interrupted after tag and draft creation"}
        self.state = {"isDraft": True, "isPrerelease": False, "assets": []}

    def mark(self, marker=None):
        path = self.root / retirement.marker_path("0.1.0")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.marker if marker is None else marker) + "\n")
        git("add", ".")
        git("commit", "-qm", "audit retirement")
        return git("rev-parse", "HEAD")

    def verify(self, sha, state=None):
        with patch.object(release_reconcile, "_gh", return_value=json.dumps(self.state if state is None else state)):
            release_reconcile._verify_predecessor("owner/repo", "main", sha)

    def test_missing_or_malicious_marker_does_not_unlock_successor(self):
        with self.assertRaisesRegex(ValueError, "still draft"):
            self.verify(self.unmarked)
        for change in ({"merged_sha": "a" * 40}, {"source_main_sha": "b" * 40},
                       {"reason": ""}, {"reason": " "}, {"schema": True}, {"extra": "unreviewed"},
                       {"reason": "x" * 501}):
            with self.subTest(change=change):
                marker = self.marker | change
                sha = self.mark(marker)
                with self.assertRaisesRegex(ValueError, "retirement marker"):
                    self.verify(sha)
        sha = self.mark()
        with self.assertRaisesRegex(ValueError, "live draft"):
            self.verify(sha, {"isDraft": False})
        path = self.root / retirement.marker_path("0.1.0")
        path.write_text(path.read_text().replace('"schema": 1', '"schema": 1, "schema": 1'))
        git("add", ".")
        git("commit", "-qm", "duplicate marker field")
        with self.assertRaisesRegex(ValueError, "duplicate field"):
            self.verify(git("rev-parse", "HEAD"))

    def test_unreadable_marker_object_is_a_retirement_error(self):
        sha = self.mark()
        real_git = retirement.git

        def missing_show(*args, **kwargs):
            if args[0] == "show":
                raise subprocess.CalledProcessError(128, ["git", *args])
            return real_git(*args, **kwargs)

        with patch.object(retirement, "git", side_effect=missing_show):
            with self.assertRaisesRegex(retirement.RetirementError, "invalid retirement marker"):
                retirement.marker_at(sha, "0.1.0")

    def test_audited_draft_allows_patch_successor_but_never_replays_predecessor(self):
        sha = self.mark()
        self.verify(sha)
        with self.assertRaisesRegex(retirement.RetirementError, "abandoned"):
            retirement.reject_abandoned(sha, "0.1.0", self.source, self.merged)
        real_git = release_publisher.git

        def with_fetched_tip(*args):
            if args[0] == "fetch":
                return ""
            if args == ("rev-parse", "FETCH_HEAD"):
                return sha
            return real_git(*args)

        with patch.dict(os.environ, {"SDLC_RELEASE_MAIN_SHA": sha, "DEFAULT_BRANCH": "main"}):
            with patch.object(release_publisher, "git", side_effect=with_fetched_tip):
                with self.assertRaisesRegex(release_publisher.IdentityError, "abandoned"):
                    release_publisher.validate_checkout(self.merged)
        with patch.dict(os.environ, {"SDLC_RELEASE_MAIN_SHA": "f" * 40, "DEFAULT_BRANCH": "main"}):
            with patch.object(release_publisher, "git", side_effect=with_fetched_tip):
                with self.assertRaisesRegex(release_publisher.IdentityError, "does not match"):
                    release_publisher.validate_checkout(self.merged)
        with patch.dict(os.environ, {"SDLC_RELEASE_MAIN_SHA": sha, "DEFAULT_BRANCH": ""}):
            with patch.object(release_publisher, "git", side_effect=with_fetched_tip):
                with self.assertRaisesRegex(release_publisher.IdentityError, "SHA and name"):
                    release_publisher.validate_checkout(self.merged)
        result = sdlc.release_snapshot(sha, date="2026-11-02")
        self.assertEqual(result["version"], "0.1.1")
        self.assertEqual(result["prior_released_boundary"], self.source)
        self.assertEqual(git("rev-list", "-n1", "v0.1.0"), self.merged)
        self.assertEqual(git("show", f"{self.merged}:.sdlc/release.json"), json.dumps(self.manifest, indent=2, sort_keys=True))

    def test_tag_or_manifest_tampering_cannot_be_retired(self):
        sha = self.mark()
        git("tag", "-f", "v0.1.0", self.source)
        with self.assertRaisesRegex(ValueError, "tag does not identify"):
            self.verify(sha)
        git("tag", "-f", "v0.1.0", self.merged)
        (self.root / ".sdlc/release.json").write_text('{"version":"0.1.0","source_main_sha":"' + self.source + '"}\n')
        git("add", ".")
        git("commit", "-qm", "tamper manifest")
        with self.assertRaisesRegex(ValueError, "tag manifest differs"):
            self.verify(git("rev-parse", "HEAD"))

    def test_trusted_gate_requires_marker_and_historical_gate_refuses_replay(self):
        pr = release_gate.PullRequest(1, "a" * 40, "sdlc/release-next", "bot", "Bot", "owner/repo", "bot", "Bot")
        def api(endpoint):
            if endpoint == "repos/owner/repo":
                return {"default_branch": "main"}
            if endpoint.endswith("/commits/main"):
                return {"sha": main}
            if endpoint.endswith("/commits/" + pr.head_sha):
                return {"parents": [{"sha": self.source}]}
            if endpoint.endswith("/commits/" + self.merged):
                return {"parents": [{"sha": self.source}]}
            raise AssertionError(endpoint)

        args = Namespace(repo="owner/repo", pr=1, release_bot="bot", app_slug="bot", historical_merged_sha="")
        main = self.unmarked
        with (patch.object(release_gate, "_pull_request", return_value=pr),
              patch.object(release_gate, "_authenticated_app", return_value=("bot", "bot")),
              patch.object(release_gate, "_gh_json", side_effect=api),
              patch.object(release_gate, "_git"),
              patch.object(release_gate, "_checks", return_value=[]),
              patch.object(release_gate, "_protection", return_value={}),
              patch.object(release_reconcile, "_gh", return_value=json.dumps(self.state)),
              patch.object(release_gate, "evaluate_release_gate") as evaluate,
              patch.object(release_gate, "_manifest_at", return_value=self.manifest)):
            with self.assertRaisesRegex(ValueError, "still draft"):
                release_gate._gate_command(args)
            evaluate.assert_not_called()
            main = self.mark()
            self.assertEqual(release_gate._gate_command(args), 0)
            evaluate.assert_called_once()
            args.historical_merged_sha = self.merged
            with self.assertRaisesRegex(retirement.RetirementError, "abandoned"):
                release_gate._gate_command(args)
