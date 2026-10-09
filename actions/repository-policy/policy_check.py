#!/usr/bin/env python3
"""Read-only live ruleset drift check used by the reusable policy workflow."""
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

from repository_policy import check_live, render_policy


def canonical_rulesets(items, name, repository):
    named = [item for item in items if item.get("name") == name]
    owned = [item for item in named if item.get("source", repository) == repository]
    if len(owned) > 1:
        raise ValueError(f"duplicate SDLC canonical rulesets found; remove/rename duplicates manually, then run `nix develop -c sdlc policy apply --repo {repository}`")
    return named, owned


def has_policy_caller(root):
    workflows = root / ".github/workflows"
    if not workflows.is_dir():
        return False
    for path in workflows.glob("*.y*ml"):
        text = path.read_text(encoding="utf-8")
        if "policy-check.yml" in text and "sdlc / policy" in text:
            return True
    return False


def evaluate_policy_check(proposed, base_config, live_rulesets, caller_present, repository, default_branch, pull_request):
    name = "SDLC default branch"
    named, owned = canonical_rulesets(live_rulesets, name, repository)
    if proposed is None:
        if base_config is not None:
            raise ValueError(f"policy declaration was removed from this PR; restore .github/repository-policy.json, then run `nix develop -c sdlc policy apply --repo {repository}`")
        raise ValueError("first-onboarding PR must add .github/repository-policy.json")
    render_policy(proposed, default_branch)  # always validate the proposal independently

    if pull_request and base_config is None:
        if named:
            raise ValueError(f"base declaration is absent but a live canonical ruleset exists; this is not first-time onboarding. Restore the base policy declaration and inspect with `nix develop -c sdlc policy plan --repo {repository}`")
        if not caller_present:
            raise ValueError("first-onboarding PR must install the policy-check.yml caller and stable `sdlc / policy` job before it can pass bootstrap validation")
        if proposed.get("require_policy_check", False):
            raise ValueError("first-onboarding declaration must set `require_policy_check: false`; after merge, apply and verify the baseline policy, then enable the check in a reviewed declaration change")
        print("::notice::FIRST-ONBOARDING: proposed declaration and policy caller validated; no base declaration or live canonical ruleset exists. This interim success does NOT prove live repository protection. After merge, run `nix develop -c sdlc policy apply --repo " + repository + "` and verify with `nix develop -c sdlc policy check --repo " + repository + "`. Then set `require_policy_check: true` in a reviewed declaration change and apply again to require the stable check.")
        return "first-onboarding"

    desired = render_policy(base_config, default_branch)
    if len(owned) != 1:
        check_live(None, desired, repository)
    check_live(owned[0], desired, repository)
    unmanaged = [item.get("name") for item in live_rulesets if item.get("name") != name]
    if unmanaged:
        print(f"::warning::Unmanaged/legacy rulesets also apply and were not reconciled: {', '.join(unmanaged)}. Inspect their effective interaction manually.")
    print(f"live policy matches base declaration for {repository}")
    return "drift-check"


def read_live_rulesets(repository):
    endpoint = f"https://api.github.com/repos/{repository}/rulesets?includes_parents=true&per_page=100"
    result = subprocess.run(["gh", "api", endpoint.removeprefix("https://api.github.com/")], text=True, capture_output=True)
    if result.returncode == 0:
        print("Read live rulesets using the Actions GITHUB_TOKEN.")
        return json.loads(result.stdout)
    # Public repositories can expose rulesets anonymously when GITHUB_TOKEN
    # does not have the fine-grained Administration:read permission.
    with urllib.request.urlopen(endpoint, timeout=30) as response:
        print("Read live rulesets anonymously; GitHub may redact bypass actors.")
        return json.load(response)


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    default_branch = os.environ["DEFAULT_BRANCH"]
    event = os.environ.get("EVENT_NAME")
    path = ".github/repository-policy.json"
    # On PRs validate the proposed declaration, then compare live state to the
    # declaration committed to the target branch (the proposal is not applied yet).
    base_config = None
    if event == "pull_request":
        base = os.environ["BASE_SHA"]
        result = subprocess.run(["git", "show", f"{base}:{path}"], text=True, capture_output=True)
        if result.returncode == 0:
            base_config = json.loads(result.stdout)
    try:
        with open(path, encoding="utf-8") as source:
            proposed = json.load(source)
    except FileNotFoundError:
        proposed = None
    if event != "pull_request":
        base_config = proposed
    live = read_live_rulesets(repository)
    evaluate_policy_check(
        proposed,
        base_config,
        live,
        has_policy_caller(Path.cwd()),
        repository,
        default_branch,
        event == "pull_request",
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, json.JSONDecodeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"policy check failed: {error}", file=sys.stderr)
        raise SystemExit(1)
