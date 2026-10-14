"""The reusable workflow is the sole CI stage definition."""

from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class SharedCiContractTests(unittest.TestCase):
    def test_reusable_workflow_defines_fast_verify_and_policy_jobs(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self_workflow = (ROOT / ".github/workflows/ci-self.yml").read_text()
        stage = (ROOT / ".github/workflows/stage.yml").read_text()
        self.assertIn("workflow_call:", workflow)
        self.assertIn("stage.yml", workflow)
        self.assertIn("merge_group:", self_workflow)
        self.assertIn("uses: ./.github/workflows/ci.yml", self_workflow)
        self.assertIn("ci.x86_64-linux.${{ inputs.stage == 'check' && 'fast' || 'verify' }}", stage)
        self.assertIn("github:blogle/sdlc/${{ inputs.sdlc_ref }}#nix-eval-jobs", stage)
        self.assertIn("Mic92/hestia@v3", stage)
        self.assertIn("Mic92/hestia/matrix@v3", stage)
        self.assertNotIn("sdlc / ${{ inputs.stage }}", stage)
        self.assertIn("name: check", workflow)
        self.assertIn("name: verify", workflow)
        self.assertIn("name: policy", workflow)
        self.assertIn("nix run .#sdlc -- install --check", workflow)

    def test_full_verification_runs_only_for_merge_queue_candidates(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        verify = workflow.split("  verify:", 1)[1].split("  policy:", 1)[0]
        self.assertIn("github.event_name == 'merge_group'", verify)
        self.assertIn("mergify/merge-queue/", verify)
        self.assertIn("github.event.repository.default_branch", verify)

    def test_hestia_gate_requires_all_evaluated_builds_and_accepts_empty_cached_matrix(self):
        stage = (ROOT / ".github/workflows/stage.yml").read_text()
        gate = stage.split("  gate:", 1)[1]
        self.assertIn("test \"$EVALUATE_RESULT\" = success", gate)
        self.assertIn('if [[ "$ANY_JOBS" == true ]]; then', gate)
        self.assertIn('test "$BUILD_RESULT" = success', gate)
        self.assertIn('test "$BUILD_RESULT" = skipped', gate)
        self.assertIn("fail-fast: false", stage)
        self.assertIn('nix build -L "${installables[@]}"', stage)

    def test_release_specific_status_is_not_a_global_required_context(self):
        ruleset = (ROOT / "rulesets/default-branch.json").read_text()
        self.assertIn('"context": "sdlc / check", "integration_id": 15368', ruleset)
        self.assertIn('"context": "sdlc / policy", "integration_id": 15368', ruleset)
        self.assertNotIn('"context": "sdlc / release-gate"', ruleset)
        self.assertNotIn("IS_RELEASE", (ROOT / ".github/workflows/ci.yml").read_text())

    def test_mergify_queues_after_fast_check_and_merges_after_verify(self):
        policy = (ROOT / ".mergify.yml").read_text()
        self.assertIn("check-success = @github-actions/sdlc / check", policy)
        self.assertIn("check-success = @github-actions/sdlc / verify", policy)
        self.assertIn("label != release", policy)
