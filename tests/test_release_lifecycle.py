import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src import release_gate, release_publisher, sdlc
from src.release_gate import Check, PullRequest


GITHUB_ACTIONS_APP_ID = 15368


class StatefulReleaseApi:
    def __init__(self, repo, main_sha, pr, checks, protection, merged_sha):
        self.repo = repo
        self.app_slug = "release-bot"
        self.current_main = main_sha
        self.pr = pr
        self.current_checks = checks
        self.current_protection = protection
        self.merged_sha = merged_sha
        self.merge_result = None

    def main_sha(self):
        return self.current_main

    def pull_request(self, _number):
        return self.pr

    def checks(self, _sha):
        return self.current_checks

    def protection(self):
        return self.current_protection

    def parent_sha(self, sha):
        return subprocess.run(
            ["git", "rev-parse", f"{sha}^"], check=True, capture_output=True, text=True
        ).stdout.strip()

    def merge(self, number, *, sha, merge_method):
        if sha != self.pr.head_sha or merge_method != "squash":
            raise AssertionError("release merge lost its expected head or squash method")
        self.merge_result = {"merged": True, "sha": self.merged_sha, "number": number}
        self.current_main = self.merged_sha
        self.pr = PullRequest(
            self.pr.number, self.pr.head_sha, self.pr.head_branch, self.pr.head_login,
            self.pr.head_type, self.pr.head_repo, self.pr.author_login, self.pr.author_type,
        )
        return self.merge_result


class ReleaseLifecycleTests(unittest.TestCase):
    def git(self, root, *args, **kwargs):
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, **kwargs).stdout.strip()

    def test_candidate_gate_merge_and_immutable_publication_resume_as_one_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old_cwd = Path.cwd()
            old_root = sdlc.ROOT
            self.addCleanup(os.chdir, old_cwd)
            self.addCleanup(setattr, sdlc, "ROOT", old_root)
            os.chdir(root)
            sdlc.ROOT = root
            release_coordinator = sys.modules[release_publisher.verify_release_tree.__module__]
            old_release_root = release_coordinator.ROOT
            self.addCleanup(setattr, release_coordinator, "ROOT", old_release_root)
            release_coordinator.ROOT = root
            (root / ".changes").mkdir()
            (root / ".changes/one.json").write_text('{"semver":"patch","summary":"one","type":"fix"}\n')
            (root / "CHANGELOG.md").write_text("# Changelog\n")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "feature with release fragment"], cwd=root, check=True)
            source_sha = self.git(root, "rev-parse", "HEAD")

            previous_root = sdlc.ROOT
            sdlc.ROOT = root
            try:
                manifest = sdlc.release_snapshot(source_sha)
            finally:
                sdlc.ROOT = previous_root
            subprocess.run(["git", "add", "CHANGELOG.md", ".sdlc/release.json", ".changes"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "prepare rolling release"], cwd=root, check=True)
            candidate_sha = self.git(root, "rev-parse", "HEAD")
            tree_sha = self.git(root, "rev-parse", f"{candidate_sha}^{{tree}}")
            actual_merge_sha = subprocess.run(
                ["git", "commit-tree", tree_sha, "-p", source_sha, "-m", "squash merge release PR"],
                cwd=root, check=True, capture_output=True, text=True,
            ).stdout.strip()

            pr = PullRequest(
                17, candidate_sha, "sdlc/release-next", "blogle", "User", "blogle/example",
                "release-bot[bot]", "Bot",
            )
            required_checks = [
                Check("sdlc / check", "success", candidate_sha, check_id=1, completed_at="2026-10-14T00:00:00Z", app_id=GITHUB_ACTIONS_APP_ID),
                Check("sdlc / policy", "success", candidate_sha, check_id=2, completed_at="2026-10-14T00:00:01Z", app_id=GITHUB_ACTIONS_APP_ID),
            ]
            protection = {
                "classic": False,
                "contexts": ["sdlc / check", "sdlc / policy"],
                "check_apps": {"sdlc / check": GITHUB_ACTIONS_APP_ID, "sdlc / policy": GITHUB_ACTIONS_APP_ID},
                "bypass_actors": [],
                "rulesets": [{"id": 7, "name": "SDLC default branch", "enforcement": "active", "updated_at": "now"}],
                "canonical_ruleset_matches": True,
                "ruleset_attestation": {"schema": 1, "repository": "blogle/example", "ruleset_id": 7, "updated_at": "now", "bypass_actors": []},
            }
            release_gate.evaluate_release_gate(
                pr=pr,
                authenticated_login="release-bot[bot]",
                app_slug="release-bot",
                main_sha=source_sha,
                parent_sha=source_sha,
                checks=required_checks,
                manifest=manifest,
                release_bot_login="release-bot[bot]",
                repository="blogle/example",
                protection=protection,
            )
            release_check = Check("sdlc / release-gate", "success", candidate_sha, "release-bot", 3, "2026-10-14T00:00:02Z")
            api = StatefulReleaseApi(
                "blogle/example", source_sha, pr, required_checks + [release_check], protection, actual_merge_sha
            )
            result = release_gate.merge_release_pr(
                api=api,
                pr_number=pr.number,
                expected_head_sha=candidate_sha,
                current_main_sha=source_sha,
                release_bot_login="release-bot[bot]",
            )
            self.assertEqual(result["sha"], actual_merge_sha)
            identity = release_publisher.validate_checkout(result["sha"])
            self.assertEqual(identity.source_main, source_sha)
            self.assertEqual(api.current_main, actual_merge_sha)

            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                ledger_path = root / ".sdlc/release-ledger.json"
                release_publisher.record_artifact(ledger_path, actual_merge_sha, "source-archive", "a" * 64)
                receipt_path = root / ".sdlc/publication-receipts.json"
                receipt_path.write_text(json.dumps({"schema": 1, "artifacts": [{
                    "name": "example-image",
                    "digest": "sha256:" + "b" * 64,
                    "version": identity.version,
                    "merged_sha": identity.commit,
                    "source_main_sha": identity.source_main,
                }]}))
                release_publisher.record_receipts(ledger_path, receipt_path, identity.version, identity.commit, identity.source_main)
                release_publisher.mark_published(ledger_path)
                release_publisher.record_receipts(ledger_path, receipt_path, identity.version, identity.commit, identity.source_main)
                ledger = json.loads(ledger_path.read_text())
                self.assertTrue(ledger["published"])
                self.assertEqual(ledger["artifacts"]["example-image"], "b" * 64)

                receipt = json.loads(receipt_path.read_text())
                receipt["artifacts"][0]["digest"] = "sha256:" + "c" * 64
                receipt_path.write_text(json.dumps(receipt))
                with self.assertRaisesRegex(release_publisher.IdentityError, "digest changed"):
                    release_publisher.record_receipts(ledger_path, receipt_path, identity.version, identity.commit, identity.source_main)
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
