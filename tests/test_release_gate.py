import unittest

from src.release_gate import Check, GateError, PullRequest, evaluate_release_gate, merge_release_pr, verify_manifest, verify_protection


class FakeApi:
    def __init__(self, main, pr, checks):
        self._main, self._pr, self._checks = main, pr, checks
        self.merge_args = None

    def main_sha(self):
        return self._main

    def pull_request(self, _number):
        return self._pr

    def checks(self, _sha):
        return self._checks

    def protection(self):
        return {"classic": True, "contexts": ["sdlc / pr-fast"], "bypass_actors": []}

    def merge(self, number, **kwargs):
        self.merge_args = (number, kwargs)
        return {"merged": True}


class ReleaseGateTests(unittest.TestCase):
    def release_pr(self, head="head"):
        return PullRequest(14, head, "sdlc/release-next", "release-bot[bot]", "Bot", "blogle/sdlc", "release-bot[bot]", "Bot")

    def test_normal_pr_is_stable_success_without_freshness(self):
        evaluate_release_gate(
            pr=PullRequest(1, "feature", "feature", "alice", "User", "alice/sdlc", "alice", "User"),
            authenticated_login="alice", main_sha="new-main", parent_sha="old-main",
            checks=(), manifest=None, release_bot_login="release-bot[bot]",
        )

    def test_stale_head_after_main_advance_fails(self):
        with self.assertRaisesRegex(GateError, "current main"):
            evaluate_release_gate(
                pr=self.release_pr(), authenticated_login="release-bot[bot]", main_sha="new-main",
                parent_sha="old-main", checks=(), manifest={}, release_bot_login="release-bot[bot]", protection={"classic": True, "contexts": ["sdlc / pr-fast"], "bypass_actors": []},
            )

    def test_release_requires_authenticated_bot_identity(self):
        with self.assertRaisesRegex(GateError, "authenticated release bot"):
            evaluate_release_gate(
                pr=self.release_pr(), authenticated_login="alice", main_sha="main", parent_sha="main",
                checks=(), manifest={}, release_bot_login="release-bot[bot]", protection={"classic": True, "contexts": ["sdlc / pr-fast"], "bypass_actors": []},
            )

    def test_required_check_must_be_successful_for_exact_head(self):
        with self.assertRaisesRegex(GateError, "release head"):
            evaluate_release_gate(
                pr=self.release_pr(), authenticated_login="release-bot[bot]", main_sha="main", parent_sha="main",
                checks=[Check("sdlc / pr-fast", "success", "old-head")], manifest={}, release_bot_login="release-bot[bot]", protection={"classic": True, "contexts": ["sdlc / pr-fast"], "bypass_actors": []},
            )

    def test_missing_protection_fails_closed(self):
        with self.assertRaisesRegex(GateError, "no applicable"):
            verify_protection({"rulesets": [], "classic": False, "contexts": [], "bypass_actors": []}, ["sdlc / release-gate"])

    def test_manifest_planner_must_be_deterministic(self):
        manifest = {
            "schema_version": 1,
            "source_main_sha": "main",
            "fragments": [{"path": ".changes/one.json", "blob_sha": "blob"}],
        }
        calls = iter([manifest, {**manifest, "version": "different"}])
        with self.assertRaisesRegex(GateError, "not deterministic"):
            verify_manifest(source_sha="main", head_sha="head", manifest=manifest, git=lambda *args, **kwargs: "", planner=lambda **_: next(calls))

    def test_merge_uses_expected_head_and_rejects_base_race(self):
        api = FakeApi("new-main", self.release_pr(), [Check("sdlc / pr-fast", "success", "head"), Check("sdlc / release-gate", "success", "head")])
        with self.assertRaisesRegex(GateError, "advanced"):
            merge_release_pr(api=api, pr_number=14, expected_head_sha="head", current_main_sha="old-main", release_bot_login="release-bot[bot]")
        self.assertIsNone(api.merge_args)

    def test_merge_passes_expected_head_sha(self):
        api = FakeApi("main", self.release_pr(), [Check("sdlc / pr-fast", "success", "head"), Check("sdlc / release-gate", "success", "head")])
        merge_release_pr(api=api, pr_number=14, expected_head_sha="head", current_main_sha="main", release_bot_login="release-bot[bot]")
        self.assertEqual(api.merge_args, (14, {"sha": "head", "merge_method": "squash"}))


if __name__ == "__main__":
    unittest.main()
