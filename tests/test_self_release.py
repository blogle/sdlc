from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SelfReleaseDogfoodTests(unittest.TestCase):
    def test_main_push_has_one_reusable_reconciliation_caller(self):
        caller = (ROOT / ".github/workflows/self-release.yml").read_text()
        reconcile = (ROOT / ".github/workflows/release-reconcile.yml").read_text()
        self.assertIn("push:\n    branches: [main]", caller)
        self.assertIn("uses: ./.github/workflows/release.yml", caller)
        self.assertIn('merged_sha: ""', caller)
        self.assertIn("secrets: inherit", caller)
        self.assertNotIn("git push", caller)
        self.assertNotIn("gh release", caller)
        self.assertNotIn("  push:\n", reconcile)

    def test_integration_branch_has_no_canary_fragment_before_publication_activation(self):
        self.assertFalse((ROOT / ".changes/self-release-dogfood.json").exists())

    def test_reusable_orchestration_has_no_retired_publish_input(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertNotIn("publish_version", workflow)
        self.assertIn("if: inputs.merged_sha == ''", workflow)
        self.assertIn("if: inputs.merged_sha != ''", workflow)

    def test_only_leaf_reconciler_owns_recovery_serialization(self):
        caller = (ROOT / ".github/workflows/release.yml").read_text()
        leaf = (ROOT / ".github/workflows/release-reconcile.yml").read_text()
        publisher = (ROOT / ".github/workflows/release-publish.yml").read_text()
        self.assertNotIn("sdlc-release-reconcile-${{ github.repository }}", caller)
        self.assertIn("sdlc-release-reconcile-${{ github.repository }}", leaf)
        self.assertIn("cancel-in-progress: false", leaf)
        self.assertNotIn("cancel-in-progress: true", leaf)
        self.assertIn("sdlc-release-publish-${{ github.repository }}", publisher)

    def test_release_merge_without_fragments_is_a_successful_noop_before_publication(self):
        workflow = (ROOT / ".github/workflows/release-reconcile.yml").read_text()
        self.assertLess(workflow.index("changelog plan --json"), workflow.index("refs/tags/v$predecessor_version"))
        self.assertIn('if [[ "$(jq -r \'.release\' <<<"$plan_json")" != true ]]', workflow)

    def test_successor_fragments_stay_blocked_until_predecessor_is_published(self):
        workflow = (ROOT / ".github/workflows/release-reconcile.yml").read_text()
        self.assertLess(workflow.index("refs/tags/v$predecessor_version"), workflow.index("release snapshot --source-sha"))
        self.assertIn("publication predecessor is unreconciled", workflow)

    def test_publication_completion_wakes_reconciliation(self):
        workflow = (ROOT / ".github/workflows/self-release.yml").read_text()
        self.assertIn("workflow_run:", workflow)
        self.assertIn("workflows: [SDLC rolling release reconcile]", workflow)
        self.assertIn("types: [completed]", workflow)
        self.assertIn("WORKFLOW_EVENT: ${{ github.event.workflow_run.event }}", workflow)
        self.assertIn("WORKFLOW_BRANCH: ${{ github.event.workflow_run.head_branch }}", workflow)
        self.assertIn("publisher_conclusion", workflow)

    def test_publication_failure_keeps_reconciliation_fail_closed(self):
        workflow = (ROOT / ".github/workflows/self-release.yml").read_text()
        reconcile = (ROOT / ".github/workflows/release-reconcile.yml").read_text()
        self.assertIn("types: [completed]", workflow)
        self.assertIn('"$publisher_conclusion" != skipped', workflow)
        self.assertIn("still draft", reconcile)
        self.assertIn("lacks its immutable published asset identity", reconcile)

    def test_reconcile_wakeup_cannot_loop_or_accept_irrelevant_workflows(self):
        workflow = (ROOT / ".github/workflows/self-release.yml").read_text()
        self.assertIn("WORKFLOW_EVENT\" == workflow_dispatch", workflow)
        self.assertIn("WORKFLOW_BRANCH\" == main", workflow)
        self.assertIn("if: needs.publisher-completion.outputs.reconcile == 'true'", workflow)
        self.assertNotIn("SDLC self-release]", workflow)

    def test_publication_wakeup_retry_is_idempotent(self):
        workflow = (ROOT / ".github/workflows/release-reconcile.yml").read_text()
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("gh pr edit \"$number\"", workflow)
        self.assertIn("--force-with-lease=refs/heads/$branch:$old", workflow)


if __name__ == "__main__":
    unittest.main()
