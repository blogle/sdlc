#!/usr/bin/env python3
"""Merged-release identity validation and durable publication state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HEX256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
FRAGMENT_RE = re.compile(r"^\.changes/[^/]+\.json$")
class IdentityError(ValueError):
    """The merged commit cannot be published as the claimed release."""


class ReleaseIdentity:
    def __init__(self, version, commit, source_main, tree, changelog_digest, fragments):
        self.version = version
        self.commit = commit
        self.source_main = source_main
        self.tree = tree
        self.changelog_digest = changelog_digest
        self.fragments = fragments


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise IdentityError(f"{label} must be a 40-character lowercase SHA")
    return value


def generated_tree_digest(changelog: bytes, fragments: list[dict]) -> str:
    """Use the same generated/deleted record digest as ``sdlc.py``."""
    records = [("CHANGELOG.md", "file", changelog)]
    records.extend((item["path"], "deleted", item["blob_sha"].encode()) for item in fragments)
    payload = b"".join(path.encode() + b"\0" + kind.encode() + b"\0" + content + b"\0" for path, kind, content in sorted(records))
    return hashlib.sha256(payload).hexdigest()


def validate_identity(
    manifest: dict,
    *,
    merged_sha: str,
    parent_sha: str,
    tree_sha: str,
    source_blobs: dict[str, str],
    merged_blobs: dict[str, str],
    changed_paths: set[str],
    changelog: bytes,
) -> ReleaseIdentity:
    """Validate the canonical updater manifest against the actual merged tree."""
    if manifest.get("schema") != 1:
        raise IdentityError("release manifest schema must be 1")
    version = manifest.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise IdentityError("release manifest has an invalid semantic version")
    commit = _sha(merged_sha, "merged commit")
    source = _sha(manifest.get("source_main_sha"), "source_main_sha")
    if _sha(parent_sha, "merged commit parent") != source:
        raise IdentityError("release commit parent does not equal manifest source_main_sha")
    _sha(tree_sha, "actual merged tree")

    fragments = manifest.get("fragments")
    if not isinstance(fragments, list) or not fragments:
        raise IdentityError("release manifest must contain fragments")
    bindings: list[tuple[str, str]] = []
    for item in fragments:
        if not isinstance(item, dict) or not FRAGMENT_RE.fullmatch(str(item.get("path", ""))):
            raise IdentityError("release manifest contains an invalid fragment path")
        path = item["path"]
        blob = _sha(item.get("blob_sha"), f"fragment {path} blob_sha")
        if source_blobs.get(path) != blob:
            raise IdentityError(f"fragment blob does not match source snapshot: {path}")
        if path in merged_blobs:
            raise IdentityError(f"consumed fragment remains in merged tree: {path}")
        bindings.append((path, blob))
    if bindings != sorted(bindings) or len({path for path, _ in bindings}) != len(bindings):
        raise IdentityError("manifest fragments must be unique and sorted")

    expected_paths = {"CHANGELOG.md", ".sdlc/release.json"} | {path for path, _ in bindings}
    if changed_paths != expected_paths:
        unexpected = sorted(changed_paths - expected_paths)
        missing = sorted(expected_paths - changed_paths)
        raise IdentityError(f"merged release tree differs outside manifest paths; unexpected={unexpected}, missing={missing}")
    if "CHANGELOG.md" not in merged_blobs:
        raise IdentityError("merged release tree lacks CHANGELOG.md")
    changelog_digest = hashlib.sha256(changelog).hexdigest()
    if manifest.get("changelog_sha256") != changelog_digest:
        raise IdentityError("merged CHANGELOG.md digest does not match manifest")
    if manifest.get("generated_tree") != generated_tree_digest(changelog, fragments):
        raise IdentityError("generated_tree does not match manifest")
    publication = manifest.get("publication")
    if publication != {"version": version, "source_main_sha": source}:
        raise IdentityError("manifest publication must contain only version and source_main_sha")
    return ReleaseIdentity(version, commit, source, _sha(tree_sha, "actual merged tree"), changelog_digest, tuple(bindings))


class ArtifactLedger:
    """Atomic audit state for one exact release identity.

    The bytes themselves live in the draft GitHub Release asset. This ledger
    records its immutable digest and can be recreated from that asset on any
    later workflow run; Actions artifacts are audit copies, not provenance.
    """

    def __init__(self, path: Path, data: dict):
        self.path = path
        self.data = data

    @classmethod
    def load_or_create(cls, path: Path, identity: ReleaseIdentity) -> "ArtifactLedger":
        if path.exists():
            data = json.loads(path.read_text())
            expected = {
                "version": identity.version,
                "merged_sha": identity.commit,
                "source_main_sha": identity.source_main,
                "tree_sha": identity.tree,
            }
            if any(data.get(key) != value for key, value in expected.items()):
                raise IdentityError("artifact ledger belongs to a different release identity")
            return cls(path, data)
        return cls(path, {
            "schema": 1,
            "version": identity.version,
            "merged_sha": identity.commit,
            "source_main_sha": identity.source_main,
            "tree_sha": identity.tree,
            "changelog_sha256": identity.changelog_digest,
            "fragments": [[path, blob] for path, blob in identity.fragments],
            "artifacts": {},
            "tagged": False,
            "published": False,
        })

    def record_artifact(self, name: str, digest: str) -> None:
        if not isinstance(name, str) or not name:
            raise IdentityError("artifact name is required")
        if not HEX256_RE.fullmatch(digest):
            raise IdentityError("artifact digest must be 64 lowercase hexadecimal characters")
        old = self.data["artifacts"].get(name)
        if old is not None and old != digest:
            raise IdentityError(f"artifact digest changed for {name}")
        self.data["artifacts"][name] = digest

    def mark_tagged(self) -> None:
        self.data["tagged"] = True

    def mark_published(self) -> None:
        if not self.data.get("tagged"):
            raise IdentityError("cannot publish before the exact tag is recorded")
        self.data["published"] = True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(self.data, output, indent=2, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def verify_tag(tag_sha: str, merged_sha: str) -> None:
    if _sha(tag_sha, "tag target") != _sha(merged_sha, "merged commit"):
        raise IdentityError("existing tag points at a different commit")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def _tree_blobs(commit: str) -> dict[str, str]:
    result = {}
    for record in git("ls-tree", "-r", commit).splitlines():
        _, kind, blob = record.split("\t", 1)[0].split()
        if kind == "blob":
            result[record.split("\t", 1)[1]] = blob
    return result


def validate_checkout(commit: str) -> ReleaseIdentity:
    commit = _sha(commit, "merged commit")
    parents = git("rev-list", "--parents", "-n1", commit).split()
    if len(parents) != 2:
        raise IdentityError("merged release commit must have exactly one parent")
    parent = parents[1]
    source_blobs = _tree_blobs(parent)
    merged_blobs = _tree_blobs(commit)
    changed_paths = set(git("diff", "--name-only", parent, commit).splitlines())
    tree = git("rev-parse", f"{commit}^{{tree}}")
    manifest = json.loads(subprocess.run(["git", "show", f"{commit}:.sdlc/release.json"], check=True, capture_output=True, text=True).stdout)
    try:
        from sdlc import verify_release_tree
    except ImportError:
        from src.sdlc import verify_release_tree
    try:
        verify_release_tree(parent, commit, manifest)
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise IdentityError(f"shared release tree verification failed: {exc}") from exc
    changelog = subprocess.run(["git", "show", f"{commit}:CHANGELOG.md"], check=True, capture_output=True).stdout
    return validate_identity(
        manifest,
        merged_sha=commit,
        parent_sha=parent,
        tree_sha=tree,
        source_blobs=source_blobs,
        merged_blobs=merged_blobs,
        changed_paths=changed_paths,
        changelog=changelog,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("commit")
    args = parser.parse_args()
    identity = validate_checkout(args.commit)
    print(json.dumps(identity.__dict__, sort_keys=True, default=list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
