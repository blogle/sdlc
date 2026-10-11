from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import src.onboard as onboard


class OnboardTests(unittest.TestCase):
    def test_dry_run_verifies_installation_without_writing_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "app.pem"
            key.write_bytes(b"-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n")
            with patch.object(onboard, "_jwt", return_value="jwt"), patch.object(
                onboard, "_gh_api", side_effect=[{"id": 7, "app_slug": "sdlc-release", "permissions": {"contents": "write", "pull_requests": "write", "checks": "write"}}, {"token": "installation-token"}, {"full_name": "owner/repo"}]
            ), patch.object(onboard, "_variable", return_value=None), patch.object(onboard, "subprocess") as process:
                onboard.onboard("owner/repo", "123", str(key), dry_run=True)
            self.assertEqual(process.run.call_count, 0)

    def test_default_provisioning_is_inactive_and_secret_is_stdin_only(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "app.pem"
            secret = b"-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----\n"
            key.write_bytes(secret)
            with patch.object(onboard, "_jwt", return_value="jwt"), patch.object(
                onboard, "_gh_api", side_effect=[{"id": 7, "app_slug": "sdlc-release", "permissions": {"contents": "write", "pull_requests": "write", "checks": "write"}}, {"token": "installation-token"}, {"full_name": "owner/repo"}]
            ), patch.object(onboard, "_variable", side_effect=[None, "false", "sdlc-release[bot]"]), patch.object(onboard, "subprocess") as process:
                onboard.onboard("owner/repo", "123", str(key))
            commands = [call.args[0] for call in process.run.call_args_list]
            self.assertIn("false", commands[0])
            self.assertIn("sdlc-release[bot]", commands[1])
            self.assertEqual(commands[-1][-2], "--repo")
            self.assertEqual(process.run.call_args_list[-1].kwargs["input"], secret)
            self.assertNotIn(secret.decode(), " ".join(" ".join(command) for command in commands))

    def test_enable_is_fail_closed_and_disable_is_explicit(self):
        with patch.object(onboard, "_preflight", return_value={"errors": ["missing ruleset"]}), patch.object(onboard, "_set_variable") as set_variable:
            with self.assertRaisesRegex(ValueError, "missing ruleset"):
                onboard.release_enable("owner/repo")
            set_variable.assert_not_called()
        with patch.object(onboard, "_set_variable") as set_variable:
            onboard.release_disable("owner/repo")
            set_variable.assert_called_once_with("owner/repo", "SDLC_RELEASE_ACTIVATE", "false")


if __name__ == "__main__":
    unittest.main()
