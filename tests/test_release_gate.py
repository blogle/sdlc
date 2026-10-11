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
    def __init__(self, main, pr, checks, app_slug="release-bot"):
        self._main, self._pr, self._checks = main, pr, checks
        self.merge_args = None
        self.app_slug = app_slug
        self.repo = "blogle/sdlc"

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
        return PullRequest(14, head, "sdlc/release-next", "blogle", "User", "blogle/sdlc", "release-bot[bot]", "Bot")

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
        pr = PullRequest(14, "head", "sdlc/release-next", "blogle", "User", "attacker/sdlc", "release-bot[bot]", "Bot")
        with self.assertRaisesRegex(GateError, "authenticated release App"):
            evaluate_release_gate(
                pr=pr, authenticated_login="release-bot[bot]", app_slug="release-bot", main_sha="main", parent_sha="main",
                checks=(), manifest={}, release_bot_login="release-bot[bot]", repository="blogle/sdlc", protection=PROTECTION,
            )

    def test_same_repository_head_owner_is_not_required_to_be_the_app(self):
        response = json.loads((Path(__file__).parent / "fixtures/release-pr.json").read_text())
        with patch.object(release_gate, "_gh_json", return_value=response):
            pr = release_gate._pull_request("blogle/sdlc", 14)
        self.assertEqual(pr.head_login, "blogle")
        self.assertEqual(pr.head_type, "User")
        self.assertEqual(pr.author_login, "sdlc-release[bot]")
        self.assertEqual(pr.author_type, "Bot")
        api = FakeApi("main", pr, [Check("sdlc / pr-fast", "success", "head", check_id=1, completed_at="2026-10-10T01:00:00Z"), Check("sdlc / release-gate", "success", "head", "sdlc-release", 2, "2026-10-10T01:00:00Z")], "sdlc-release")
        merge_release_pr(api=api, pr_number=14, expected_head_sha="head", current_main_sha="main", release_bot_login="sdlc-release[bot]")

    def test_same_repository_release_pr_by_human_is_rejected(self):
        pr = PullRequest(14, "head", "sdlc/release-next", "blogle", "User", "blogle/sdlc", "blogle", "User")
        api = FakeApi("main", pr, [])
        with self.assertRaisesRegex(GateError, "release App"):
            merge_release_pr(api=api, pr_number=14, expected_head_sha="head", current_main_sha="main", release_bot_login="sdlc-release[bot]")

    def test_latest_failure_invalidates_older_success_for_exact_head(self):
        checks = [
            Check("sdlc / pr-fast", "success", "head", "ci", 10, "2026-10-10T01:00:00Z"),
            Check("sdlc / pr-fast", "failure", "head", "ci", 11, "2026-10-10T02:00:00Z"),
        ]
        with self.assertRaisesRegex(GateError, "latest required check"):
            release_gate._required_check(checks, "sdlc / pr-fast", "head", "ci")

    def test_new_success_after_failure_authorizes_exact_head(self):
        checks = [
            Check("sdlc / pr-fast", "failure", "head", "ci", 10, "2026-10-10T01:00:00Z"),
            Check("sdlc / pr-fast", "success", "head", "ci", 11, "2026-10-10T02:00:00Z"),
        ]
        release_gate._required_check(checks, "sdlc / pr-fast", "head", "ci")

    def test_success_from_another_app_cannot_override_latest_failure(self):
        checks = [
            Check("sdlc / pr-fast", "failure", "head", "trusted", 10, "2026-10-10T02:00:00Z"),
            Check("sdlc / pr-fast", "success", "head", "attacker", 11, "2026-10-10T03:00:00Z"),
        ]
        with self.assertRaisesRegex(GateError, "not successful"):
            release_gate._required_check(checks, "sdlc / pr-fast", "head", "trusted")

    def test_check_api_is_paginated_and_preserves_completion_metadata(self):
        response = json.dumps([{"total_count": 1, "check_runs": [{"name": "sdlc / pr-fast", "conclusion": "success", "head_sha": "head", "id": 7, "completed_at": "2026-10-10T01:00:00Z", "app": {"slug": "ci"}}]}])
        with patch.object(release_gate.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, response, "")) as run:
            checks = release_gate._checks("blogle/sdlc", "head")
        self.assertEqual(checks[0].check_id, 7)
        self.assertIn("--paginate", run.call_args.args[0])
        self.assertIn("--slurp", run.call_args.args[0])

    def test_check_api_allows_unrelated_in_progress_checks(self):
        response = json.dumps([{"check_runs": [
            {"name": "sdlc / pr-fast", "status": "completed", "conclusion": "success", "head_sha": "head", "id": 7, "completed_at": "2026-10-10T01:00:00Z", "app": {"slug": "ci"}},
            {"name": "unrelated", "status": "in_progress", "conclusion": None, "head_sha": "head", "id": 8, "completed_at": None, "app": {"slug": "other"}},
        ]}])
        with patch.object(release_gate.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, response, "")):
            checks = release_gate._checks("blogle/sdlc", "head")
        release_gate._required_check(checks, "sdlc / pr-fast", "head", "ci")

    def test_check_api_rejects_completed_check_without_completion_time(self):
        response = json.dumps([{"check_runs": [{"name": "sdlc / pr-fast", "status": "completed", "conclusion": "success", "head_sha": "head", "id": 7, "completed_at": None}]}])
        with patch.object(release_gate.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, response, "")):
            with self.assertRaisesRegex(GateError, "ambiguous GitHub check metadata"):
                release_gate._checks("blogle/sdlc", "head")

    def test_raw_git_output_preserves_newline_terminated_changelog_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run(["git", "init", "-q"], cwd=temp, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=temp, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=temp, check=True)
            changelog = Path(temp) / "CHANGELOG.md"
            changelog.write_bytes(b"# Changelog\n")
            subprocess.run(["git", "add", "CHANGELOG.md"], cwd=temp, check=True)
            subprocess.run(["git", "commit", "-qm", "source"], cwd=temp, check=True)
            commit = release_gate._git("rev-parse", "HEAD", cwd=temp)
            self.assertEqual(release_gate._git("show", f"{commit}:CHANGELOG.md", cwd=temp, raw=True), b"# Changelog\n")

    def test_installation_auth_uses_supported_provenance_endpoints_only(self):
        with patch.object(release_gate, "_gh_json", side_effect=lambda endpoint: {
            "installation/repositories?per_page=100": {"repositories": [{"full_name": "blogle/sdlc"}]},
        }[endpoint]) as api:
            self.assertEqual(
                release_gate._authenticated_app("release-bot[bot]", "release-bot", "blogle/sdlc"),
                ("release-bot[bot]", "release-bot"),
            )
            self.assertEqual(api.call_args_list[0].args[0], "installation/repositories?per_page=100")
            self.assertNotIn("user", [call.args[0] for call in api.call_args_list])
            self.assertNotIn("app", [call.args[0] for call in api.call_args_list])

    def test_installation_auth_rejects_wrong_configured_bot_or_repository(self):
        with self.assertRaisesRegex(GateError, "configured release bot"):
            release_gate._authenticated_app("other[bot]", "release-bot", "blogle/sdlc")
        with patch.object(release_gate, "_gh_json", return_value={"repositories": []}), self.assertRaisesRegex(GateError, "cannot access"):
            release_gate._authenticated_app("release-bot[bot]", "release-bot", "blogle/sdlc")

    def test_required_check_must_be_successful_for_exact_head(self):
        with self.assertRaisesRegex(GateError, "exact head"):
            evaluate_release_gate(
                pr=self.release_pr(), authenticated_login="release-bot[bot]", app_slug="release-bot", main_sha="main", parent_sha="main",
                checks=[Check("sdlc / pr-fast", "success", "old-head", check_id=1, completed_at="2026-10-10T01:00:00Z")], manifest={}, release_bot_login="release-bot[bot]", repository="blogle/sdlc", protection=PROTECTION,
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
        api = FakeApi("new-main", self.release_pr(), [Check("sdlc / pr-fast", "success", "head", check_id=1, completed_at="2026-10-10T01:00:00Z"), Check("sdlc / release-gate", "success", "head", "release-bot", 2, "2026-10-10T01:00:00Z")])
        with self.assertRaisesRegex(GateError, "advanced"):
            merge_release_pr(api=api, pr_number=14, expected_head_sha="head", current_main_sha="old-main", release_bot_login="release-bot[bot]")
        self.assertIsNone(api.merge_args)

    def test_merge_passes_expected_head_and_requires_app_owned_gate(self):
        api = FakeApi("main", self.release_pr(), [Check("sdlc / pr-fast", "success", "head", check_id=1, completed_at="2026-10-10T01:00:00Z"), Check("sdlc / release-gate", "success", "head", "release-bot", 2, "2026-10-10T01:00:00Z")])
        merge_release_pr(api=api, pr_number=14, expected_head_sha="head", current_main_sha="main", release_bot_login="release-bot[bot]")
        self.assertEqual(api.merge_args, (14, {"sha": "head", "merge_method": "squash"}))

    def test_merge_rejects_same_named_check_from_another_app(self):
        api = FakeApi("main", self.release_pr(), [Check("sdlc / pr-fast", "success", "head", check_id=1, completed_at="2026-10-10T01:00:00Z"), Check("sdlc / release-gate", "success", "head", "attacker", 2, "2026-10-10T01:00:00Z")])
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
        self.assertIn("jq -r '.head.sha'", workflow)
        self.assertIn("repos/$GITHUB_REPOSITORY/check-runs", workflow)
        self.assertNotIn("GITHUB_EVENT_PULL_REQUEST_HEAD_SHA", workflow)
        self.assertNotIn("if: github.event.pull_request.head.ref == 'sdlc/release-next'", workflow)
        self.assertIn("steps.release-app.outputs.app-slug", workflow)
        self.assertIn("check-runs?per_page=100", workflow)
        self.assertIn("sleep 10", workflow)


if __name__ == "__main__":
    unittest.main()
