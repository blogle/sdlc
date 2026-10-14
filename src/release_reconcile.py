"""Portable rolling-release reconciliation for a consumer checkout."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

import sdlc


def _run(*args: str, cwd: str | Path | None = None, raw: bool = False) -> str | bytes:
    result = subprocess.run(list(args), cwd=cwd, check=True, capture_output=True)
    return result.stdout if raw else result.stdout.decode().strip()


def _gh(*args: str) -> str:
    return str(_run("gh", *args))


def _fail(message: str) -> None:
    raise ValueError(f"release reconcile stopped: {message}")


def _retire(repo: str, branch: str, default_branch: str) -> None:
    number = _gh("pr", "list", "--repo", repo, "--state", "open", "--base", default_branch, "--head", branch, "--json", "number", "--jq", ".[0].number")
    if number:
        _gh("pr", "close", number, "--repo", repo, "--comment", "No unreleased fragments remain; candidate retired.")


def _verify_predecessor(repo: str, default_branch: str, baseline: str) -> None:
    try:
        _run("git", "cat-file", "-e", f"origin/{default_branch}:.sdlc/release.json")
    except subprocess.CalledProcessError:
        return
    predecessor = _run("git", "show", f"origin/{default_branch}:.sdlc/release.json")
    manifest = json.loads(predecessor)
    version = manifest.get("version", "")
    source = manifest.get("source_main_sha", "")
    if not isinstance(version, str) or not sdlc.VERSION_RE.fullmatch(version):
        _fail("committed release manifest has no valid version")
    if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
        _fail("committed release manifest has no valid source SHA")
    try:
        tag_sha = _run("git", "rev-list", "-n1", f"refs/tags/v{version}")
    except subprocess.CalledProcessError:
        tag_sha = ""
    if not tag_sha:
        _fail(f"release v{version} has no committed tag; publication predecessor is unreconciled")
    if _run("git", "rev-parse", f"{tag_sha}^") != source:
        _fail(f"release v{version} tag does not identify the manifest's merged commit")
    tagged_manifest = _run("git", "show", f"{tag_sha}:.sdlc/release.json", raw=True)
    if hashlib.sha256(tagged_manifest).hexdigest() != hashlib.sha256((predecessor + "\n").encode()).hexdigest():
        _fail(f"release v{version} tag manifest differs from main")
    try:
        _run("git", "merge-base", "--is-ancestor", tag_sha, baseline)
    except subprocess.CalledProcessError:
        _fail(f"release v{version} tag is not on {default_branch}")
    state_raw = _gh("release", "view", f"v{version}", "--repo", repo, "--json", "isDraft,isPrerelease,assets")
    state = json.loads(state_raw)
    if not state:
        _fail(f"release v{version} has no completed publication ledger")
    if state.get("isDraft") is not False:
        _fail(f"release v{version} is still draft")
    if state.get("isPrerelease") is not False:
        _fail(f"release v{version} is prerelease")
    asset = f"sdlc-source-{version}-{tag_sha}.tar.gz"
    digest = next((item.get("digest", "") for item in state.get("assets", []) if item.get("name") == asset), "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        _fail(f"release v{version} lacks its immutable published asset identity")


def _has_fragments(worktree: Path) -> bool:
    old_root = sdlc.ROOT
    sdlc.ROOT = worktree
    try:
        return bool(sdlc.fragments())
    finally:
        sdlc.ROOT = old_root


def reconcile(repo: str | None = None) -> int:
    repo = repo or os.environ.get("GITHUB_REPOSITORY", "")
    default_branch = os.environ.get("SDLC_DEFAULT_BRANCH", "")
    if not default_branch:
        _fail("SDLC_DEFAULT_BRANCH is required")
    branch = os.environ.get("SDLC_RELEASE_BRANCH", "sdlc/release-next")
    if not repo:
        _fail("GITHUB_REPOSITORY is required")
    sdlc.policy_command("check", repo)
    _gh("auth", "setup-git")
    for attempt in range(1, 4):
        subprocess.run(["git", "fetch", "--prune", "--tags", "origin", default_branch, branch], check=False)
        baseline = str(_run("git", "rev-parse", f"origin/{default_branch}"))
        with tempfile.TemporaryDirectory(prefix="sdlc-release-") as temp:
            worktree = Path(temp)
            _run("git", "worktree", "add", "--detach", str(worktree), baseline)
            try:
                if not _has_fragments(worktree):
                    _retire(repo, branch, default_branch)
                    return 0
                _verify_predecessor(repo, default_branch, baseline)
                old_root = sdlc.ROOT
                sdlc.ROOT = worktree
                try:
                    manifest = sdlc.release_snapshot(baseline)
                finally:
                    sdlc.ROOT = old_root
                if not isinstance(manifest, dict):
                    _fail("deterministic snapshot generation produced no manifest")
                candidate = str(_run("git", "-C", str(worktree), "rev-parse", "HEAD"))
                _run("git", "-C", str(worktree), "config", "user.name", "sdlc-release[bot]")
                _run("git", "-C", str(worktree), "config", "user.email", "sdlc-release[bot]@users.noreply.github.com")
                _run("git", "-C", str(worktree), "add", "CHANGELOG.md", ".sdlc/release.json", ".changes")
                date = str(_run("git", "-C", str(worktree), "show", "-s", "--format=%cI", baseline))
                env = os.environ | {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
                subprocess.run(["git", "-C", str(worktree), "commit", "-m", "docs: prepare rolling release candidate"], check=True, capture_output=True, env=env)
                candidate = str(_run("git", "-C", str(worktree), "rev-parse", "HEAD"))
                try:
                    old = str(_run("git", "rev-parse", "--verify", f"refs/remotes/origin/{branch}"))
                    lease = f"--force-with-lease=refs/heads/{branch}:{old}"
                except subprocess.CalledProcessError:
                    lease = f"--force-with-lease=refs/heads/{branch}:"
                pushed = subprocess.run(["git", "-C", str(worktree), "push", lease, "origin", f"HEAD:refs/heads/{branch}"], capture_output=True)
                if pushed.returncode == 0:
                    break
            finally:
                _run("git", "worktree", "remove", "--force", str(worktree))
        if attempt == 3:
            _fail("release branch changed concurrently after three lease retries")
    number = _gh("pr", "list", "--repo", repo, "--state", "open", "--base", default_branch, "--head", branch, "--json", "number", "--jq", ".[0].number")
    title = "chore: rolling release candidate"
    body = f"Generated by the release App from {default_branch} snapshot {baseline}. This PR is bot-owned, excluded from Mergify, and replaceable until merged.\n\nCandidate commit: {candidate}\n\nThe manifest binds the version, source SHA, ordered fragments, changelog digest, generated tree, and publication identity."
    if number:
        _gh("pr", "edit", number, "--repo", repo, "--title", title, "--body", body)
    else:
        _gh("pr", "create", "--repo", repo, "--base", default_branch, "--head", branch, "--title", title, "--body", body)
    return 0
