import unittest
from unittest.mock import patch

import src.onboard as onboard


class OnboardTests(unittest.TestCase):
    def _patch_ready(self):
        return (
            patch.object(onboard, "_user_api", return_value={"default_branch": "main"}),
            patch.object(onboard, "_installation", return_value=({"app_slug": "sdlc-release"}, b"private-key")),
            patch.object(onboard, "_workflow_readiness", return_value={"successful_ci": True}),
            patch.object(onboard, "_policy", return_value={"plan": "no-op", "policy_verified": True}),
        )

    def test_dry_run_is_prepare_only(self):
        patches = self._patch_ready()
        with patches[0], patches[1], patches[2], patches[3], patch.object(onboard, "_set_variable") as set_variable, patch.object(onboard.subprocess, "run") as run:
            onboard.onboard("owner/repo", "123", "/secure/app.pem", dry_run=True)
        set_variable.assert_not_called()
        run.assert_not_called()

    def test_cutover_disables_first_then_verifies_policy_and_enables(self):
        patches = self._patch_ready()
        with patches[0], patches[1], patches[2], patches[3], patch.object(onboard, "_set_variable") as set_variable, patch.object(onboard.subprocess, "run") as run:
            onboard.onboard("owner/repo", "123", "/secure/app.pem")
        self.assertEqual(
            [call.args for call in set_variable.call_args_list],
            [("owner/repo", "SDLC_RELEASE_ACTIVATE", "false"), ("owner/repo", "SDLC_RELEASE_BOT_LOGIN", "sdlc-release[bot]"), ("owner/repo", "SDLC_RELEASE_ACTIVATE", "true")],
        )
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[-1].kwargs["input"], b"private-key")
        self.assertNotIn("private-key", " ".join(" ".join(call.args[0]) for call in run.call_args_list))

    def test_enable_is_fail_closed_and_disable_is_explicit(self):
        with patch.object(onboard, "_preflight", return_value={"errors": ["missing ruleset"]}), patch.object(onboard, "_set_variable") as set_variable:
            with self.assertRaisesRegex(ValueError, "missing ruleset"):
                onboard.release_enable("owner/repo")
            set_variable.assert_not_called()
        with patch.object(onboard, "_set_variable") as set_variable:
            onboard.release_disable("owner/repo")
            set_variable.assert_called_once_with("owner/repo", "SDLC_RELEASE_ACTIVATE", "false")

    def test_unready_workflows_stop_before_policy_or_activation_writes(self):
        with patch.object(onboard, "_user_api", return_value={"default_branch": "main"}), patch.object(
            onboard, "_installation", return_value=({"app_slug": "sdlc-release"}, b"private-key")
        ), patch.object(onboard, "_workflow_readiness", side_effect=ValueError("CI has not reported")), patch.object(onboard, "_set_variable") as set_variable, patch.object(onboard, "_policy") as policy:
            with self.assertRaisesRegex(ValueError, "CI has not reported"):
                onboard.onboard("owner/repo", "123", "/secure/app.pem")
        set_variable.assert_not_called()
        policy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
