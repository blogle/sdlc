import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import src.release_gate as release_gate
from src.release_gate import Check, GateError, PullRequest, evaluate_release_gate, merge_release_pr, verify_candidate, verify_protection
from src.sdlc import generated_tree_digest


PROTECTION = {"classic": True, "contexts": ["sdlc / pr-fast"], "bypass_actors": []}


class FakeApi:
    def __init__(self, main, pr, checks):
        self._main, self._pr, self._checks = main, pr, checks
        self.merge_args = None
        self.app_slug = "release-bot"

    def main_sha(self):
        return self._main

    def pull_request(self, _number):
        return self._pr

    def checks(self, _sha):
        return self._checks

    def protection(self):
        return PROTECTION

    def parent_sha(self, _sha):
        return self._main

    def merge(self, number, **kwargs):
        self.merge_args = (number, kwargs)
        return {"merged": True}


class ReleaseGateTests(unittest.TestCase):
    def release_pr(self, head="head"):
        return PullRequest(14, head, "sdlc/release-next", "release-bot[bot]", "Bot", "blogle/sdlc", "release-bot[bot]", "Bot")

    def test_normal_pr_is_stable_success_without_freshness(self):
        evaluate_release_gate(
            pr=PullRequest(1, "feature", "feature", "alice", "User", "alice/sdlc", "alice", "User"),
            authenticated_login="alice", app_slug="irrelevant", main_sha="new-main", parent_sha="old-main",
            checks=(), manifest=None, release_bot_login="release-bot[bot]", repository="blogle/sdlc", protection=None,
        )

    def test_stale_head_after_main_advance_fails(self):
        with self.assertRaisesRegex(GateError, "current main"):
            evaluate_release_gate(
                pr=self.release_pr(), authenticated_login="release-bot[bot]", app_slug="release-bot", main_sha="new-main",
                parent_sha="old-main", checks=(), manifest={}, release_bot_login="release-bot[bot]", repository="blogle/sdlc", protection=PROTECTION,
            )

    def test_release_requires_authenticated_app_and_same_repository_branch(self):
        pr = PullRequest(14, "head", "sdlc/release-next", "release-bot[bot]", "Bot", "attacker/sdlc", "release-bot[bot]", "Bot")
        with self.assertRaisesRegex(GateError, "authenticated release App"):
            evaluate_release_gate(
                pr=pr, authenticated_login="release-bot[bot]", app_slug="release-bot", main_sha="main", parent_sha="main",
                checks=(), manifest={}, release_bot_login="release-bot[bot]", repository="blogle/sdlc", protection=PROTECTION,
            )

    def test_required_check_must_be_successful_for_exact_head(self):
        with self.assertRaisesRegex(GateError, "exact head"):
            evaluate_release_gate(
                pr=self.release_pr(), authenticated_login="release-bot[bot]", app_slug="release-bot", main_sha="main", parent_sha="main",
                checks=[Check("sdlc / pr-fast", "success", "old-head")], manifest={}, release_bot_login="release-bot[bot]", repository="blogle/sdlc", protection=PROTECTION,
            )

    def test_missing_protection_fails_closed(self):
        with self.assertRaisesRegex(GateError, "no applicable"):
            verify_protection({"rulesets": [], "classic": False, "contexts": [], "bypass_actors": []})

    def test_manifest_uses_canonical_coordinator_schema_and_replays_twice(self):
        changelog = b"# Changelog\n\n## [1.0.0]\n"
        manifest = {
            "schema": 1,
            "version": "1.0.0",
            "prior_released_boundary": None,
            "source_main_sha": "main",
            "fragments": [{"path": ".changes/one.json", "blob_sha": "a" * 40}],
            "changelog_sha256": hashlib.sha256(changelog).hexdigest(),
            "generated_tree": generated_tree_digest(changelog, [{"path": ".changes/one.json", "blob_sha": "a" * 40}]),
            "publication": {"version": "1.0.0", "source_main_sha": "main"},
        }
        calls = []

        def planner(source):
            calls.append(source)
            return manifest

        def git(*args, raw=False):
            if args[:2] == ("rev-parse", "main:.changes/one.json"):
                return "a" * 40
            if args[:2] == ("rev-parse", "head:.changes/one.json"):
                raise GateError("missing fragment")
            if args[:2] == ("diff", "--name-only"):
                return ".changes/one.json\n.sdlc/release.json\nCHANGELOG.md"
            if args[:2] == ("show", "head:CHANGELOG.md") and raw:
                return changelog
            raise AssertionError(args)

        with patch.object(release_gate, "verify_release_tree"):
            verify_candidate("main", "head", manifest, git=git, planner=planner)
        self.assertEqual(calls, ["main", "main"])

    def test_merge_rechecks_live_base_and_expected_head(self):
        api = FakeApi("new-main", self.release_pr(), [Check("sdlc / pr-fast", "success", "head"), Check("sdlc / release-gate", "success", "head", "release-bot")])
        with self.assertRaisesRegex(GateError, "advanced"):
            merge_release_pr(api=api, pr_number=14, expected_head_sha="head", current_main_sha="old-main", release_bot_login="release-bot[bot]")
        self.assertIsNone(api.merge_args)

    def test_merge_passes_expected_head_and_requires_app_owned_gate(self):
        api = FakeApi("main", self.release_pr(), [Check("sdlc / pr-fast", "success", "head"), Check("sdlc / release-gate", "success", "head", "release-bot")])
        merge_release_pr(api=api, pr_number=14, expected_head_sha="head", current_main_sha="main", release_bot_login="release-bot[bot]")
        self.assertEqual(api.merge_args, (14, {"sha": "head", "merge_method": "squash"}))

    def test_merge_rejects_same_named_check_from_another_app(self):
        api = FakeApi("main", self.release_pr(), [Check("sdlc / pr-fast", "success", "head"), Check("sdlc / release-gate", "success", "head", "attacker")])
        with self.assertRaisesRegex(GateError, "App"):
            merge_release_pr(api=api, pr_number=14, expected_head_sha="head", current_main_sha="main", release_bot_login="release-bot[bot]")

    def test_cli_normal_pr_is_real_process_and_does_not_need_app_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            gh = Path(temp) / "gh"
            gh.write_text("#!/bin/sh\nprintf '%s' '{\"head\":{\"sha\":\"head\",\"ref\":\"feature\",\"user\":{\"login\":\"alice\",\"type\":\"User\"},\"repo\":{\"full_name\":\"alice/sdlc\"}},\"user\":{\"login\":\"alice\",\"type\":\"User\"}}'\n")
            gh.chmod(0o755)
            env = {"PATH": f"{temp}:{__import__('os').environ['PATH']}"}
            result = subprocess.run([sys.executable, "src/release_gate.py", "gate", "--repo", "blogle/sdlc", "--pr", "17", "--release-bot", "release-bot[bot]"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_workflow_uses_event_json_and_head_specific_app_check(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/release-gate.yml").read_text()
        self.assertIn("jq -r '.pull_request.head.sha'", workflow)
        self.assertIn("repos/$GITHUB_REPOSITORY/check-runs", workflow)
        self.assertNotIn("GITHUB_EVENT_PULL_REQUEST_HEAD_SHA", workflow)


if __name__ == "__main__":
    unittest.main()
