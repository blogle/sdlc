#!/usr/bin/env python3
"""Validation and durable state for the merged-release publisher.

This module deliberately does not create GitHub releases.  The workflow owns
the side effects after this module has frozen an exact merged commit.
"""
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
VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
FRAGMENT_RE = re.compile(r"^\.changes/[^/]+\.json$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


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


def validate_identity(
    manifest: dict,
    *,
    merged_sha: str,
    parent_sha: str,
    tree_sha: str,
    files: set[str],
    blobs: dict[str, str],
    source_blobs: dict[str, str],
    changelog: bytes,
) -> ReleaseIdentity:
    """Validate the complete identity produced by the release updater.

    ``parent_sha`` is intentionally an input rather than inferred from the
    manifest: the publisher must prove that the actual squash merge landed on
    the exact main snapshot used by the updater.
    """
    if manifest.get("schema") != 1:
        raise IdentityError("release manifest schema must be 1")
    version = manifest.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise IdentityError("release manifest has an invalid semantic version")
    commit = _sha(merged_sha, "merged commit")
    source = _sha(manifest.get("source_main_sha"), "source_main_sha")
    if _sha(parent_sha, "merged commit parent") != source:
        raise IdentityError("release commit parent does not equal manifest source_main_sha")
    tree = _sha(tree_sha, "generated_tree")
    if _sha(manifest.get("generated_tree"), "generated_tree") != tree:
        raise IdentityError("release manifest generated_tree does not match merged commit")

    raw_fragments = manifest.get("fragments")
    if not isinstance(raw_fragments, list) or not raw_fragments:
        raise IdentityError("release manifest must contain fragments")
    fragments: list[tuple[str, str]] = []
    for item in raw_fragments:
        if not isinstance(item, dict) or not FRAGMENT_RE.fullmatch(str(item.get("path", ""))):
            raise IdentityError("release manifest contains an invalid fragment path")
        path = item["path"]
        blob = _sha(item.get("blob_sha"), f"fragment {path} blob_sha")
        if source_blobs.get(path) != blob:
            raise IdentityError(f"fragment blob does not match manifest source snapshot: {path}")
        if path in files:
            raise IdentityError(f"consumed fragment still exists in merged tree: {path}")
        if blobs.get(path) == blob:
            raise IdentityError(f"consumed fragment unexpectedly exists in merged tree: {path}")
        fragments.append((path, blob))
    if fragments != sorted(fragments) or len({path for path, _ in fragments}) != len(fragments):
        raise IdentityError("manifest fragments must be unique and sorted")

    expected = manifest.get("changelog_sha256")
    actual = hashlib.sha256(changelog).hexdigest()
    if expected != actual:
        raise IdentityError("merged CHANGELOG.md digest does not match manifest")
    publication = manifest.get("publication")
    if not isinstance(publication, dict) or not isinstance(publication.get("artifacts"), list) or not publication["artifacts"]:
        raise IdentityError("manifest publication.artifacts is required")
    if not isinstance(publication.get("build_command"), str) or not publication["build_command"].strip():
        raise IdentityError("manifest publication.build_command is required")
    for artifact in publication["artifacts"]:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("name"), str):
            raise IdentityError("each publication artifact needs a name")
        if "digest" in artifact and not DIGEST_RE.fullmatch(str(artifact["digest"])):
            raise IdentityError("artifact digest must use sha256:<hex>")
    return ReleaseIdentity(version, commit, source, tree, actual, tuple(fragments))


class ArtifactLedger:
    """Atomic, append-by-replacement ledger for one immutable release.

    The workflow uploads this file as a durable Actions artifact after every
    transition.  A retry may load it, but can never change its identity or an
    already recorded digest.
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
            "fragments": list(identity.fragments),
            "status": "built",
            "artifacts": {},
            "tagged": False,
            "published": False,
        })

    def record_artifact(self, name: str, digest: str) -> None:
        if not DIGEST_RE.fullmatch(digest):
            raise IdentityError("artifact digest must use sha256:<hex>")
        old = self.data["artifacts"].get(name)
        if old is not None and old != digest:
            raise IdentityError(f"artifact digest changed for {name}")
        self.data["artifacts"][name] = digest
        self.data["status"] = "built"

    def mark_tagged(self) -> None:
        self.data["tagged"] = True
        self.data["status"] = "tagged"

    def mark_published(self) -> None:
        if not self.data.get("tagged"):
            raise IdentityError("cannot publish before the exact tag is recorded")
        self.data["published"] = True
        self.data["status"] = "published"

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


def validate_checkout(commit: str) -> ReleaseIdentity:
    commit = _sha(commit, "merged commit")
    parents = git("rev-list", "--parents", "-n1", commit).split()
    if len(parents) != 2:
        raise IdentityError("merged release commit must have exactly one parent")
    tree = git("rev-parse", f"{commit}^{{tree}}")
    records = git("ls-tree", "-r", commit).splitlines()
    blobs = {}
    for record in records:
        mode, kind, blob = record.split("\t", 1)[0].split()
        path = record.split("\t", 1)[1]
        if kind == "blob":
            blobs[path] = blob
    parent_records = git("ls-tree", "-r", parents[1]).splitlines()
    source_blobs = {}
    for record in parent_records:
        mode, kind, blob = record.split("\t", 1)[0].split()
        if kind == "blob":
            source_blobs[record.split("\t", 1)[1]] = blob
    manifest = json.loads(subprocess.run(["git", "show", f"{commit}:.sdlc/release.json"], check=True, capture_output=True, text=True).stdout)
    changelog = subprocess.run(["git", "show", f"{commit}:CHANGELOG.md"], check=True, capture_output=True).stdout
    return validate_identity(manifest, merged_sha=commit, parent_sha=parents[1], tree_sha=tree, files=set(blobs), blobs=blobs, source_blobs=source_blobs, changelog=changelog)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("commit")
    args = parser.parse_args()
    identity = validate_checkout(args.commit)
    print(json.dumps(identity.__dict__, sort_keys=True, default=list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
