import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("sdlc", Path(__file__).parents[1] / "src/sdlc.py")
sdlc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sdlc)
POLICY_SPEC = importlib.util.spec_from_file_location("repository_policy", Path(__file__).parents[1] / "actions/repository-policy/repository_policy.py")
repository_policy = importlib.util.module_from_spec(POLICY_SPEC)
POLICY_SPEC.loader.exec_module(repository_policy)
CHECK_SPEC = importlib.util.spec_from_file_location("policy_check", Path(__file__).parents[1] / "actions/repository-policy/policy_check.py")
policy_check = importlib.util.module_from_spec(CHECK_SPEC)
CHECK_SPEC.loader.exec_module(policy_check)


class ChangelogTests(unittest.TestCase):
    def test_zero_fragments_and_finalize_compacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = sdlc.ROOT
            sdlc.ROOT = Path(temp)
            try:
                (Path(temp) / ".changes").mkdir()
                sdlc.changelog("finalize")
                frag = Path(temp) / ".changes/feature.json"
                frag.write_text(json.dumps({"type": "feature", "semver": "minor", "summary": "Added a capability"}))
                sdlc.changelog("finalize", version="0.1.0", date="2026-10-03")
                self.assertIn("## [0.1.0] - 2026-10-03", (Path(temp) / "CHANGELOG.md").read_text())
                self.assertFalse(frag.exists())
            finally:
                sdlc.ROOT = root

    def test_aggregate_bump_is_max_semver(self):
        entries = [(Path("patch.json"), {"type": "fix", "semver": "patch", "summary": "x"}), (Path("major.json"), {"type": "breaking", "semver": "major", "summary": "y"})]
        self.assertEqual(sdlc.bump_intent(entries), "major")

    def test_finalize_consumes_only_selected_merge_fragments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = sdlc.ROOT
            sdlc.ROOT = Path(temp)
            try:
                changes = Path(temp) / ".changes"
                changes.mkdir()
                first = changes / "first.json"
                second = changes / "second.json"
                first.write_text(json.dumps({"type": "feature", "semver": "minor", "summary": "first PR"}))
                second.write_text(json.dumps({"type": "fix", "semver": "patch", "summary": "second PR"}))
                sdlc.changelog("finalize", version="0.1.0", date="2026-10-05", selected=[".changes/first.json"])
                self.assertFalse(first.exists())
                self.assertTrue(second.exists())
                changelog = (Path(temp) / "CHANGELOG.md").read_text()
                self.assertIn("first PR", changelog)
                self.assertNotIn("second PR", changelog)
            finally:
                sdlc.ROOT = root

    def test_plan_and_finalize_are_scoped_to_one_pr_and_reject_stale_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = sdlc.ROOT
            sdlc.ROOT = Path(temp)
            try:
                changes = Path(temp) / ".changes"
                changes.mkdir()
                first = changes / "first.json"
                second = changes / "second.json"
                first.write_text(json.dumps({"type": "feature", "semver": "minor", "summary": "first PR"}))
                second.write_text(json.dumps({"type": "breaking", "semver": "major", "summary": "second PR"}))
                output = io.StringIO()
                with redirect_stdout(output):
                    sdlc.changelog("plan", json_output=True, selected=[".changes/first.json"])
                self.assertEqual(json.loads(output.getvalue()), {"bump": "minor", "release": True, "version": "0.1.0"})
                with self.assertRaises(ValueError):
                    sdlc.fragments([".changes/already-consumed.json"])
            finally:
                sdlc.ROOT = root


class ConsumerWorkflowTests(unittest.TestCase):
    def test_stable_required_checks_are_local_always_gates(self):
        workflow = (Path(__file__).parents[1] / "examples/minimal/.github/workflows/ci.yml").read_text()
        self.assertIn("name: sdlc / pr-fast", workflow)
        self.assertIn("needs: pr-fast", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn('test \"${{ needs.pr-fast.result }}\" = success', workflow)
        self.assertIn("name: sdlc / candidate", workflow)
        self.assertIn("needs: candidate", workflow)
        self.assertIn("if: always() && (github.event_name == 'merge_group' || startsWith(github.event.pull_request.head.ref, 'mergify/merge-queue/'))", workflow)
        self.assertIn('test \"${{ needs.candidate.result }}\" = success', workflow)

    def test_nested_stage_workflows_follow_outer_exact_revision(self):
        root = Path(__file__).parents[1]
        for name in ("pr-fast.yml", "candidate.yml"):
            wrapper = (root / ".github/workflows" / name).read_text()
            self.assertIn("uses: $/.github/workflows/stage.yml", wrapper)
            self.assertNotRegex(wrapper, re.compile(r"blogle/sdlc/.+stage\.yml@"))


class RepositoryPolicyTests(unittest.TestCase):
    def test_renderer_builds_complete_canonical_ruleset(self):
        ruleset = repository_policy.render_policy({"extra_required_status_checks": ["security / scan"], "require_policy_check": True}, "main")
        self.assertEqual(ruleset["name"], "SDLC default branch")
        self.assertEqual(ruleset["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH"])
        rules = {rule["type"]: rule for rule in ruleset["rules"]}
        self.assertIn("deletion", rules)
        self.assertIn("non_fast_forward", rules)
        self.assertEqual(rules["pull_request"]["parameters"]["allowed_merge_methods"], ["squash"])
        self.assertFalse(rules["required_status_checks"]["parameters"]["strict_required_status_checks_policy"])
        self.assertEqual(
            [check["context"] for check in rules["required_status_checks"]["parameters"]["required_status_checks"]],
            ["sdlc / pr-fast", "sdlc / policy", "security / scan"],
        )

    def test_default_branch_alias_handles_main_and_master(self):
        for branch in ("main", "master"):
            rendered = repository_policy.render_policy({}, branch)
            self.assertEqual(rendered["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH"])

    def test_policy_check_context_is_activated_only_by_declaration_flag(self):
        for enabled, expected in ((False, ["sdlc / pr-fast"]), (True, ["sdlc / pr-fast", "sdlc / policy"])):
            rendered = repository_policy.render_policy({"require_policy_check": enabled}, "main")
            checks = next(rule for rule in rendered["rules"] if rule["type"] == "required_status_checks")
            self.assertEqual([item["context"] for item in checks["parameters"]["required_status_checks"]], expected)
        with self.assertRaisesRegex(ValueError, "require_policy_check must be a boolean"):
            repository_policy.render_policy({"require_policy_check": "yes"}, "main")

    def test_check_fails_closed_for_missing_skew_and_hidden_bypass(self):
        desired = repository_policy.render_policy({}, "main")
        with self.assertRaisesRegex(ValueError, "policy apply --repo owner/repo"):
            repository_policy.check_live(None, desired, "owner/repo")
        skew = dict(desired, enforcement="disabled")
        with self.assertRaisesRegex(ValueError, "drift detected"):
            repository_policy.check_live(skew, desired, "owner/repo")
        hidden = dict(desired)
        hidden.pop("bypass_actors")
        warning = io.StringIO()
        with redirect_stderr(warning):
            repository_policy.check_live(hidden, desired, "owner/repo")
        self.assertIn("bypass configuration was not verified", warning.getvalue())

    def test_renderer_rejects_duplicate_or_canonical_extra_checks(self):
        with self.assertRaises(ValueError):
            repository_policy.render_policy({"extra_required_status_checks": ["dup", "dup"]}, "main")
        with self.assertRaises(ValueError):
            repository_policy.render_policy({"extra_required_status_checks": ["sdlc / pr-fast"]}, "main")

    def test_minimal_consumer_declares_only_extra_checks(self):
        declaration = json.loads((Path(__file__).parents[1] / "examples/minimal/.github/repository-policy.json").read_text())
        self.assertEqual(declaration, {"extra_required_status_checks": [], "require_policy_check": True})

    def test_policy_workflow_is_read_only_and_stable_check_is_not_skipped(self):
        root = Path(__file__).parents[1]
        workflow = (root / ".github/workflows/policy-check.yml").read_text()
        self.assertIn("contents: read", workflow)
        self.assertNotIn("administration: write", workflow)
        self.assertFalse((root / ".github/workflows/reconcile-policy.yml").exists())
        caller = (root / "examples/minimal/.github/workflows/ci.yml").read_text()
        self.assertIn("name: sdlc / policy", caller)
        self.assertIn("if: always()", caller)
        self.assertIn("test \"${{ needs.policy.result }}\" = success", caller)
        self.assertNotIn("SDLC_POLICY_APP", caller)

    def test_pr_check_validates_proposal_but_uses_base_declaration(self):
        desired = repository_policy.render_policy({}, "main")
        live = [dict(desired, source="owner/repo")]
        with redirect_stdout(io.StringIO()):
            outcome = policy_check.evaluate_policy_check(
                {"extra_required_status_checks": ["new"]}, {}, live, True, "owner/repo", "main", True
            )
        self.assertEqual(outcome, "drift-check")
        with self.assertRaisesRegex(ValueError, "must not contain duplicates"):
            policy_check.evaluate_policy_check(
                {"extra_required_status_checks": ["dup", "dup"]}, {}, live, True, "owner/repo", "main", True
            )

    def test_initial_onboarding_has_explicit_interim_outcome_only_without_live_policy(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = policy_check.evaluate_policy_check({"require_policy_check": False}, None, [], True, "owner/repo", "main", True)
        self.assertEqual(result, "first-onboarding")
        self.assertIn("FIRST-ONBOARDING", output.getvalue())
        self.assertIn("does NOT prove live repository protection", output.getvalue())
        self.assertIn("policy apply --repo owner/repo", output.getvalue())
        root = Path(__file__).parents[1]
        self.assertTrue(policy_check.has_policy_caller(root))
        with self.assertRaisesRegex(ValueError, "must install the policy-check.yml caller"):
            policy_check.evaluate_policy_check({"require_policy_check": False}, None, [], False, "owner/repo", "main", True)
        already_live = [dict(repository_policy.render_policy({"require_policy_check": False}, "main"), source="owner/repo")]
        with self.assertRaisesRegex(ValueError, "not first-time onboarding"):
            policy_check.evaluate_policy_check({"require_policy_check": False}, None, already_live, True, "owner/repo", "main", True)

    def test_existing_onboarded_base_cannot_delete_or_lose_declaration(self):
        with self.assertRaisesRegex(ValueError, "policy declaration was removed"):
            policy_check.evaluate_policy_check(None, {}, [], True, "owner/repo", "main", True)
        with self.assertRaisesRegex(ValueError, "canonical ruleset is missing"):
            policy_check.evaluate_policy_check({}, {}, [], True, "owner/repo", "main", True)

    def test_existing_live_ruleset_drift_fails_against_base_policy(self):
        desired = repository_policy.render_policy({}, "main")
        live = [dict(desired, source="owner/repo", enforcement="disabled")]
        with self.assertRaisesRegex(ValueError, "drift detected"):
            policy_check.evaluate_policy_check({}, {}, live, True, "owner/repo", "main", True)

    def test_read_only_ruleset_api_uses_gh_then_public_anonymous_fallback(self):
        gh_result = type("Result", (), {"returncode": 0, "stdout": "[]"})()
        with patch.object(policy_check.subprocess, "run", return_value=gh_result) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(policy_check.read_live_rulesets("owner/repo"), [])
        self.assertIn("gh", run.call_args.args[0])
        denied = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "HTTP 403"})()
        response = io.StringIO("[]")
        with patch.object(policy_check.subprocess, "run", return_value=denied), \
             patch.object(policy_check.urllib.request, "urlopen", return_value=response), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(policy_check.read_live_rulesets("owner/repo"), [])

    def test_local_policy_apply_is_idempotent_and_verifies_read_after_write(self):
        desired = repository_policy.render_policy({}, "main")
        desired_with_id = dict(desired, id=23)
        empty = type("Result", (), {"stdout": "[]"})()
        applied = type("Result", (), {"stdout": ""})()
        verified = type("Result", (), {"stdout": json.dumps([desired_with_id])})()
        with patch.object(sdlc, "policy_context", return_value=("owner/repo", "main")), \
             patch.object(sdlc, "policy_config", return_value={}), \
             patch.object(sdlc.subprocess, "run", side_effect=[empty, applied, verified]) as run, \
             redirect_stdout(io.StringIO()):
            sdlc.policy_command("apply", "owner/repo")
        self.assertTrue(any("POST" in call.args[0] for call in run.call_args_list))
        self.assertTrue(any("rulesets?per_page=100" in call.args[0][-1] for call in run.call_args_list))

    def test_local_policy_check_is_read_only_and_duplicate_apply_refuses_to_guess(self):
        desired = repository_policy.render_policy({}, "main")
        with patch.object(sdlc, "policy_context", return_value=("owner/repo", "main")), \
             patch.object(sdlc, "policy_config", return_value={}), \
             patch.object(sdlc.subprocess, "run", return_value=type("Result", (), {"stdout": json.dumps([desired])})()) as run, \
             redirect_stdout(io.StringIO()):
            sdlc.policy_command("check", "owner/repo")
        self.assertEqual(len(run.call_args_list), 1)
        self.assertNotIn("--method", run.call_args.args[0])
        duplicate = [dict(desired, id=1), dict(desired, id=2)]
        with patch.object(sdlc, "policy_context", return_value=("owner/repo", "main")), \
             patch.object(sdlc, "policy_config", return_value={}), \
             patch.object(sdlc.subprocess, "run", return_value=type("Result", (), {"stdout": json.dumps(duplicate)})()), \
             self.assertRaisesRegex(ValueError, "resolve duplicates manually"):
            sdlc.policy_command("apply", "owner/repo")

    def test_mergify_policies_use_native_queue_conditions_for_admission(self):
        root = Path(__file__).parents[1]
        fixture = (root / "examples/minimal/.mergify.yml").read_text()
        self.assertIn("extends: sdlc", fixture)
        self.assertNotIn("queue_rules:", fixture)
        for path in (root / ".mergify.yml",):
            policy = path.read_text()
            self.assertIn("pull_request_rules:", policy)
            self.assertIn("- name: automatically queue validated candidates", policy)
            self.assertIn("actions:\n      queue:\n        name: validated candidates", policy)
            self.assertNotIn("merge_protections", policy)
            self.assertNotIn("auto_merge_conditions", policy)
            self.assertNotIn("autoqueue", policy)
            auto_conditions = re.search(r"(?ms)^    conditions:\n(.*?)(?=^    actions:)", policy).group(1)
            queue_conditions = re.search(r"(?ms)^    queue_conditions:\n(.*?)(?=^    merge_conditions:)", policy).group(1)
            self.assertEqual(auto_conditions, queue_conditions)
            self.assertIn('check-success = "sdlc / candidate"', policy)
            for condition in (
                "base ~= ^(main|master)$",
                "label = integration:auto",
                "label != integration:review",
                "label = integration:review",
                "label != integration:auto",
                '"#approved-reviews-by >= 1"',
            ):
                self.assertEqual(policy.count(condition), 2)
            self.assertEqual(policy.count("name: validated candidates"), 2)
            self.assertIn("commands_restrictions:\n  queue:\n    conditions:", policy)
            self.assertIn("sender-permission >= write", policy)
            self.assertIn("sender = anvil-daemon[bot]", policy)
            self.assertEqual(policy.count('check-success = "sdlc / candidate"'), 1)
            self.assertIn("queue_controls_comment: true", policy)
            self.assertIn("status_comments: all", policy)

if __name__ == "__main__":
    unittest.main()
