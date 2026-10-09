import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
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
API_SPEC = importlib.util.spec_from_file_location("ruleset_api", Path(__file__).parents[1] / "actions/repository-policy/ruleset_api.py")
ruleset_api = importlib.util.module_from_spec(API_SPEC)
API_SPEC.loader.exec_module(ruleset_api)


class ChangelogTests(unittest.TestCase):
    def _git_repo(self, temp, fragments):
        root = Path(temp)
        (root / ".changes").mkdir(parents=True)
        if not fragments:
            (root / ".changes" / ".keep").write_text("")
        for name, item in fragments.items():
            (root / ".changes" / f"{name}.json").write_text(json.dumps(item, sort_keys=True) + "\n")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "source"], cwd=root, check=True,
            env={**sdlc.os.environ, "GIT_AUTHOR_DATE": "2026-10-09T00:00:00Z", "GIT_COMMITTER_DATE": "2026-10-09T00:00:00Z"},
        )
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()

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

    def test_snapshot_coalesces_all_fragments_and_uses_strongest_semver(self):
        with tempfile.TemporaryDirectory() as temp:
            source = self._git_repo(temp, {
                "a": {"type": "fix", "semver": "patch", "summary": "a"},
                "b": {"type": "feature", "semver": "minor", "summary": "b"},
                "c": {"type": "breaking", "semver": "major", "summary": "c"},
            })
            root = sdlc.ROOT
            sdlc.ROOT = Path(temp)
            try:
                with redirect_stdout(io.StringIO()):
                    manifest = sdlc.release_snapshot(source, date="2026-10-09")
                self.assertEqual(manifest["schema"], 1)
                self.assertIn("generated_tree", manifest)
                self.assertNotIn("generated_tree_sha256", manifest)
                self.assertEqual(manifest["publication"], {"artifacts": [], "build_command": "true"})
                self.assertEqual(manifest["version"], "1.0.0")
                self.assertEqual([item["path"] for item in manifest["fragments"]], [".changes/a.json", ".changes/b.json", ".changes/c.json"])
                self.assertEqual([item["blob_sha"] for item in manifest["fragments"]], [
                    subprocess.run(["git", "rev-parse", f"{source}:.changes/{name}.json"], cwd=temp, check=True, capture_output=True, text=True).stdout.strip()
                    for name in ("a", "b", "c")
                ])
                self.assertFalse(any((Path(temp) / ".changes" / f"{name}.json").exists() for name in ("a", "b", "c")))
                self.assertEqual(manifest["changelog_sha256"], sdlc._sha256(Path(temp) / "CHANGELOG.md"))
            finally:
                sdlc.ROOT = root

    def test_merged_snapshot_tree_is_reconstructibly_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            source = self._git_repo(temp, {"one": {"type": "fix", "semver": "patch", "summary": "one"}})
            root = sdlc.ROOT
            sdlc.ROOT = Path(temp)
            try:
                with redirect_stdout(io.StringIO()):
                    manifest = sdlc.release_snapshot(source, date="2026-10-09")
                subprocess.run(["git", "add", "CHANGELOG.md", ".sdlc/release.json", ".changes"], cwd=temp, check=True)
                subprocess.run(["git", "config", "user.name", "test"], cwd=temp, check=True)
                subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=temp, check=True)
                subprocess.run(["git", "commit", "-qm", "release candidate"], cwd=temp, check=True)
                merged = subprocess.run(["git", "rev-parse", "HEAD"], cwd=temp, check=True, capture_output=True, text=True).stdout.strip()
                self.assertTrue(sdlc.verify_release_tree(source, merged, manifest))
            finally:
                sdlc.ROOT = root

    def test_snapshot_is_reproducible_for_identical_source_trees(self):
        fragments = {"same": {"type": "feature", "semver": "minor", "summary": "same"}}
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first"
            second = Path(temp) / "second"
            source = self._git_repo(first, fragments)
            self._git_repo(second, fragments)
            outputs = []
            for checkout in (first, second):
                root = sdlc.ROOT
                sdlc.ROOT = checkout
                try:
                    with redirect_stdout(io.StringIO()):
                        manifest = sdlc.release_snapshot(source, date="2026-10-09")
                    outputs.append((manifest, (checkout / "CHANGELOG.md").read_bytes()))
                finally:
                    sdlc.ROOT = root
            self.assertEqual(outputs[0], outputs[1])

    def test_snapshot_uses_published_manifest_as_successor_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            source = self._git_repo(temp, {})
            root_path = Path(temp)
            manifest_path = root_path / ".sdlc/release.json"
            manifest_path.parent.mkdir()
            manifest_path.write_text(json.dumps({"schema": 1, "version": "1.0.0", "source_main_sha": source}) + "\n")
            subprocess.run(["git", "add", ".sdlc/release.json"], cwd=temp, check=True)
            subprocess.run(["git", "commit", "-qm", "published release boundary"], cwd=temp, check=True)
            (root_path / ".changes").mkdir(exist_ok=True)
            (root_path / ".changes/next.json").write_text(json.dumps({"type": "feature", "semver": "minor", "summary": "next"}) + "\n")
            subprocess.run(["git", "add", ".changes/next.json"], cwd=temp, check=True)
            subprocess.run(["git", "commit", "-qm", "next source"], cwd=temp, check=True)
            latest = subprocess.run(["git", "rev-parse", "HEAD"], cwd=temp, check=True, capture_output=True, text=True).stdout.strip()
            old_root = sdlc.ROOT
            sdlc.ROOT = root_path
            try:
                with redirect_stdout(io.StringIO()):
                    manifest = sdlc.release_snapshot(latest, date="2026-10-10")
                self.assertEqual(manifest["version"], "1.1.0")
                self.assertEqual(manifest["prior_released_boundary"], source)
            finally:
                sdlc.ROOT = old_root


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

    def test_sdlc_source_ci_emits_stable_candidate_only_for_speculative_prs(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/ci.yml").read_text()
        self.assertIn("candidate-context:\n    name: sdlc / candidate", workflow)
        self.assertIn("needs: candidate", workflow)
        self.assertIn("if: always() && github.event_name == 'pull_request' && startsWith(github.event.pull_request.head.ref, 'mergify/merge-queue/')", workflow)
        self.assertIn('test "${{ needs.candidate.result }}" = success', workflow)
        self.assertNotIn("merge_group", workflow)

    def test_nested_stage_workflows_follow_outer_exact_revision(self):
        root = Path(__file__).parents[1]
        for name in ("pr-fast.yml", "candidate.yml"):
            wrapper = (root / ".github/workflows" / name).read_text()
            self.assertIn("uses: $/.github/workflows/stage.yml", wrapper)
            self.assertNotRegex(wrapper, re.compile(r"blogle/sdlc/.+stage\.yml@"))

    def test_release_reconcile_is_app_authenticated_and_lease_safe(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/release-reconcile.yml").read_text()
        self.assertIn("actions/create-github-app-token@v1", workflow)
        self.assertIn("sdlc/release-next", workflow)
        self.assertIn("--force-with-lease=refs/heads/$branch:$old", workflow)
        self.assertNotIn("HEAD:main", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("policy check --repo", workflow)
        self.assertIn("ruleset has been applied and read back successfully", workflow)
        self.assertNotIn("committed release manifest awaiting publication", workflow)
        for field in ("schema=1", "prior_released_boundary", "source_main_sha", "generated_tree", "changelog_sha256", "publication{artifacts, build_command}"):
            self.assertIn(field, workflow)
        self.assertNotIn("committed release manifest awaiting publication", workflow)

    def test_release_reconcile_checks_tag_and_publication_ledger_for_successors(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/release-reconcile.yml").read_text()
        self.assertIn("refs/tags/v$predecessor_version", workflow)
        self.assertIn("gh release view", workflow)
        self.assertIn("git merge-base --is-ancestor \"$tag_sha\" \"$baseline\"", workflow)

    def test_release_reconcile_retries_races_without_competing_prs(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/release-reconcile.yml").read_text()
        self.assertIn("for attempt in 1 2 3", workflow)
        self.assertIn("release branch changed concurrently", workflow)
        self.assertEqual(workflow.count("gh pr list --repo \"$repo\" --state open --base main --head \"$branch\""), 2)

    def test_release_reconcile_is_idempotent_for_the_same_snapshot(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/release-reconcile.yml").read_text()
        self.assertIn("GIT_AUTHOR_DATE=\"$source_date\" GIT_COMMITTER_DATE=\"$source_date\"", workflow)
        self.assertIn("gh pr edit \"$number\"", workflow)
        self.assertNotIn("gh pr create", workflow.split("if [[ -n \"$number\" ]]", 1)[0])


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

    def test_check_uses_desired_fields_and_ignores_unowned_api_defaults(self):
        desired = repository_policy.render_policy({}, "main")
        api_detail = json.loads(json.dumps(desired))
        pr_rule = next(rule for rule in api_detail["rules"] if rule["type"] == "pull_request")
        pr_rule["parameters"]["required_reviewers"] = []
        pr_rule["parameters"]["require_extra_approval_for_unattributed_changes"] = True
        checks = next(rule for rule in api_detail["rules"] if rule["type"] == "required_status_checks")
        checks["parameters"]["required_status_checks"][0]["integration_id"] = 123
        repository_policy.check_live(api_detail, desired, "owner/repo")

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
        self.assertIn("uses: $/actions/repository-policy", workflow)
        self.assertNotIn("actions/repository-policy/policy_check.py", workflow)
        action = (root / "actions/repository-policy/action.yml").read_text()
        self.assertIn("$GITHUB_ACTION_PATH/policy_check.py", action)
        self.assertFalse((root / ".github/workflows/reconcile-policy.yml").exists())
        caller = (root / "examples/minimal/.github/workflows/ci.yml").read_text()
        self.assertIn("name: sdlc / policy", caller)
        self.assertIn("if: always()", caller)
        self.assertIn("test \"${{ needs.policy.result }}\" = success", caller)
        self.assertNotIn("SDLC_POLICY_APP", caller)

    def test_external_consumer_check_needs_no_sdlc_scripts_in_caller_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".github/workflows").mkdir(parents=True)
            (root / ".github/repository-policy.json").write_text('{"require_policy_check": false}')
            (root / ".github/workflows/ci.yml").write_text(
                "uses: blogle/sdlc/.github/workflows/policy-check.yml@v1.2.3\nname: sdlc / policy\n"
            )
            self.assertFalse((root / "actions/repository-policy").exists())
            env = {
                "GITHUB_REPOSITORY": "owner/consumer",
                "DEFAULT_BRANCH": "main",
                "EVENT_NAME": "pull_request",
                "BASE_SHA": "base-sha",
            }
            with patch.dict(policy_check.os.environ, env, clear=False), \
                 patch.object(policy_check.Path, "cwd", return_value=root), \
                 patch.object(policy_check.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "missing base config")), \
                 patch.object(policy_check, "read_live_rulesets", return_value=[]), \
                 redirect_stdout(io.StringIO()) as output:
                policy_check.main()
            self.assertIn("FIRST-ONBOARDING", output.getvalue())

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

    def test_list_summary_is_followed_by_full_detail_fetch(self):
        desired = repository_policy.render_policy({}, "main")
        summary = {"id": 42, "name": "SDLC default branch", "source": "owner/repo", "enforcement": "active"}
        detail = dict(desired, id=42, source="owner/repo")
        responses = [
            subprocess.CompletedProcess([], 0, json.dumps([[summary]]), ""),
            subprocess.CompletedProcess([], 0, json.dumps(detail), ""),
        ]
        with patch.object(ruleset_api.subprocess, "run", side_effect=responses) as run:
            found, anonymous = ruleset_api.read_rulesets("owner/repo")
        self.assertFalse(anonymous)
        self.assertEqual(found, [detail])
        self.assertIn("--paginate", run.call_args_list[0].args[0])
        self.assertIn("repos/owner/repo/rulesets/42", run.call_args_list[1].args[0])

    def test_paginated_list_keeps_legacy_rulesets_and_fetches_canonical_detail(self):
        legacy = {"id": 1, "name": "legacy", "source": "owner/repo"}
        summary = {"id": 42, "name": "SDLC default branch", "source": "owner/repo"}
        detail = dict(repository_policy.render_policy({}, "main"), id=42, source="owner/repo")
        with patch.object(ruleset_api.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, json.dumps([[legacy], [summary]]), ""),
            subprocess.CompletedProcess([], 0, json.dumps(detail), ""),
        ]):
            records, _ = ruleset_api.read_rulesets("owner/repo", include_parents=True)
        self.assertEqual(records, [legacy, detail])

    def test_read_only_ruleset_api_paginates_and_falls_back_anonymously_without_hiding_errors(self):
        gh_result = subprocess.CompletedProcess([], 0, "[[]]", "")
        with patch.object(ruleset_api.subprocess, "run", return_value=gh_result) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(policy_check.read_live_rulesets("owner/repo"), [])
        self.assertIn("--paginate", run.call_args.args[0])
        denied = subprocess.CalledProcessError(1, ["gh", "api"], "", "HTTP 403")
        with patch.object(sdlc.ruleset_api.subprocess, "run", side_effect=denied), \
             patch.object(sdlc.ruleset_api, "_anonymous_pages", return_value=[]), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(policy_check.read_live_rulesets("owner/repo"), [])
        with patch.object(sdlc.ruleset_api.subprocess, "run", side_effect=denied), \
             patch.object(sdlc.ruleset_api, "_anonymous_pages", side_effect=OSError("network denied")), \
             self.assertRaisesRegex(OSError, "network denied"):
            policy_check.read_live_rulesets("owner/repo")

    def test_local_policy_apply_is_idempotent_and_verifies_read_after_write(self):
        desired = repository_policy.render_policy({}, "main")
        detail = dict(desired, id=23, source="owner/repo")
        legacy = {"id": 90, "name": "legacy rules", "source": "owner/repo"}
        applied = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(sdlc, "policy_context", return_value=("owner/repo", "main")), \
             patch.object(sdlc, "policy_config", return_value={}), \
             patch.object(sdlc.ruleset_api, "read_rulesets", side_effect=[([legacy], False), ([legacy, detail], False)]), \
             patch.object(sdlc.subprocess, "run", return_value=applied) as run, \
             redirect_stdout(io.StringIO()):
            sdlc.policy_command("apply", "owner/repo")
        self.assertTrue(any("POST" in call.args[0] for call in run.call_args_list))
        self.assertFalse(any("PUT" in call.args[0] or "DELETE" in call.args[0] for call in run.call_args_list))

    def test_local_policy_apply_updates_only_after_full_detail_comparison(self):
        desired = repository_policy.render_policy({}, "main")
        current = dict(desired, id=23, source="owner/repo", enforcement="disabled")
        verified = dict(desired, id=23, source="owner/repo")
        with patch.object(sdlc, "policy_context", return_value=("owner/repo", "main")), \
             patch.object(sdlc, "policy_config", return_value={}), \
             patch.object(sdlc.ruleset_api, "read_rulesets", side_effect=[([current], False), ([verified], False)]), \
             patch.object(sdlc.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run, \
             redirect_stdout(io.StringIO()):
            sdlc.policy_command("apply", "owner/repo")
        self.assertTrue(any("PUT" in call.args[0] for call in run.call_args_list))

    def test_local_policy_apply_is_noop_for_full_matching_details(self):
        desired = repository_policy.render_policy({}, "main")
        detail = dict(desired, id=23, source="owner/repo")
        pr_rule = next(rule for rule in detail["rules"] if rule["type"] == "pull_request")
        pr_rule["parameters"]["required_reviewers"] = []
        pr_rule["parameters"]["require_extra_approval_for_unattributed_changes"] = True
        with patch.object(sdlc, "policy_context", return_value=("owner/repo", "main")), \
             patch.object(sdlc, "policy_config", return_value={}), \
             patch.object(sdlc.ruleset_api, "read_rulesets", return_value=([detail], False)), \
             patch.object(sdlc.subprocess, "run") as run, \
             redirect_stdout(io.StringIO()):
            sdlc.policy_command("apply", "owner/repo")
        run.assert_not_called()

    def test_local_policy_check_is_read_only_and_duplicate_apply_refuses_to_guess(self):
        desired = repository_policy.render_policy({}, "main")
        with patch.object(sdlc, "policy_context", return_value=("owner/repo", "main")), \
             patch.object(sdlc, "policy_config", return_value={}), \
             patch.object(sdlc.ruleset_api, "read_rulesets", return_value=([dict(desired, id=1, source="owner/repo")], False)) as read, \
             patch.object(sdlc.subprocess, "run") as run, \
             redirect_stdout(io.StringIO()):
            sdlc.policy_command("check", "owner/repo")
        read.assert_called_once()
        run.assert_not_called()
        duplicate = [dict(desired, id=1, source="owner/repo"), dict(desired, id=2, source="owner/repo")]
        with patch.object(sdlc, "policy_context", return_value=("owner/repo", "main")), \
             patch.object(sdlc, "policy_config", return_value={}), \
             patch.object(sdlc.ruleset_api, "read_rulesets", return_value=(duplicate, False)), \
             patch.object(sdlc.subprocess, "run") as duplicate_write, \
             self.assertRaisesRegex(ValueError, "resolve duplicates manually"):
            sdlc.policy_command("apply", "owner/repo")
        duplicate_write.assert_not_called()

    def test_legacy_rulesets_are_reported_and_never_replaced(self):
        legacy = {"id": 90, "name": "old policy", "source": "owner/repo"}
        output = io.StringIO()
        with patch.object(sdlc, "policy_context", return_value=("owner/repo", "main")), \
             patch.object(sdlc, "policy_config", return_value={}), \
             patch.object(sdlc.ruleset_api, "read_rulesets", return_value=([legacy], False)), \
             redirect_stdout(output):
            sdlc.policy_command("plan", "owner/repo")
        self.assertEqual(json.loads(output.getvalue())["unmanaged_rulesets"], [legacy])

    def test_policy_plan_fails_on_ruleset_read_error_instead_of_assuming_missing(self):
        with patch.object(sdlc, "policy_context", return_value=("owner/repo", "main")), \
             patch.object(sdlc, "policy_config", return_value={}), \
             patch.object(sdlc.ruleset_api, "read_rulesets", side_effect=OSError("API unavailable")), \
             self.assertRaisesRegex(OSError, "API unavailable"):
            sdlc.policy_command("plan", "owner/repo")

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
