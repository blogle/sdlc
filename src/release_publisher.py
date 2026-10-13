"""Validate the immutable release identity after protected merge."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess

from sdlc import VERSION_RE, verify_release_tree


@dataclass(frozen=True)
class ReleaseIdentity:
    version: str
    commit: str
    source_sha: str


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def validate_checkout(merged_sha: str) -> ReleaseIdentity:
    if not re.fullmatch(r"[0-9a-f]{40}", merged_sha) or _git("rev-parse", "HEAD") != merged_sha:
        raise ValueError("publisher checkout must be the actual merged release SHA")
    parents = _git("rev-list", "--parents", "-n1", merged_sha).split()
    if len(parents) != 2:
        raise ValueError("release commit must be a squash commit with one source parent")
    source_sha = parents[1]
    manifest = json.loads(_git("show", f"{merged_sha}:.sdlc/release.json"))
    version = manifest.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ValueError("release manifest version is invalid")
    if manifest.get("source_sha") != source_sha:
        raise ValueError("release manifest source_sha does not equal the actual release parent")
    verify_release_tree(source_sha, merged_sha, manifest)
    return ReleaseIdentity(version, merged_sha, source_sha)
