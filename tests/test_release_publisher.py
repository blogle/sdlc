import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.sdlc import generated_tree_digest


SPEC = importlib.util.spec_from_file_location(
    "release_publisher", Path(__file__).parents[1] / "src/release_publisher.py"
)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class PublisherTests(unittest.TestCase):
    def identity_inputs(self):
        changelog = b"# Changelog\n\n## [1.0.0]\n"
        fragment = "0" * 40
        return {
            "schema": 1,
            "version": "1.0.0",
            "source_main_sha": "1" * 40,
            "fragments": [{"path": ".changes/one.json", "blob_sha": fragment}],
            "changelog_sha256": hashlib.sha256(changelog).hexdigest(),
            "generated_tree": generated_tree_digest(changelog, [{"path": ".changes/one.json", "blob_sha": fragment}]),
            "publication": {"version": "1.0.0", "source_main_sha": "1" * 40},
        }, changelog

    def test_stale_squash_merge_base_is_rejected(self):
        manifest, changelog = self.identity_inputs()
        with self.assertRaisesRegex(publisher.IdentityError, "parent"):
            publisher.validate_identity(
                manifest,
                merged_sha="3" * 40,
                parent_sha="4" * 40,
                tree_sha="2" * 40,
                source_blobs={".changes/one.json": "0" * 40},
                merged_blobs={"CHANGELOG.md": "a" * 40, ".sdlc/release.json": "b" * 40},
                changed_paths={"CHANGELOG.md", ".sdlc/release.json", ".changes/one.json"},
                changelog=changelog,
            )

    def test_identity_rejects_mutated_tree_and_changelog(self):
        manifest, changelog = self.identity_inputs()
        with self.assertRaisesRegex(publisher.IdentityError, "CHANGELOG"):
            publisher.validate_identity(
                manifest,
                merged_sha="3" * 40,
                parent_sha="1" * 40,
                tree_sha="2" * 40,
                source_blobs={".changes/one.json": "0" * 40},
                merged_blobs={"CHANGELOG.md": "a" * 40, ".sdlc/release.json": "b" * 40},
                changed_paths={"CHANGELOG.md", ".sdlc/release.json", ".changes/one.json"},
                changelog=changelog + b"mutated",
            )
        manifest["generated_tree"] = "9" * 64
        with self.assertRaisesRegex(publisher.IdentityError, "generated_tree"):
            publisher.validate_identity(
                manifest,
                merged_sha="3" * 40,
                parent_sha="1" * 40,
                tree_sha="2" * 40,
                source_blobs={".changes/one.json": "0" * 40},
                merged_blobs={"CHANGELOG.md": "a" * 40, ".sdlc/release.json": "b" * 40},
                changed_paths={"CHANGELOG.md", ".sdlc/release.json", ".changes/one.json"},
                changelog=changelog,
            )

    def test_interrupted_build_resumes_without_changing_identity_or_digest(self):
        manifest, changelog = self.identity_inputs()
        identity = publisher.validate_identity(
            manifest,
            merged_sha="3" * 40,
            parent_sha="1" * 40,
            tree_sha="2" * 40,
            source_blobs={".changes/one.json": "0" * 40},
            merged_blobs={"CHANGELOG.md": "a" * 40, ".sdlc/release.json": "b" * 40},
            changed_paths={"CHANGELOG.md", ".sdlc/release.json", ".changes/one.json"},
            changelog=changelog,
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "release-ledger.json"
            ledger = publisher.ArtifactLedger.load_or_create(path, identity)
            ledger.record_artifact("source-archive", "a" * 64)
            ledger.save()
            resumed = publisher.ArtifactLedger.load_or_create(path, identity)
            resumed.record_artifact("source-archive", "a" * 64)
            with self.assertRaisesRegex(publisher.IdentityError, "changed"):
                resumed.record_artifact("source-archive", "b" * 64)

    def test_tag_conflict_is_rejected(self):
        with self.assertRaisesRegex(publisher.IdentityError, "different commit"):
            publisher.verify_tag("4" * 40, "3" * 40)
        publisher.verify_tag("3" * 40, "3" * 40)

    def test_validate_checkout_reconstructs_actual_squash_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".changes").mkdir()
            (root / ".changes/one.json").write_text('{"semver":"patch","summary":"one","type":"fix"}\n')
            (root / "CHANGELOG.md").write_text("# Changelog\n")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "source"], cwd=root, check=True)
            source = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            (root / ".changes/one.json").unlink()
            changelog = b"# Changelog\n\n## [1.0.0]\n\n- **fix**: one\n"
            (root / "CHANGELOG.md").write_bytes(changelog)
            manifest = {
                "schema": 1,
                "version": "1.0.0",
                "prior_released_boundary": None,
                "source_main_sha": source,
                "fragments": [{"path": ".changes/one.json", "blob_sha": subprocess.run(["git", "rev-parse", f"{source}:.changes/one.json"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()}],
                "changelog_sha256": hashlib.sha256(changelog).hexdigest(),
                "generated_tree": generated_tree_digest(changelog, [{"path": ".changes/one.json", "blob_sha": subprocess.run(["git", "rev-parse", f"{source}:.changes/one.json"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()}]),
                "publication": {"version": "1.0.0", "source_main_sha": source},
            }
            (root / ".sdlc").mkdir()
            (root / ".sdlc/release.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "release"], cwd=root, check=True)
            merged = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            result = subprocess.run([sys.executable, str(Path(__file__).parents[1] / "src/release_publisher.py"), merged], cwd=root, check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(result.stdout)["commit"], merged)

    def test_ledger_cannot_resume_another_merged_sha(self):
        manifest, changelog = self.identity_inputs()
        identity = publisher.validate_identity(
            manifest,
            merged_sha="3" * 40,
            parent_sha="1" * 40,
            tree_sha="2" * 40,
            source_blobs={".changes/one.json": "0" * 40},
            merged_blobs={"CHANGELOG.md": "a" * 40, ".sdlc/release.json": "b" * 40},
            changed_paths={"CHANGELOG.md", ".sdlc/release.json", ".changes/one.json"},
            changelog=changelog,
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "release-ledger.json"
            path.write_text(json.dumps({"version": identity.version, "merged_sha": "4" * 40}))
            with self.assertRaisesRegex(publisher.IdentityError, "different release identity"):
                publisher.ArtifactLedger.load_or_create(path, identity)

    def test_workflow_resumes_from_immutable_release_asset(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/release-publish.yml").read_text()
        self.assertIn("gh release download", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("git archive --format=tar.gz", workflow)
        self.assertIn("sdlc-source-${version}-${MERGED_SHA}.tar.gz", workflow)
        self.assertIn("isDraft", workflow)
        self.assertIn("published immutable asset digest is missing", workflow)
        self.assertNotIn("ledger_artifact", workflow)
        self.assertNotIn('gh release download "$tag" --pattern "$asset" --dir .sdlc --clobber', workflow)
        self.assertIn("git merge-base --is-ancestor \"$MERGED_SHA\" FETCH_HEAD", workflow)
        self.assertIn("gh auth setup-git", workflow)
        self.assertIn("GH_TOKEN: ${{ steps.release-app.outputs.token }}", workflow)
        self.assertLess(workflow.index("Prepare one immutable source artifact"), workflow.index("Create or verify exact annotated version tag"))
        self.assertLess(workflow.index("git config user.name 'sdlc-release[bot]'"), workflow.index('git tag -a "$tag"'))
        self.assertLess(workflow.index("git config user.email 'sdlc-release[bot]@users.noreply.github.com'"), workflow.index('git tag -a "$tag"'))

    def test_workflow_ruleset_validation_exits_only_on_invalid_ruleset(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/release-publish.yml").read_text()
        self.assertIn('sys.exit("canonical active ruleset missing; refusing publication") if len(matches) != 1 else None', workflow)
        self.assertNotIn('raise SystemExit("canonical active ruleset missing; refusing publication") if len(matches) != 1 else None', workflow)


if __name__ == "__main__":
    unittest.main()
