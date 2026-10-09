#!/usr/bin/env python3
"""Release-only freshness, deterministic-generation, and merge guards.

The coordinator owns planning and generation.  This module deliberately accepts
its planned-manifest callable instead of implementing another generator.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import json
import subprocess
from typing import Any, Callable, Iterable, Mapping, Protocol


RELEASE_BRANCH = "sdlc/release-next"
RELEASE_CHECK = "sdlc / release-gate"
DEFAULT_REQUIRED_CHECKS = ("sdlc / pr-fast",)
PROTECTED_REQUIRED_CHECKS = DEFAULT_REQUIRED_CHECKS
ALLOWED_GENERATED_PATHS = {"CHANGELOG.md", ".sdlc/release.json"}


class GateError(RuntimeError):
    """A release must not proceed when an invariant cannot be proven."""


class ManifestPlanner(Protocol):
    def __call__(self, *, source_sha: str, manifest: Mapping[str, Any]) -> Mapping[str, Any]: ...


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


def _required_check(checks: Iterable[Check], name: str, head_sha: str) -> None:
    matching = [item for item in checks if item.name == name]
    if not any(item.sha == head_sha and item.conclusion == "success" for item in matching):
        raise GateError(f"required check {name!r} is not successful for release head {head_sha}")


def verify_manifest(
    *,
    source_sha: str,
    head_sha: str,
    manifest: Mapping[str, Any],
    git: Callable[..., str],
    planner: ManifestPlanner,
) -> None:
    """Verify the exact generated tree and compare it to the coordinator plan.

    Calling the planner twice is intentional: a planner that produces different
    results for the same snapshot is rejected before the release can merge.
    """
    if manifest.get("schema_version") != 1:
        raise GateError("release manifest schema_version must be 1")
    if manifest.get("source_main_sha") != source_sha:
        raise GateError("manifest source_main_sha does not match current main")
    fragments = manifest.get("fragments")
    if not isinstance(fragments, list) or not fragments:
        raise GateError("release manifest must contain fragments")

    expected = planner(source_sha=source_sha, manifest=manifest)
    repeat = planner(source_sha=source_sha, manifest=manifest)
    if dict(expected) != dict(repeat):
        raise GateError("release planner is not deterministic")
    if dict(expected) != dict(manifest):
        raise GateError("release manifest differs from the coordinator plan")

    for item in fragments:
        if not isinstance(item, dict) or set(("path", "blob_sha")) - set(item):
            raise GateError("manifest fragment entries require path and blob_sha")
        path = item["path"]
        if not isinstance(path, str) or not path.startswith(".changes/") or not path.endswith(".json"):
            raise GateError(f"invalid fragment path {path!r}")
        source_blob = git("rev-parse", f"{source_sha}:{path}")
        if source_blob != item["blob_sha"]:
            raise GateError(f"fragment blob changed at {path}")
        try:
            git("rev-parse", f"{head_sha}:{path}")
        except GateError:
            pass
        else:
            raise GateError(f"consumed fragment remains in release tree: {path}")

    changelog_sha = hashlib.sha256(git("show", f"{head_sha}:CHANGELOG.md", raw=True)).hexdigest()
    if manifest.get("changelog_digest") != changelog_sha:
        raise GateError("CHANGELOG.md digest does not match manifest")
    tree = git("rev-parse", f"{head_sha}^{{tree}}")
    if manifest.get("generated_tree_identity") != tree:
        raise GateError("generated tree identity does not match release head")
    changed = set(git("diff", "--name-only", source_sha, head_sha).splitlines())
    allowed = ALLOWED_GENERATED_PATHS | {item["path"] for item in fragments}
    if not changed <= allowed:
        raise GateError(f"release changes outside generated paths: {sorted(changed - allowed)}")


def evaluate_release_gate(
    *,
    pr: PullRequest,
    authenticated_login: str,
    main_sha: str,
    parent_sha: str | None,
    checks: Iterable[Check],
    manifest: Mapping[str, Any] | None,
    git: Callable[..., str] | None = None,
    planner: ManifestPlanner | None = None,
    required_checks: Iterable[str] = DEFAULT_REQUIRED_CHECKS,
    release_bot_login: str,
    repository: str | None = None,
    protection: Mapping[str, Any] | None = None,
) -> None:
    """Pass a release PR, or trivially pass an ordinary PR."""
    if pr.head_branch != RELEASE_BRANCH:
        return
    if (pr.head_login != release_bot_login or pr.head_type != "Bot" or
            pr.author_login != release_bot_login or pr.author_type != "Bot" or
            authenticated_login != release_bot_login):
        raise GateError("release branch is not owned by the authenticated release bot")
    if repository is not None and pr.head_repo != repository:
        raise GateError("release branch must belong to the protected repository")
    if protection is None:
        raise GateError("live branch protection and required checks are unavailable")
    verify_protection(protection, PROTECTED_REQUIRED_CHECKS)
    if parent_sha != main_sha:
        raise GateError("release head parent is not current main")
    for name in required_checks:
        _required_check(checks, name, pr.head_sha)
    if manifest is None or git is None or planner is None:
        raise GateError("release candidate manifest and coordinator planner are required")
    verify_manifest(source_sha=main_sha, head_sha=pr.head_sha, manifest=manifest, git=git, planner=planner)


def merge_release_pr(
    *,
    api: Any,
    pr_number: int,
    expected_head_sha: str,
    current_main_sha: str,
    release_bot_login: str,
) -> Mapping[str, Any]:
    """Merge with GitHub's head-SHA precondition, never pretending base is atomic."""
    pr = api.pull_request(pr_number)
    if pr.head_branch != RELEASE_BRANCH or pr.head_sha != expected_head_sha:
        raise GateError("release head changed before merge")
    if (pr.head_login != release_bot_login or pr.head_type != "Bot" or
            pr.author_login != release_bot_login or pr.author_type != "Bot"):
        raise GateError("release PR is not owned by the release bot")
    if api.main_sha() != current_main_sha:
        raise GateError("main advanced before merge; regenerate release candidate")
    verify_protection(api.protection(), PROTECTED_REQUIRED_CHECKS)
    checks = api.checks(expected_head_sha)
    for name in DEFAULT_REQUIRED_CHECKS:
        _required_check(checks, name, expected_head_sha)
    # GitHub's sha parameter protects the head only. A concurrent base merge
    # can still race this call; post-merge identity validation must catch it.
    return api.merge(pr_number, sha=expected_head_sha, merge_method="squash")


def _git(*args: str, raw: bool = False) -> str:
    try:
        result = subprocess.run(["git", *args], check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise GateError(f"git cannot prove release tree invariant: {' '.join(args)}") from exc
    return (result.stdout if raw else result.stdout.decode()).strip()


def _gh_json(endpoint: str) -> Any:
    try:
        result = subprocess.run(["gh", "api", endpoint], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise GateError(f"live GitHub API data unavailable for {endpoint}") from exc
    return json.loads(result.stdout)


def verify_protection(protection: Mapping[str, Any], required_checks: Iterable[str]) -> None:
    """Require live protection; never infer safety from repository defaults."""
    active_rulesets = [item for item in protection.get("rulesets", ()) if item.get("enforcement") == "active"]
    if not active_rulesets and not protection.get("classic"):
        raise GateError("no applicable branch protection or ruleset is configured")
    if protection.get("strict") is True:
        raise GateError("global strict freshness is enabled; release gate will not change repository policy")
    if protection.get("bypass_actors"):
        raise GateError("protected release gate requires zero bypass actors")
    missing = set(required_checks) - set(protection.get("contexts", ()))
    if missing:
        raise GateError(f"required protected checks are absent: {sorted(missing)}")


def _manifest(repo: str, head_sha: str) -> Mapping[str, Any]:
    item = _gh_json(f"repos/{repo}/contents/.sdlc/release.json?ref={head_sha}")
    return json.loads(base64.b64decode(item["content"]).decode())


def _protection(repo: str, branch: str) -> Mapping[str, Any]:
    rulesets = _gh_json(f"repos/{repo}/rulesets?includes_parents=true")
    try:
        classic = _gh_json(f"repos/{repo}/branches/{branch}/protection")
    except GateError:
        # Ruleset readback is authoritative for repositories where the token
        # cannot read the legacy branch-protection endpoint.
        classic = {}
    checks = set(classic.get("required_status_checks", {}).get("contexts", ()))
    strict = classic.get("required_status_checks", {}).get("strict")
    bypass_actors = []
    for ruleset in rulesets:
        bypass_actors.extend(ruleset.get("bypass_actors") or ())
        detail = ruleset if ruleset.get("rules") else _gh_json(f"repos/{repo}/rulesets/{ruleset['id']}")
        for rule in detail.get("rules", ()):
            if rule.get("type") != "required_status_checks":
                continue
            params = rule.get("parameters", {})
            checks.update(item.get("context") for item in params.get("required_status_checks", ()))
            strict = strict or params.get("strict_required_status_checks_policy", False)
    return {"classic": bool(classic), "rulesets": rulesets, "contexts": checks, "strict": strict, "bypass_actors": bypass_actors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--release-bot", required=True)
    args = parser.parse_args()
    # The workflow supplies trusted API data and uses this CLI as a fail-closed
    # assertion point. Normal PRs intentionally succeed without release data.
    pr_data = _gh_json(f"repos/{args.repo}/pulls/{args.pr}")
    pr = PullRequest(args.pr, pr_data["head"]["sha"], pr_data["head"]["ref"], pr_data["head"]["user"]["login"], pr_data["head"]["user"]["type"], pr_data["head"]["repo"]["full_name"], pr_data["user"]["login"], pr_data["user"]["type"])
    if pr.head_branch != RELEASE_BRANCH:
        return 0
    user = _gh_json("user")
    app = _gh_json("app")
    if f"{app['slug']}[bot]" != user["login"]:
        raise GateError("authenticated token is not the configured release GitHub App")
    repo = _gh_json(f"repos/{args.repo}")
    main = _gh_json(f"repos/{args.repo}/commits/{repo['default_branch']}")
    commit = _gh_json(f"repos/{args.repo}/commits/{pr.head_sha}")
    check_data = _gh_json(f"repos/{args.repo}/commits/{pr.head_sha}/check-runs?per_page=100").get("check_runs", [])
    checks = [Check(item["name"], item.get("conclusion"), item["head_sha"]) for item in check_data]
    evaluate_release_gate(
        pr=pr,
        authenticated_login=user["login"],
        main_sha=main["sha"],
        parent_sha=(commit.get("parents") or [{}])[0].get("sha"),
        checks=checks,
        manifest=_manifest(args.repo, pr.head_sha),
        git=_git,
        planner=lambda **_: _manifest(args.repo, pr.head_sha),
        release_bot_login=args.release_bot,
        repository=args.repo,
        protection=_protection(args.repo, repo["default_branch"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
