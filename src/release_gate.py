#!/usr/bin/env python3
"""Fail-closed release candidate validation and merge authorization.

Release snapshot generation is owned by ``sdlc.release_snapshot`` from the
rolling-release coordinator.  This module replays that interface; it does not
implement a second changelog generator.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Callable, Iterable, Mapping

try:
    import sdlc as coordinator
except ModuleNotFoundError:
    from . import sdlc as coordinator

verify_release_tree = coordinator.verify_release_tree


RELEASE_BRANCH = "sdlc/release-next"
RELEASE_CHECK = "sdlc / release-gate"
MANIFEST_PATH = ".sdlc/release.json"
FRAGMENT_PREFIX = ".changes/"
FRAGMENT_SUFFIX = ".json"


class GateError(RuntimeError):
    """A release operation cannot prove an invariant."""


@dataclass(frozen=True)
class PullRequest:
    number: int
    head_sha: str
    head_branch: str
    head_login: str
    head_type: str
    head_repo: str
    author_login: str
    author_type: str
    merge_sha: str = ""


@dataclass(frozen=True)
class Check:
    name: str
    conclusion: str | None
    sha: str
    app_slug: str | None = None
    check_id: int | None = None
    completed_at: str | None = None


def _git(*args: str, cwd: str | Path | None = None, raw: bool = False) -> str | bytes:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise GateError(f"git cannot prove release invariant: {' '.join(args)}") from exc
    return result.stdout if raw else result.stdout.decode().strip()


def _gh_json(endpoint: str) -> Any:
    env = os.environ.copy()
    try:
        result = subprocess.run(["gh", "api", endpoint], check=True, capture_output=True, text=True, env=env)
    except subprocess.CalledProcessError as exc:
        raise GateError(f"live GitHub API data unavailable for {endpoint}") from exc
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GateError(f"invalid GitHub API response for {endpoint}") from exc


def _pull_request(repo: str, number: int) -> PullRequest:
    data = _gh_json(f"repos/{repo}/pulls/{number}")
    head = data.get("head", {})
    user = data.get("user", {})
    head_repo = head.get("repo") or {}
    return PullRequest(
        number,
        head.get("sha", ""),
        head.get("ref", ""),
        (head.get("user") or {}).get("login", ""),
        (head.get("user") or {}).get("type", ""),
        head_repo.get("full_name", ""),
        user.get("login", ""),
        user.get("type", ""),
        data.get("merge_commit_sha", "") or "",
    )


def _checks(repo: str, sha: str) -> list[Check]:
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100", "--paginate", "--slurp"],
            check=True,
            capture_output=True,
            text=True,
        )
        pages = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise GateError(f"paginated GitHub check data unavailable for {sha}") from exc
    if not isinstance(pages, list) or any(
        not isinstance(page, dict) or not isinstance(page.get("check_runs"), list)
        for page in pages
    ):
        raise GateError(f"ambiguous paginated GitHub check data for {sha}")
    items = [item for page in pages for item in page["check_runs"]]
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), int) for item in items):
        raise GateError(f"ambiguous GitHub check metadata for {sha}")
    if any(item.get("status") == "completed" and (not isinstance(item.get("completed_at"), str) or not item["completed_at"]) for item in items):
        raise GateError(f"ambiguous GitHub check metadata for {sha}")
    ids = [item["id"] for item in items]
    if len(ids) != len(set(ids)):
        raise GateError(f"duplicate paginated GitHub checks for {sha}")
    return [
        Check(
            item.get("name", ""),
            item.get("conclusion"),
            item.get("head_sha", ""),
            (item.get("app") or {}).get("slug"),
            item.get("id"),
            item.get("completed_at"),
        )
        for item in items
    ]


def _required_check(checks: Iterable[Check], name: str, sha: str, app_slug: str | None = None) -> None:
    candidates = [item for item in checks if item.name == name and item.sha == sha]
    if app_slug is not None:
        candidates = [item for item in candidates if item.app_slug == app_slug]
    if not candidates or any(item.check_id is None or item.completed_at is None for item in candidates):
        if candidates:
            raise GateError(f"required check {name!r} has ambiguous completion ordering for exact head {sha}")
        suffix = f" from App {app_slug!r}" if app_slug else ""
        raise GateError(f"required check {name!r}{suffix} is not successful for exact head {sha}")
    latest = max(candidates, key=lambda item: (item.completed_at, item.check_id))
    if latest.conclusion != "success":
        raise GateError(f"latest required check {name!r} is not successful for exact head {sha}")


def _authenticated_app(release_bot_login: str, app_slug: str, repository: str) -> tuple[str, str]:
    """Validate the trusted action output and installation repository access.

    Installation tokens deliberately cannot call ``/app`` or ``/user``.  The
    app slug comes from create-github-app-token's trusted step output, while
    this endpoint proves that the installation can access this repository.
    """
    if not app_slug or release_bot_login != f"{app_slug}[bot]":
        raise GateError("configured release bot does not match the authenticated release App")
    repositories = _gh_json("installation/repositories?per_page=100").get("repositories", [])
    if not any(item.get("full_name") == repository for item in repositories):
        raise GateError("authenticated release App installation cannot access this repository")
    return release_bot_login, app_slug


def _manifest_at(repo: str, sha: str) -> Mapping[str, Any]:
    item = _gh_json(f"repos/{repo}/contents/{MANIFEST_PATH}?ref={sha}")
    try:
        return json.loads(base64.b64decode(item["content"]).decode())
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise GateError("release manifest is missing or invalid JSON") from exc


def replay_snapshot(source_sha: str, *, git: Callable[..., str | bytes] = _git) -> tuple[Mapping[str, Any], bytes]:
    """Re-run the pinned coordinator snapshot from the exact source commit."""
    with tempfile.TemporaryDirectory(prefix="release-gate-") as temp:
        source_dir = Path(temp) / "source"
        git("worktree", "add", "--detach", str(source_dir), source_sha)
        try:
            old_root = coordinator.ROOT
            coordinator.ROOT = source_dir
            try:
                result = coordinator.release_snapshot(source_sha)
                changelog = (source_dir / "CHANGELOG.md").read_bytes()
            finally:
                coordinator.ROOT = old_root
            if not isinstance(result, dict):
                raise GateError("coordinator snapshot produced no release manifest")
            return result, changelog
        finally:
            try:
                git("worktree", "remove", "--force", str(source_dir))
            except GateError:
                pass


def verify_candidate(source_sha: str, head_sha: str, manifest: Mapping[str, Any], *, git: Callable[..., str | bytes] = _git, planner: Callable[[str], Any] = replay_snapshot) -> None:
    """Replay the canonical planner and prove the candidate changed only its output."""
    if manifest.get("source_sha") != source_sha:
        raise GateError("manifest source_sha does not match current main")
    expected = planner(source_sha)
    repeated = planner(source_sha)
    if expected != repeated:
        raise GateError("coordinator snapshot replay is not deterministic")
    if isinstance(expected, tuple):
        expected_manifest, expected_changelog = expected
    else:
        expected_manifest, expected_changelog = expected, None
    if dict(expected_manifest) != dict(manifest):
        raise GateError("candidate manifest differs from deterministic coordinator replay")
    paths = sorted(path for path in str(git("ls-tree", "-r", "--name-only", source_sha, "--", ".changes")).splitlines() if path.endswith(".json"))
    if not paths:
        raise GateError("release source has no changelog fragments")
    for path in paths:
        try:
            git("rev-parse", f"{head_sha}:{path}")
        except GateError:
            continue
        raise GateError(f"consumed fragment remains in release tree: {path}")
    changed = set(str(git("diff", "--name-only", source_sha, head_sha)).splitlines())
    allowed = {"CHANGELOG.md", MANIFEST_PATH, *paths}
    if changed != allowed:
        raise GateError(f"candidate changed paths differ from generated output: {sorted(changed ^ allowed)}")
    if expected_changelog is not None and git("show", f"{head_sha}:CHANGELOG.md", raw=True) != expected_changelog:
        raise GateError("candidate CHANGELOG.md differs from deterministic generation")
    try:
        verify_release_tree(source_sha, head_sha, manifest)
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise GateError(f"shared coordinator tree verification failed: {exc}") from exc


def evaluate_release_gate(*, pr: PullRequest, authenticated_login: str, app_slug: str, main_sha: str, parent_sha: str | None, checks: Iterable[Check], manifest: Mapping[str, Any] | None, release_bot_login: str, repository: str, planner: Callable[[str], Mapping[str, Any]] = replay_snapshot) -> None:
    if pr.head_branch != RELEASE_BRANCH:
        return
    if (authenticated_login != release_bot_login or pr.author_login != release_bot_login or pr.author_type != "Bot" or pr.head_repo != repository):
        raise GateError("release PR is not owned by the authenticated release App")
    if parent_sha != main_sha:
        raise GateError("release head parent is not current main")
    if not re.fullmatch(r"[0-9a-f]{40}", pr.merge_sha):
        raise GateError("GitHub has not produced a test merge commit for the release candidate")
    _required_check(checks, "sdlc / check", pr.merge_sha)
    if manifest is None:
        raise GateError("release manifest is required")
    verify_candidate(main_sha, pr.head_sha, manifest, planner=planner)


class GithubMergeApi:
    def __init__(self, repo: str, branch: str, app_slug: str):
        self.repo, self.branch, self.app_slug = repo, branch, app_slug

    def pull_request(self, number: int) -> PullRequest:
        return _pull_request(self.repo, number)

    def main_sha(self) -> str:
        return _gh_json(f"repos/{self.repo}/commits/{self.branch}")["sha"]

    def parent_sha(self, sha: str) -> str:
        parents = _gh_json(f"repos/{self.repo}/commits/{sha}").get("parents", [])
        if len(parents) != 1:
            raise GateError("release head must have exactly one parent")
        return parents[0]["sha"]

    def merge_parents(self, sha: str) -> list[str]:
        parents = _gh_json(f"repos/{self.repo}/commits/{sha}").get("parents", [])
        return [item.get("sha", "") for item in parents]

    def checks(self, sha: str) -> list[Check]:
        return _checks(self.repo, sha)

    def merge(self, number: int, *, sha: str, merge_method: str) -> Mapping[str, Any]:
        if merge_method != "squash":
            raise GateError("release merge must use squash")
        result = subprocess.run(["gh", "api", "--method", "PUT", f"repos/{self.repo}/pulls/{number}/merge", "-f", f"sha={sha}", "-f", "merge_method=squash"], check=True, capture_output=True, text=True)
        return json.loads(result.stdout)


def merge_release_pr(*, api: GithubMergeApi, pr_number: int, expected_head_sha: str, current_main_sha: str, release_bot_login: str) -> Mapping[str, Any]:
    """Trusted worker preflight; GitHub's ``sha`` is the only merge precondition."""
    pr = api.pull_request(pr_number)
    if pr.head_branch != RELEASE_BRANCH or pr.head_sha != expected_head_sha:
        raise GateError("release head changed before merge")
    if pr.author_login != release_bot_login or pr.author_type != "Bot" or pr.head_repo != api.repo:
        raise GateError("release PR is not owned by the release App")
    if api.main_sha() != current_main_sha:
        raise GateError("main advanced before merge; regenerate release candidate")
    if api.parent_sha(expected_head_sha) != current_main_sha:
        raise GateError("release head parent is not current main")
    if api.merge_parents(pr.merge_sha) != [current_main_sha, expected_head_sha]:
        raise GateError("release test-merge commit does not bind the current base and head")
    checks = api.checks(pr.merge_sha)
    _required_check(checks, "sdlc / check", pr.merge_sha)
    _required_check(checks, "sdlc / release-gate", pr.merge_sha, api.app_slug)
    return api.merge(pr_number, sha=expected_head_sha, merge_method="squash")


def _gate_command(args: argparse.Namespace) -> int:
    pr = _pull_request(args.repo, args.pr)
    if pr.head_branch != RELEASE_BRANCH:
        return 0
    login, slug = _authenticated_app(args.release_bot, args.app_slug, args.repo)
    repo = _gh_json(f"repos/{args.repo}")
    main = _gh_json(f"repos/{args.repo}/commits/{repo['default_branch']}")
    commit = _gh_json(f"repos/{args.repo}/commits/{pr.head_sha}")
    if args.historical_merged_sha:
        manifest = _manifest_at(args.repo, pr.head_sha)
        source_sha = manifest.get("source_sha") if manifest else None
        if not source_sha or (commit.get("parents") or [{}])[0].get("sha") != source_sha:
            raise GateError("historical release head parent does not match manifest source_sha")
        merged = _gh_json(f"repos/{args.repo}/commits/{args.historical_merged_sha}")
        parents = merged.get("parents") or []
        if len(parents) != 1 or parents[0].get("sha") != source_sha:
            raise GateError("historical squash merge does not match manifest source_sha")
        verify_candidate(source_sha, args.historical_merged_sha, manifest)
        return 0
    merge_parents = [item.get("sha", "") for item in _gh_json(f"repos/{args.repo}/commits/{pr.merge_sha}").get("parents", [])]
    if merge_parents != [main["sha"], pr.head_sha]:
        raise GateError("release test-merge commit does not bind the current base and head")
    evaluate_release_gate(
        pr=pr,
        authenticated_login=login,
        app_slug=slug,
        main_sha=main["sha"],
        parent_sha=(commit.get("parents") or [{}])[0].get("sha"),
        checks=_checks(args.repo, pr.merge_sha),
        manifest=_manifest_at(args.repo, pr.head_sha),
        release_bot_login=args.release_bot,
        repository=args.repo,
    )
    return 0


def _merge_command(args: argparse.Namespace) -> int:
    pr = _pull_request(args.repo, args.pr)
    if pr.head_branch != RELEASE_BRANCH:
        raise GateError("target PR is not the release PR")
    login, slug = _authenticated_app(args.release_bot, args.app_slug, args.repo)
    if login != args.release_bot:
        raise GateError("authenticated App does not match release bot")
    repository = _gh_json(f"repos/{args.repo}")
    api = GithubMergeApi(args.repo, repository["default_branch"], slug)
    expected = args.expected_head or pr.head_sha
    current_main = api.main_sha()
    verify_candidate(current_main, expected, _manifest_at(args.repo, expected))
    api_result = merge_release_pr(api=api, pr_number=args.pr, expected_head_sha=expected, current_main_sha=current_main, release_bot_login=args.release_bot)
    if not api_result.get("merged"):
        raise GateError(f"GitHub declined release merge: {api_result}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("gate", "merge"):
        command = commands.add_parser(name)
        command.add_argument("--repo", required=True)
        command.add_argument("--pr", type=int, required=True)
        command.add_argument("--release-bot", required=True)
        command.add_argument("--app-slug", default="")
    commands.choices["gate"].add_argument("--historical-merged-sha", default="")
    commands.choices["merge"].add_argument("--expected-head")
    args = parser.parse_args()
    try:
        return _gate_command(args) if args.command == "gate" else _merge_command(args)
    except (GateError, OSError, subprocess.CalledProcessError) as exc:
        print(f"release-gate: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
