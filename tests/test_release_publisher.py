import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


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
            "generated_tree": "2" * 40,
            "publication": {
                "artifacts": [{"name": "package", "path": "out"}],
                "build_command": "just build-release",
            },
        }, changelog

    def test_stale_squash_merge_base_is_rejected(self):
        manifest, changelog = self.identity_inputs()
        with self.assertRaisesRegex(publisher.IdentityError, "parent"):
            publisher.validate_identity(
                manifest,
                merged_sha="3" * 40,
                parent_sha="4" * 40,
                tree_sha="2" * 40,
                files=set(),
                blobs={},
                source_blobs={".changes/one.json": "0" * 40},
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
                files=set(),
                blobs={},
                source_blobs={".changes/one.json": "0" * 40},
                changelog=changelog + b"mutated",
            )
        manifest["generated_tree"] = "9" * 40
        with self.assertRaisesRegex(publisher.IdentityError, "generated_tree"):
            publisher.validate_identity(
                manifest,
                merged_sha="3" * 40,
                parent_sha="1" * 40,
                tree_sha="2" * 40,
                files=set(),
                blobs={},
                source_blobs={".changes/one.json": "0" * 40},
                changelog=changelog,
            )

    def test_interrupted_build_resumes_without_changing_identity_or_digest(self):
        manifest, changelog = self.identity_inputs()
        identity = publisher.validate_identity(
            manifest,
            merged_sha="3" * 40,
            parent_sha="1" * 40,
            tree_sha="2" * 40,
            files=set(),
            blobs={},
            source_blobs={".changes/one.json": "0" * 40},
            changelog=changelog,
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "release-ledger.json"
            ledger = publisher.ArtifactLedger.load_or_create(path, identity)
            ledger.record_artifact("package", "sha256:" + "a" * 64)
            ledger.save()
            resumed = publisher.ArtifactLedger.load_or_create(path, identity)
            resumed.record_artifact("package", "sha256:" + "a" * 64)
            with self.assertRaisesRegex(publisher.IdentityError, "changed"):
                resumed.record_artifact("package", "sha256:" + "b" * 64)

    def test_tag_conflict_is_rejected(self):
        with self.assertRaisesRegex(publisher.IdentityError, "different commit"):
            publisher.verify_tag("4" * 40, "3" * 40)
        publisher.verify_tag("3" * 40, "3" * 40)

    def test_ledger_cannot_resume_another_merged_sha(self):
        manifest, changelog = self.identity_inputs()
        identity = publisher.validate_identity(
            manifest,
            merged_sha="3" * 40,
            parent_sha="1" * 40,
            tree_sha="2" * 40,
            files=set(),
            blobs={},
            source_blobs={".changes/one.json": "0" * 40},
            changelog=changelog,
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "release-ledger.json"
            path.write_text(json.dumps({"version": identity.version, "merged_sha": "4" * 40}))
            with self.assertRaisesRegex(publisher.IdentityError, "different release identity"):
                publisher.ArtifactLedger.load_or_create(path, identity)


if __name__ == "__main__":
    unittest.main()
