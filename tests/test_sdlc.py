import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

SOURCE = Path(__file__).parents[1] / "src/sdlc.py"
SPEC = importlib.util.spec_from_file_location("sdlc", SOURCE)
sdlc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sdlc)


class ChangelogTests(unittest.TestCase):
    def test_sorted_fragments_choose_strongest_bump_and_generate_deterministically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".changes").mkdir()
            (root / ".changes/z.json").write_text('{"type":"fix","semver":"patch","summary":"Fix"}\n')
            (root / ".changes/a.json").write_text('{"type":"feat","semver":"minor","summary":"Feature"}\n')
            (root / "CHANGELOG.md").write_text("# Changelog\n")
            sdlc.ROOT = root
            entries = sdlc.fragments()
            self.assertEqual([path.name for path, _ in entries], ["a.json", "z.json"])
            self.assertEqual(sdlc.bump_intent(entries), "minor")
            with redirect_stdout(io.StringIO()):
                sdlc.changelog("finalize", version="1.2.0", date="2026-10-12")
            self.assertIn("## [1.2.0] - 2026-10-12", (root / "CHANGELOG.md").read_text())
            self.assertFalse(list((root / ".changes").glob("*.json")))

    def test_invalid_fragment_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".changes").mkdir()
            (root / ".changes/bad.json").write_text('{"semver":"future"}')
            sdlc.ROOT = root
            with self.assertRaisesRegex(ValueError, "requires type, semver, and summary"):
                sdlc.fragments()


class RulesetTests(unittest.TestCase):
    def test_read_only_automated_merges_attestation_is_bound_to_exact_policy_and_revision(self):
        import subprocess

        primary = dict(sdlc._canonical_ruleset(), id=7, updated_at="now")
        updates = dict(sdlc._canonical_ruleset("main-updates.json"), id=8, updated_at="now")
        attestation = {
            "automated_merges": {
                "schema": 1, "repository": "owner/repo", "ruleset_id": 8,
                "updated_at": "now", "bypass_actors": updates.pop("bypass_actors"),
            },
        }
        read = lambda repo, **kwargs: subprocess.CompletedProcess([], 0, json.dumps(primary if kwargs["name"] == primary["name"] else updates), "")
        with patch.object(sdlc, "_ruleset", side_effect=read), patch.dict(sdlc.os.environ, {"SDLC_RULESET_ATTESTATION": json.dumps(attestation)}), redirect_stdout(io.StringIO()):
            sdlc.ruleset_command("check", "owner/repo")
        attestation["automated_merges"]["updated_at"] = "stale"
        with patch.object(sdlc, "_ruleset", side_effect=read), patch.dict(sdlc.os.environ, {"SDLC_RULESET_ATTESTATION": json.dumps(attestation)}), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "ruleset is update"):
                sdlc.ruleset_command("check", "owner/repo")

    def test_apply_installs_both_layers_idempotently_without_bypassing_checks(self):
        state = {}
        mutations = []

        def read(repo, read_only=False, name="SDLC default branch"):
            value = state.get(name)
            return __import__("subprocess").CompletedProcess([], 0, json.dumps(value), "") if value else None

        def write(command, **kwargs):
            value = json.loads(kwargs["input"])
            mutations.append(value)
            state[value["name"]] = dict(value, id=len(state) + 1)

        with patch.object(sdlc, "_ruleset", side_effect=read), patch.object(sdlc.subprocess, "run", side_effect=write), redirect_stdout(io.StringIO()):
            sdlc.ruleset_command("apply", "owner/repo")
            sdlc.ruleset_command("apply", "owner/repo")
            sdlc.ruleset_command("check", "owner/repo")
        self.assertEqual(len(mutations), 2)
        self.assertEqual(state["SDLC default branch"]["bypass_actors"], [])
        self.assertEqual(state["SDLC automated merges"]["rules"], [{"type": "update"}])
        state["SDLC automated merges"]["bypass_actors"].append({"actor_id": 1, "actor_type": "Team", "bypass_mode": "always"})
        with patch.object(sdlc, "_ruleset", side_effect=read), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "ruleset is update"):
                sdlc.ruleset_command("check", "owner/repo")

    def test_contract_projection_ignores_github_metadata_but_detects_policy_drift(self):
        desired = sdlc._canonical_ruleset()
        actual = json.loads(json.dumps(desired))
        actual["id"] = 7
        checks = next(rule for rule in actual["rules"] if rule["type"] == "required_status_checks")
        checks["parameters"]["required_status_checks"][0]["app_id"] = 123
        actual["rules"].reverse()
        self.assertTrue(sdlc._matches_ruleset(actual, desired))
        actual["enforcement"] = "disabled"
        self.assertFalse(sdlc._matches_ruleset(actual, desired))

    def test_plan_and_apply_are_idempotent(self):
        desired = sdlc._canonical_ruleset()
        listing = json.dumps([{"id": 7, "name": desired["name"]}])
        detail = json.dumps(dict(desired, id=7))
        with patch.object(sdlc.subprocess, "run", side_effect=[
            __import__("subprocess").CompletedProcess([], 0, listing, ""),
            __import__("subprocess").CompletedProcess([], 0, detail, ""),
        ]) as run, redirect_stdout(io.StringIO()) as out:
            sdlc.ruleset_command("plan", "owner/repo")
        self.assertEqual(json.loads(out.getvalue())["action"], "no-op")
        self.assertEqual(run.call_count, 2)

    def test_dry_run_still_reports_the_canonical_plan_without_api_read_access(self):
        error = __import__("subprocess").CalledProcessError(1, ["gh", "api"])
        with patch.object(sdlc.subprocess, "run", side_effect=error), redirect_stdout(io.StringIO()) as out:
            sdlc.ruleset_command("apply", "owner/repo", dry_run=True)
        plan = json.loads(out.getvalue())
        self.assertEqual(plan["action"], "read-unavailable")
        self.assertEqual(plan["ruleset"], sdlc._canonical_ruleset())

    def test_read_only_ruleset_check_accepts_only_current_attested_bypass_redaction(self):
        desired = sdlc._canonical_ruleset()
        actual = dict(desired, id=7, updated_at="now")
        del actual["bypass_actors"]
        response = __import__("subprocess").CompletedProcess([], 0, json.dumps(actual), "")
        attestation = json.dumps({"schema": 1, "repository": "owner/repo", "ruleset_id": 7, "updated_at": "now", "bypass_actors": []})
        automated = __import__("subprocess").CompletedProcess([], 0, json.dumps(sdlc._canonical_ruleset("main-updates.json")), "")
        with patch.object(sdlc, "_ruleset", side_effect=[response, automated]), patch.dict(sdlc.os.environ, {"SDLC_RULESET_ATTESTATION": attestation}), redirect_stdout(io.StringIO()) as out:
            sdlc.ruleset_command("check", "owner/repo")
        self.assertIn("matches", out.getvalue())

        stale = json.dumps({"schema": 1, "repository": "owner/repo", "ruleset_id": 7, "updated_at": "old", "bypass_actors": []})
        with patch.object(sdlc, "_ruleset", return_value=response), patch.dict(sdlc.os.environ, {"SDLC_RULESET_ATTESTATION": stale}):
            with self.assertRaisesRegex(ValueError, "ruleset is update"):
                sdlc.ruleset_command("check", "owner/repo")


class InstallTests(unittest.TestCase):
    def test_install_generates_and_checks_pinned_adapters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath("flake.lock").write_text(json.dumps({"nodes": {
                "root": {"inputs": {"sdlc": "contract"}},
                "contract": {"locked": {"rev": "a" * 40}},
            }}))
            sdlc.ROOT = root
            with redirect_stdout(io.StringIO()):
                sdlc.install_adapters()
                sdlc.install_adapters(check=True)
            workflow = (root / ".github/workflows/sdlc.yml").read_text()
            self.assertIn("ci.yml@" + "a" * 40, workflow)
            self.assertIn("sdlc_ref: " + "a" * 40, workflow)
            reconcile = (root / ".github/workflows/sdlc-release.yml").read_text()
            self.assertIn("release.yml@" + "a" * 40, reconcile)
            self.assertIn("workflows: [SDLC release merge]", reconcile)
            self.assertNotIn("pull_request_target", reconcile)
            gate = (root / ".github/workflows/sdlc-release-gate.yml").read_text()
            self.assertIn("release-gate.yml@" + "a" * 40, gate)
            self.assertIn("workflows: [SDLC CI]", gate)
            merge = (root / ".github/workflows/sdlc-release-merge.yml").read_text()
            self.assertIn("release-merge.yml@" + "a" * 40, merge)
            self.assertIn("workflows: [SDLC release gate]", merge)
            cache_workflow = (root / ".github/workflows/sdlc-cache-gc.yml").read_text()
            self.assertIn("hestia-gc.yml@" + "a" * 40, cache_workflow)
            self.assertIn("actions: write", cache_workflow)
            self.assertIn("dry_run: ${{ inputs.dry_run == true }}", cache_workflow)
            self.assertTrue((root / ".mergify.yml").exists())
            self.assertTrue((root / "renovate.json").exists())
            workflow = workflow.replace("a" * 40, "b" * 40)
            (root / ".github/workflows/sdlc.yml").write_text(workflow)
            with self.assertRaisesRegex(ValueError, "adapters are stale"):
                sdlc.install_adapters(check=True)


if __name__ == "__main__":
    unittest.main()
