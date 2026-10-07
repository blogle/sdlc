import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from contextlib import redirect_stdout

SPEC = importlib.util.spec_from_file_location("sdlc", Path(__file__).parents[1] / "src/sdlc.py")
sdlc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sdlc)
POLICY_SPEC = importlib.util.spec_from_file_location("repository_policy", Path(__file__).parents[1] / "actions/repository-policy/repository_policy.py")
repository_policy = importlib.util.module_from_spec(POLICY_SPEC)
POLICY_SPEC.loader.exec_module(repository_policy)


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
        ruleset = repository_policy.render_policy({"extra_required_status_checks": ["security / scan"]}, "main")
        self.assertEqual(ruleset["name"], "SDLC default branch")
        self.assertEqual(ruleset["conditions"]["ref_name"]["include"], ["refs/heads/main"])
        rules = {rule["type"]: rule for rule in ruleset["rules"]}
        self.assertIn("deletion", rules)
        self.assertIn("non_fast_forward", rules)
        self.assertEqual(rules["pull_request"]["parameters"]["allowed_merge_methods"], ["squash"])
        self.assertFalse(rules["required_status_checks"]["parameters"]["strict_required_status_checks_policy"])
        self.assertEqual(
            [check["context"] for check in rules["required_status_checks"]["parameters"]["required_status_checks"]],
            ["sdlc / pr-fast", "security / scan"],
        )

    def test_renderer_rejects_duplicate_or_canonical_extra_checks(self):
        with self.assertRaises(ValueError):
            repository_policy.render_policy({"extra_required_status_checks": ["dup", "dup"]}, "main")
        with self.assertRaises(ValueError):
            repository_policy.render_policy({"extra_required_status_checks": ["sdlc / pr-fast"]}, "main")

    def test_minimal_consumer_declares_only_extra_checks(self):
        declaration = json.loads((Path(__file__).parents[1] / "examples/minimal/.github/repository-policy.json").read_text())
        self.assertEqual(declaration, {"extra_required_status_checks": []})

    def test_policy_workflow_never_reconciles_pull_requests(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/reconcile-policy.yml").read_text()
        self.assertIn("if: github.event_name == 'pull_request'", workflow)
        self.assertIn("if: >-", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)
        self.assertIn("permission-administration: write", workflow)
        self.assertEqual(workflow.count("uses: $/actions/repository-policy"), 2)
        self.assertIn("actions/checkout@v4", workflow)
        self.assertIn("github.event.pull_request.head.repo.full_name", workflow)
        self.assertNotRegex(workflow, re.compile(r"repository:\s*blogle/sdlc"))
        self.assertNotRegex(workflow, re.compile(r"ref:\s*v\d"))
        self.assertIn("id: policy-credentials", workflow)
        self.assertIn("SDLC_POLICY_APP_ID: ${{ secrets.SDLC_POLICY_APP_ID }}", workflow)
        self.assertIn("SDLC_POLICY_APP_PRIVATE_KEY: ${{ secrets.SDLC_POLICY_APP_PRIVATE_KEY }}", workflow)
        self.assertEqual(workflow.count("if: steps.policy-credentials.outputs.configured == 'true'"), 2)
        self.assertIn("::warning::Live policy reconciliation is not configured", workflow)

    def test_policy_composite_uses_its_own_action_path(self):
        action = (Path(__file__).parents[1] / "actions/repository-policy/action.yml").read_text()
        self.assertIn("$GITHUB_ACTION_PATH/repository_policy.py", action)
        self.assertIn("$GITHUB_ACTION_PATH/reconcile-repository-policy.sh", action)
        self.assertIn("$GITHUB_WORKSPACE/.github/repository-policy.json", action)

    def test_mergify_policies_use_native_queue_conditions_for_admission(self):
        root = Path(__file__).parents[1]
        for path in (root / ".mergify.yml", root / "examples/minimal/.mergify.yml"):
            policy = path.read_text()
            self.assertNotIn("pull_request_rules", policy)
            self.assertNotIn("merge_protections", policy)
            self.assertNotIn("auto_merge_conditions", policy)
            self.assertNotIn("autoqueue", policy)
            self.assertIn('check-success = "sdlc / pr-fast"', policy)
            self.assertIn('check-success = "sdlc / candidate"', policy)
            self.assertIn('label = integration:auto', policy)
            self.assertIn('label != integration:review', policy)
            self.assertIn('label = integration:review', policy)
            self.assertIn('label != integration:auto', policy)
            self.assertIn('"#approved-reviews-by >= 1"', policy)
            self.assertIn("commands_restrictions:\n  queue:\n    conditions:", policy)
            self.assertIn("sender-permission >= write", policy)
            self.assertIn("sender = anvil-daemon[bot]", policy)
            self.assertEqual(policy.count('check-success = "sdlc / candidate"'), 1)

if __name__ == "__main__":
    unittest.main()
