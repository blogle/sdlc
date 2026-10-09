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
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from contextlib import redirect_stdout
from typing import Any, Callable, Iterable, Mapping


RELEASE_BRANCH = "sdlc/release-next"
RELEASE_CHECK = "sdlc / release-gate"
REQUIRED_CHECKS = ("sdlc / pr-fast",)
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


@dataclass(frozen=True)
class Check:
    name: str
    conclusion: str | None
    sha: str
    app_slug: str | None = None


def _git(*args: str, cwd: str | Path | None = None, raw: bool = False) -> str | bytes:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise GateError(f"git cannot prove release invariant: {' '.join(args)}") from exc
    return result.stdout.strip() if raw else result.stdout.decode().strip()


def _gh_json(endpoint: str) -> Any:
    try:
        result = subprocess.run(["gh", "api", endpoint], check=True, capture_output=True, text=True)
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
    )


def _checks(repo: str, sha: str) -> list[Check]:
    return [
        Check(item.get("name", ""), item.get("conclusion"), item.get("head_sha", ""), (item.get("app") or {}).get("slug"))
        for item in _gh_json(f"repos/{repo}/commits/{sha}/check-runs?per_page=100").get("check_runs", [])
    ]


def _required_check(checks: Iterable[Check], name: str, sha: str, app_slug: str | None = None) -> None:
    candidates = [item for item in checks if item.name == name and item.sha == sha and item.conclusion == "success"]
    if app_slug is not None:
        candidates = [item for item in candidates if item.app_slug == app_slug]
    if not candidates:
        suffix = f" from App {app_slug!r}" if app_slug else ""
        raise GateError(f"required check {name!r}{suffix} is not successful for exact head {sha}")


def verify_protection(protection: Mapping[str, Any], required_checks: Iterable[str] = REQUIRED_CHECKS) -> None:
    active = [item for item in protection.get("rulesets", ()) if item.get("enforcement") == "active"]
    if not active and not protection.get("classic"):
        raise GateError("no applicable branch protection or active ruleset is configured")
    if protection.get("strict") is True:
        raise GateError("global strict freshness is enabled; release gate will not change repository policy")
    if protection.get("bypass_actors"):
        raise GateError("release merge requires zero protection bypass actors")
    missing = set(required_checks) - set(protection.get("contexts", ()))
    if missing:
        raise GateError(f"protected required checks are absent: {sorted(missing)}")


def _protection(repo: str, branch: str) -> Mapping[str, Any]:
    rulesets = _gh_json(f"repos/{repo}/rulesets?includes_parents=true")
    try:
        classic = _gh_json(f"repos/{repo}/branches/{branch}/protection")
    except GateError:
        classic = {}
    contexts = set(classic.get("required_status_checks", {}).get("contexts", ()))
    strict = classic.get("required_status_checks", {}).get("strict")
    bypass = []
    for summary in rulesets:
        bypass.extend(summary.get("bypass_actors") or ())
        detail = summary if summary.get("rules") else _gh_json(f"repos/{repo}/rulesets/{summary['id']}")
        for rule in detail.get("rules", ()):
            if rule.get("type") != "required_status_checks":
                continue
            params = rule.get("parameters", {})
            contexts.update(item.get("context") for item in params.get("required_status_checks", ()))
            strict = strict or params.get("strict_required_status_checks_policy", False)
    return {"classic": bool(classic), "rulesets": rulesets, "contexts": contexts, "strict": strict, "bypass_actors": bypass}


def _authenticated_app(release_bot_login: str) -> tuple[str, str]:
    user = _gh_json("user")
    app = _gh_json("app")
    slug = app.get("slug", "")
    if user.get("login") != release_bot_login or user.get("login") != f"{slug}[bot]":
        raise GateError("authenticated token is not the configured release GitHub App")
    return user["login"], slug


def _manifest_at(repo: str, sha: str) -> Mapping[str, Any]:
    item = _gh_json(f"repos/{repo}/contents/{MANIFEST_PATH}?ref={sha}")
    try:
        return json.loads(base64.b64decode(item["content"]).decode())
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise GateError("release manifest is missing or invalid JSON") from exc


def _load_coordinator(source_dir: Path) -> Any:
    module_path = source_dir / "src/sdlc.py"
    if not module_path.exists():
        raise GateError("coordinator changelog snapshot interface is unavailable")
    spec = importlib.util.spec_from_file_location("coordinator_sdlc", module_path)
    if spec is None or spec.loader is None:
        raise GateError("cannot load coordinator changelog snapshot interface")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "release_snapshot", None)) or not callable(getattr(module, "verify_release_tree", None)):
        raise GateError("coordinator does not expose the shared snapshot generator/verifier")
    return module


def _with_coordinator(source_sha: str, callback: Callable[[Any], Any]) -> Any:
    with tempfile.TemporaryDirectory(prefix="release-gate-") as temp:
        source_dir = Path(temp) / "source"
        _git("worktree", "add", "--detach", str(source_dir), source_sha)
        try:
            return callback(_load_coordinator(source_dir))
        finally:
            try:
                _git("worktree", "remove", "--force", str(source_dir))
            except GateError:
                pass


def replay_snapshot(source_sha: str, *, git: Callable[..., str | bytes] = _git) -> Mapping[str, Any]:
    """Re-run the coordinator snapshot from the exact source commit."""
    def generate(module: Any) -> Mapping[str, Any]:
        module.ROOT = Path(module.__file__).resolve().parents[1]
        with redirect_stdout(io.StringIO()):
            result = module.release_snapshot(source_sha)
        if not isinstance(result, dict):
            raise GateError("coordinator snapshot produced no release manifest")
        return result

    return _with_coordinator(source_sha, generate)


def _validate_manifest_contract(manifest: Mapping[str, Any], source_sha: str) -> None:
    if manifest.get("schema") != 1 or "schemaVersion" in manifest:
        raise GateError("release manifest schema must be 1")
    if manifest.get("source_main_sha") != source_sha:
        raise GateError("manifest source_main_sha does not match current main")
    publication = manifest.get("publication")
    if publication != {"version": manifest.get("version"), "source_main_sha": source_sha}:
        raise GateError("manifest publication must contain only version and source_main_sha")
    if "generated_tree" not in manifest or "generated_tree_sha256" in manifest:
        raise GateError("manifest must use generated_tree canonical digest")


def verify_candidate(source_sha: str, head_sha: str, manifest: Mapping[str, Any], *, git: Callable[..., str | bytes] = _git, planner: Callable[[str], Mapping[str, Any]] = replay_snapshot, verifier: Callable[[str, str, Mapping[str, Any]], None] | None = None) -> None:
    """Replay the canonical planner and prove the candidate changed only its output."""
    _validate_manifest_contract(manifest, source_sha)
    expected = planner(source_sha)
    repeated = planner(source_sha)
    if dict(expected) != dict(repeated):
        raise GateError("coordinator snapshot replay is not deterministic")
    if dict(expected) != dict(manifest):
        raise GateError("candidate manifest differs from deterministic coordinator replay")
    def verify(module: Any) -> None:
        module.ROOT = Path.cwd()
        try:
            module.verify_release_tree(source_sha, head_sha, dict(manifest))
        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
            raise GateError(f"shared release tree verifier rejected candidate: {exc}") from exc

    if verifier is not None:
        verifier(source_sha, head_sha, manifest)
    else:
        _with_coordinator(source_sha, verify)


def evaluate_release_gate(*, pr: PullRequest, authenticated_login: str, app_slug: str, main_sha: str, parent_sha: str | None, checks: Iterable[Check], manifest: Mapping[str, Any] | None, release_bot_login: str, repository: str, protection: Mapping[str, Any], planner: Callable[[str], Mapping[str, Any]] = replay_snapshot) -> None:
    if pr.head_branch != RELEASE_BRANCH:
        return
    if (authenticated_login != release_bot_login or pr.head_login != release_bot_login or pr.head_type != "Bot" or pr.author_login != release_bot_login or pr.author_type != "Bot" or pr.head_repo != repository):
        raise GateError("release PR is not owned by the authenticated release App")
    verify_protection(protection)
    if parent_sha != main_sha:
        raise GateError("release head parent is not current main")
    _required_check(checks, "sdlc / pr-fast", pr.head_sha)
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

    def protection(self) -> Mapping[str, Any]:
        return _protection(self.repo, self.branch)

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
    if pr.head_login != release_bot_login or pr.head_type != "Bot" or pr.author_login != release_bot_login or pr.author_type != "Bot":
        raise GateError("release PR is not owned by the release App")
    if api.main_sha() != current_main_sha:
        raise GateError("main advanced before merge; regenerate release candidate")
    if api.parent_sha(expected_head_sha) != current_main_sha:
        raise GateError("release head parent is not current main")
    verify_protection(api.protection())
    checks = api.checks(expected_head_sha)
    _required_check(checks, "sdlc / pr-fast", expected_head_sha)
    _required_check(checks, RELEASE_CHECK, expected_head_sha, api.app_slug)
    return api.merge(pr_number, sha=expected_head_sha, merge_method="squash")


def _gate_command(args: argparse.Namespace) -> int:
    pr = _pull_request(args.repo, args.pr)
    if pr.head_branch != RELEASE_BRANCH:
        return 0
    login, slug = _authenticated_app(args.release_bot)
    repo = _gh_json(f"repos/{args.repo}")
    main = _gh_json(f"repos/{args.repo}/commits/{repo['default_branch']}")
    commit = _gh_json(f"repos/{args.repo}/commits/{pr.head_sha}")
    evaluate_release_gate(
        pr=pr,
        authenticated_login=login,
        app_slug=slug,
        main_sha=main["sha"],
        parent_sha=(commit.get("parents") or [{}])[0].get("sha"),
        checks=_checks(args.repo, pr.head_sha),
        manifest=_manifest_at(args.repo, pr.head_sha),
        release_bot_login=args.release_bot,
        repository=args.repo,
        protection=_protection(args.repo, repo["default_branch"]),
    )
    return 0


def _merge_command(args: argparse.Namespace) -> int:
    pr = _pull_request(args.repo, args.pr)
    if pr.head_branch != RELEASE_BRANCH:
        raise GateError("target PR is not the release PR")
    login, slug = _authenticated_app(args.release_bot)
    if login != args.release_bot:
        raise GateError("authenticated App does not match release bot")
    api = GithubMergeApi(args.repo, "main", slug)
    expected = args.expected_head or pr.head_sha
    api_result = merge_release_pr(api=api, pr_number=args.pr, expected_head_sha=expected, current_main_sha=api.main_sha(), release_bot_login=args.release_bot)
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
    commands.choices["merge"].add_argument("--expected-head")
    args = parser.parse_args()
    try:
        return _gate_command(args) if args.command == "gate" else _merge_command(args)
    except (GateError, OSError, subprocess.CalledProcessError) as exc:
        print(f"release-gate: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
