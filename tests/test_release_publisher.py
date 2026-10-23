import hashlib
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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

    def _self_source_release(self, root: Path):
        (root / ".sdlc").mkdir()
        (root / "CHANGELOG.md").write_text("# Changelog\n")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "source"], cwd=root, check=True)
        source = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
        (root / ".sdlc/release.json").write_text(json.dumps({"version": "1.0.0", "source_main_sha": source}) + "\n")
        subprocess.run(["git", "add", ".sdlc/release.json"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "release"], cwd=root, check=True)
        merged = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
        asset = root.parent / "published-source.tar.gz"
        subprocess.run(["git", "archive", "--format=tar.gz", "--output", str(asset), merged], cwd=root, check=True)
        return source, merged, asset

    def _fake_gh(self, directory: Path):
        fake_gh = directory / "gh"
        fake_gh.write_text("""#!/usr/bin/env python3
import os, shutil, sys
assert sys.argv[1:3] == ["release", "download"]
asset = sys.argv[sys.argv.index("--pattern") + 1]
target = sys.argv[sys.argv.index("--dir") + 1]
shutil.copyfile(os.environ["FAKE_ASSET"], os.path.join(target, asset))
""")
        fake_gh.chmod(0o755)
        return fake_gh

    @unittest.skipUnless(shutil.which("jq"), "jq is unavailable")
    def test_self_consumer_publisher_receipts_the_actual_source_release_asset(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            source, merged, asset = self._self_source_release(root)
            original = asset.read_bytes()
            asset.write_bytes(gzip.compress(gzip.decompress(original), compresslevel=1, mtime=1))
            self.assertNotEqual(asset.read_bytes(), original)
            fake_bin = Path(temp) / "bin"
            fake_bin.mkdir()
            self._fake_gh(fake_bin)
            receipts = root / ".sdlc/receipts.json"
            env = os.environ | {
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "FAKE_ASSET": str(asset),
                "SDLC_RELEASE_VERSION": "1.0.0",
                "SDLC_RELEASE_MERGED_SHA": merged,
                "SDLC_RELEASE_SOURCE_SHA": source,
                "SDLC_RELEASE_RECEIPTS": str(receipts),
            }
            subprocess.run(["bash", str(Path(__file__).parents[1] / "scripts/sdlc-release-publish.sh")], cwd=root, env=env, check=True)
            receipt = json.loads(receipts.read_text())
            self.assertEqual(receipt["artifacts"][0]["name"], f"sdlc-source-1.0.0-{merged}.tar.gz")
            self.assertEqual(receipt["artifacts"][0]["source_main_sha"], source)
            self.assertEqual(receipt["artifacts"][0]["digest"], "sha256:" + hashlib.sha256(asset.read_bytes()).hexdigest())

    @unittest.skipUnless(shutil.which("jq"), "jq is unavailable")
    def test_self_consumer_publisher_rejects_source_asset_digest_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            source, merged, asset = self._self_source_release(root)
            asset.write_bytes(gzip.compress(gzip.decompress(asset.read_bytes()).replace(b"# Changelog", b"# Tampered!")))
            fake_bin = Path(temp) / "bin"
            fake_bin.mkdir()
            self._fake_gh(fake_bin)
            receipts = root / ".sdlc/receipts.json"
            env = os.environ | {
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "FAKE_ASSET": str(asset),
                "SDLC_RELEASE_VERSION": "1.0.0",
                "SDLC_RELEASE_MERGED_SHA": merged,
                "SDLC_RELEASE_SOURCE_SHA": source,
                "SDLC_RELEASE_RECEIPTS": str(receipts),
            }
            result = subprocess.run(["bash", str(Path(__file__).parents[1] / "scripts/sdlc-release-publish.sh")], cwd=root, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(receipts.exists())

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
            path.write_text(json.dumps({"schema": 1, "version": identity.version, "merged_sha": "4" * 40, "artifacts": {}}))
            with self.assertRaisesRegex(publisher.IdentityError, "different release identity"):
                publisher.ArtifactLedger.load_or_create(path, identity)

    def test_consumer_receipts_record_multiple_immutable_artifacts(self):
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
            ledger_path = Path(temp) / "release-ledger.json"
            publisher.ArtifactLedger.load_or_create(ledger_path, identity).save()
            receipts = Path(temp) / "publication-receipts.json"
            receipts.write_text(json.dumps({
                "schema": 1,
                "artifacts": [
                    {"name": "anvil", "digest": "sha256:" + "a" * 64, "version": "1.0.0", "merged_sha": "3" * 40, "source_main_sha": "1" * 40},
                    {"name": "sandbox", "digest": "sha256:" + "b" * 64, "version": "1.0.0", "merged_sha": "3" * 40, "source_main_sha": "1" * 40},
                ],
            }))
            with patch.object(publisher, "validate_checkout", return_value=identity):
                publisher.record_receipts(ledger_path, receipts, "1.0.0", "3" * 40, "1" * 40)
            self.assertEqual(json.loads(ledger_path.read_text())["artifacts"], {"anvil": "a" * 64, "sandbox": "b" * 64})

    def test_completion_requires_identity_bound_consumer_receipts(self):
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
            ledger_path = Path(temp) / "release-ledger.json"
            ledger = publisher.ArtifactLedger.load_or_create(ledger_path, identity)
            ledger.record_artifact("source-archive", "c" * 64)
            ledger.mark_tagged()
            ledger.save()
            with patch.object(publisher, "validate_checkout", return_value=identity):
                with self.assertRaisesRegex(publisher.IdentityError, "receipts"):
                    publisher.mark_published(ledger_path)
            receipts = Path(temp) / "publication-receipts.json"
            receipts.write_text(json.dumps({"schema": 1, "artifacts": [
                {"name": "image", "digest": "sha256:" + "a" * 64, "version": "1.0.0", "merged_sha": "3" * 40, "source_main_sha": "1" * 40},
            ]}))
            with patch.object(publisher, "validate_checkout", return_value=identity):
                publisher.record_receipts(ledger_path, receipts, "1.0.0", "3" * 40, "1" * 40)
                publisher.mark_published(ledger_path)
            self.assertTrue(json.loads(ledger_path.read_text())["published"])

    @unittest.skipUnless(shutil.which("nix"), "nix is unavailable")
    def test_shared_publisher_builds_and_executes_ci_publish_from_a_merged_consumer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".changes").mkdir()
            (root / "flake.nix").write_text(f'''{{
  inputs = {{
    sdlc.url = "path:{Path(__file__).parents[1]}";
    nixpkgs.follows = "sdlc/nixpkgs-latest";
  }};
  outputs = {{ nixpkgs, sdlc, ... }}:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${{system}};
      check = pkgs.runCommand "consumer-check" {{ }} "touch $out";
      publish = pkgs.writeShellScriptBin "publish" ''printf 'invoked\\n' > "$HOOK_OUTPUT"'';
    in {{
      ci.${{system}} = sdlc.lib.mkConsumer {{
        check = {{ fmt = check; }};
        verify = {{ tests = check; }};
        publish = {{ type = "app"; program = "${{publish}}/bin/publish"; }};
      }};
    }};
}}\n''')
            (root / ".changes/one.json").write_text('{"semver":"patch","summary":"one","type":"fix"}\n')
            (root / "CHANGELOG.md").write_text("# Changelog\n")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "source"], cwd=root, check=True)
            source = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            fragment_blob = subprocess.run(["git", "rev-parse", f"{source}:.changes/one.json"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            (root / ".changes/one.json").unlink()
            changelog = b"# Changelog\n\n## [1.0.0]\n\n- **fix**: one\n"
            (root / "CHANGELOG.md").write_bytes(changelog)
            manifest = {
                "schema": 1,
                "version": "1.0.0",
                "prior_released_boundary": None,
                "source_main_sha": source,
                "fragments": [{"path": ".changes/one.json", "blob_sha": fragment_blob}],
                "changelog_sha256": hashlib.sha256(changelog).hexdigest(),
                "generated_tree": generated_tree_digest(changelog, [{"path": ".changes/one.json", "blob_sha": fragment_blob}]),
                "publication": {"version": "1.0.0", "source_main_sha": source},
            }
            (root / ".sdlc").mkdir()
            (root / ".sdlc/release.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "release"], cwd=root, check=True)
            merged = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            output = root / "publish-invoked"
            environment = {
                "SDLC_RELEASE_VERSION": "1.0.0",
                "SDLC_RELEASE_MERGED_SHA": merged,
                "SDLC_RELEASE_SOURCE_SHA": source,
                "SDLC_RELEASE_RECEIPTS": str(root / ".sdlc/receipts.json"),
                "HOOK_OUTPUT": str(output),
            }
            source_root = Path(__file__).parents[1]
            subprocess.run(
                ["nix", "run", f"{source_root}#sdlc", "--", "release", "invoke", "--consumer-flake", str(root), "--system", "x86_64-linux"],
                cwd=root,
                env=os.environ | environment,
                check=True,
            )
            self.assertEqual(output.read_text(), "invoked\n")

    def test_workflow_resumes_from_immutable_release_asset(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/release-publish.yml").read_text()
        self.assertIn("gh release download", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("git archive --format=tar.gz", workflow)
        self.assertIn('gzip -dc ".sdlc/$asset" | cmp -- .sdlc/source.expected.tar -', workflow)
        self.assertIn("durable source asset contents changed", workflow)
        self.assertIn("sdlc-source-${version}-${MERGED_SHA}.tar.gz", workflow)
        self.assertIn("isDraft", workflow)
        self.assertIn("published immutable asset digest is missing", workflow)
        self.assertNotIn("ledger_artifact", workflow)
        self.assertNotIn('gh release download "$tag" --pattern "$asset" --dir .sdlc --clobber', workflow)
        self.assertIn("git merge-base --is-ancestor \"$MERGED_SHA\" FETCH_HEAD", workflow)
        self.assertIn("gh auth setup-git", workflow)
        self.assertIn("GH_TOKEN: ${{ steps.release-app.outputs.token }}", workflow)
        self.assertIn("packages: write", workflow)
        self.assertIn('nix run "$SDLC_FLAKE"#sdlc -- release publish', workflow)
        self.assertIn('nix run "$SDLC_FLAKE"#sdlc -- release invoke', workflow)
        self.assertNotIn("nix run nixpkgs#just", workflow)
        self.assertIn('nix run "$SDLC_FLAKE"#sdlc -- release receipts', workflow)
        self.assertIn("test -s \"$SDLC_RELEASE_RECEIPTS\"", workflow)
        self.assertIn('gh release edit "$tag" --draft=false', workflow)
        self.assertNotIn("python3 src/release_publisher.py", workflow)
        persist = workflow.split("- name: Persist artifact bytes without overwriting", 1)[1].split("- name: Save publication audit ledger", 1)[0]
        self.assertIn("SDLC_FLAKE:", persist)
        self.assertIn('nix run "$SDLC_FLAKE"#sdlc -- release ledger artifact', persist)
        self.assertLess(workflow.index("Prepare one immutable source artifact"), workflow.index("Create or verify exact annotated version tag"))
        self.assertLess(workflow.index("git config user.name 'sdlc-release[bot]'"), workflow.index('git tag -a "$tag"'))
        self.assertLess(workflow.index("git config user.email 'sdlc-release[bot]@users.noreply.github.com'"), workflow.index('git tag -a "$tag"'))

    def test_publisher_resolves_canonical_ruleset_cli(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/release-publish.yml").read_text()
        self.assertIn('nix run "$SDLC_FLAKE"#sdlc -- ruleset check', workflow)


if __name__ == "__main__":
    unittest.main()
