"""Regression guards for Hestia cache wiring and PR/default-branch isolation."""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text()


class HestiaCacheWorkflowTests(unittest.TestCase):
    def test_filters_upstream_in_eval_and_build_jobs(self):
        workflow = source(".github/workflows/stage.yml")
        self.assertEqual(workflow.count("uses: Mic92/hestia@v3"), 2)
        self.assertEqual(workflow.count("upstream-cache-filter: true"), 2)
        self.assertEqual(
            workflow.count('upstream-cache-key-names: "cache.nixos.org-1 nix-community.cachix.org-1"'),
            2,
        )
        # Derivation closures must be filtered in the evaluating job only.
        self.assertEqual(workflow.count("filter-drv-closures: true"), 1)
        self.assertIn('"$HESTIA_BIN" prefetch ${{ matrix.installables }}', workflow)
        self.assertIn("wait-manifest-version: ${{ needs.evaluate.outputs.manifest-version }}", workflow)

    def test_sdlc_populates_default_branch_cache(self):
        workflow = source(".github/workflows/ci.yml")
        self.assertIn("push:\n    branches: [main, master]", workflow)
        self.assertIn("cache-warm:", workflow)
        self.assertIn("uses: ./.github/workflows/candidate.yml", workflow)
        self.assertIn("github.event_name == 'push' && github.ref == format(", workflow)

    def test_consumer_examples_publish_on_default_branch_only(self):
        for example in ("minimal", "clean-room"):
            with self.subTest(example=example):
                workflow = source(f"examples/{example}/.github/workflows/ci.yml")
                self.assertIn("cache-warm:", workflow)
                self.assertIn("uses: blogle/sdlc/.github/workflows/candidate.yml@v1.0.0", workflow)
                self.assertIn(
                    "github.event_name == 'push' && github.ref == format('refs/heads/{0}', github.event.repository.default_branch)",
                    workflow,
                )

    def test_gc_has_write_permission_and_only_runs_on_default_branch(self):
        workflow = source(".github/workflows/hestia-gc.yml")
        self.assertIn("actions: write", workflow)
        self.assertIn("workflow_call:", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("github.ref == format('refs/heads/{0}', github.event.repository.default_branch)", workflow)
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", workflow)
        self.assertIn('"$HESTIA_BIN" gc', workflow)
        self.assertIn("group: hestia-gc-", workflow)

    def test_source_ci_does_not_run_candidate_on_default_branch_as_required_gate(self):
        workflow = source(".github/workflows/ci.yml")
        self.assertIn(
            "if: github.event_name == 'pull_request' && startsWith(github.event.pull_request.head.ref, 'mergify/merge-queue/')",
            workflow,
        )
        self.assertIn("if: always() && github.event_name == 'pull_request'", workflow)
