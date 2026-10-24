from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SelfReleaseDogfoodTests(unittest.TestCase):
    def test_main_push_has_one_reusable_reconciliation_caller(self):
        caller = (ROOT / ".github/workflows/self-release.yml").read_text()
        reconcile = (ROOT / ".github/workflows/release-reconcile.yml").read_text()
        self.assertIn("push:\n    branches: [main]", caller)
        self.assertIn("uses: ./.github/workflows/release-reconcile.yml", caller)
        self.assertIn('sdlc_ref: ""', caller)
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
        self.assertNotIn("packages: write", workflow)
        self.assertNotIn("packages: read", workflow)

    def test_only_leaf_reconciler_owns_recovery_serialization(self):
        caller = (ROOT / ".github/workflows/release.yml").read_text()
        leaf = (ROOT / ".github/workflows/release-reconcile.yml").read_text()
        publisher = (ROOT / ".github/workflows/release-publish.yml").read_text()
        self.assertNotIn("sdlc-release-reconcile-${{ github.repository }}", caller)
        self.assertIn("sdlc-release-reconcile-${{ github.repository }}", leaf)
        self.assertIn("cancel-in-progress: false", leaf)
        self.assertNotIn("cancel-in-progress: true", leaf)
        self.assertIn("sdlc-release-publish-${{ github.repository }}", publisher)

    def test_compatible_v2_channel_advances_only_after_successful_source_publication(self):
        publisher = (ROOT / ".github/workflows/release-publish.yml").read_text()
        marker = "Advance the compatible v2 channel after a successful SDLC release"
        self.assertIn("if: github.repository == 'blogle/sdlc'", publisher)
        self.assertIn('[[ "${version%%.*}" == 2 ]] || exit 0', publisher)
        self.assertIn('git push --force-with-lease="refs/heads/v2:$expected"', publisher)
        self.assertGreater(publisher.index(marker), publisher.index("Record completed publication"))

    def test_release_merge_without_fragments_is_a_successful_noop_before_publication(self):
        reconcile = (ROOT / "src/release_reconcile.py").read_text()
        body = reconcile[reconcile.index("def reconcile") :]
        self.assertLess(body.index("if not _has_fragments"), body.index("_verify_predecessor"))
        self.assertIn("_retire(repo, branch, default_branch)", reconcile)

    def test_successor_fragments_stay_blocked_until_predecessor_is_published(self):
        reconcile = (ROOT / "src/release_reconcile.py").read_text()
        self.assertLess(reconcile.index("_verify_predecessor(repo"), reconcile.index("sdlc.release_snapshot"))
        self.assertIn("publication predecessor is unreconciled", reconcile)

    def test_publication_completion_wakes_reconciliation(self):
        workflow = (ROOT / ".github/workflows/self-release.yml").read_text()
        self.assertIn("workflow_run:", workflow)
        self.assertIn("workflows: [SDLC rolling release reconcile, SDLC release merge]", workflow)
        self.assertIn("types: [completed]", workflow)
        self.assertIn("WORKFLOW_EVENT: ${{ github.event.workflow_run.event }}", workflow)
        self.assertIn("WORKFLOW_NAME: ${{ github.event.workflow_run.name }}", workflow)
        self.assertIn("WORKFLOW_BRANCH: ${{ github.event.workflow_run.head_branch }}", workflow)
        self.assertIn("publisher_conclusion", workflow)
        self.assertIn('startswith("publish actual merged release /")', workflow)
        self.assertIn(".[][] | objects | select", workflow)

    def test_publication_failure_keeps_reconciliation_fail_closed(self):
        workflow = (ROOT / ".github/workflows/self-release.yml").read_text()
        reconcile = (ROOT / "src/release_reconcile.py").read_text()
        self.assertIn("types: [completed]", workflow)
        self.assertIn('"$publisher_conclusion" != skipped', workflow)
        self.assertIn("still draft", reconcile)
        self.assertIn("lacks its immutable published asset identity", reconcile)

    def test_reconcile_wakeup_cannot_loop_or_accept_irrelevant_workflows(self):
        workflow = (ROOT / ".github/workflows/self-release.yml").read_text()
        self.assertIn("WORKFLOW_NAME\" == \"SDLC release merge\"", workflow)
        self.assertIn("WORKFLOW_EVENT\" == workflow_dispatch", workflow)
        self.assertIn("WORKFLOW_BRANCH\" == main", workflow)
        self.assertIn("if: needs.publisher-completion.outputs.reconcile == 'true'", workflow)
        self.assertNotIn("SDLC self-release]", workflow)

    def test_publication_wakeup_retry_is_idempotent(self):
        workflow = (ROOT / ".github/workflows/release-reconcile.yml").read_text()
        reconcile = (ROOT / "src/release_reconcile.py").read_text()
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn('"pr", "edit"', reconcile)
        self.assertIn("--force-with-lease=refs/heads/{branch}:{old}", reconcile)

    def test_release_merge_recovery_skips_completed_publication(self):
        workflow = (ROOT / ".github/workflows/release-merge.yml").read_text()
        self.assertIn("already durably published; recovery is a no-op", workflow)
        self.assertIn("sort_by(.merged_at) | reverse", workflow)
        self.assertIn("--historical-merged-sha", workflow)

    def test_disabled_activation_blocks_reconciliation_merge_and_publication_mutations(self):
        reconcile = (ROOT / ".github/workflows/release-reconcile.yml").read_text()
        merge = (ROOT / ".github/workflows/release-merge.yml").read_text()
        publish = (ROOT / ".github/workflows/release-publish.yml").read_text()
        self.assertIn("if: vars.SDLC_RELEASE_ACTIVATE == 'true'", reconcile)
        self.assertIn("vars.SDLC_RELEASE_ACTIVATE == 'true'", merge)
        self.assertIn("inputs.activate && vars.SDLC_RELEASE_ACTIVATE == 'true'", publish)

    def test_self_release_consumer_exposes_and_runs_the_canonical_publish_app(self):
        flake = (ROOT / "flake.nix").read_text()
        contract = (ROOT / "ci.nix").read_text()
        publisher = (ROOT / "scripts/sdlc-release-publish.sh").read_text()
        self.assertIn("sdlcLib.mkConsumer", flake)
        self.assertIn("inherit publish", contract)
        self.assertIn("SDLC_RELEASE_RECEIPTS", publisher)
        self.assertIn("source_main_sha", publisher)


if __name__ == "__main__":
    unittest.main()
