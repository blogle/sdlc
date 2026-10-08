#!/usr/bin/env python3
"""Read-only live ruleset drift check used by the reusable policy workflow."""
import json
import os
import subprocess
import sys
import urllib.request

from repository_policy import check_live, render_policy


def desired_for_check(proposed, base_config, default_branch):
    render_policy(proposed, default_branch)  # validate the proposal independently
    return render_policy(base_config if base_config is not None else proposed, default_branch)


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    default_branch = os.environ["DEFAULT_BRANCH"]
    event = os.environ.get("EVENT_NAME")
    path = ".github/repository-policy.json"
    # On PRs validate the proposed declaration, then compare live state to the
    # declaration committed to the target branch (the proposal is not applied yet).
    with open(path, encoding="utf-8") as source:
        proposed = json.load(source)
    base_config = None
    if event == "pull_request":
        base = os.environ["BASE_SHA"]
        result = subprocess.run(["git", "show", f"{base}:{path}"], text=True, capture_output=True)
        if result.returncode:
            raise ValueError(f"base branch has no {path}; install the caller and run `nix develop -c sdlc policy apply --repo {repository}`")
        base_config = json.loads(result.stdout)
    desired = desired_for_check(proposed, base_config, default_branch)
    endpoint = f"https://api.github.com/repos/{repository}/rulesets?includes_parents=true&per_page=100"
    result = subprocess.run(["gh", "api", endpoint.removeprefix("https://api.github.com/")], text=True, capture_output=True)
    if result.returncode == 0:
        items = json.loads(result.stdout)
    else:
        # Public repositories can expose effective rulesets without an
        # administration-capable token; bypass actor details may be redacted.
        with urllib.request.urlopen(endpoint, timeout=30) as response:
            items = json.load(response)
    canonical = [item for item in items if item.get("name") == desired["name"] and item.get("source", repository) == repository]
    if len(canonical) > 1:
        raise ValueError(f"duplicate SDLC canonical rulesets found; remove/rename duplicates manually, then run `nix develop -c sdlc policy apply --repo {repository}`")
    check_live(canonical[0] if canonical else None, desired, repository)
    unmanaged = [item.get("name") for item in items if item.get("name") != desired["name"]]
    if unmanaged:
        print(f"::warning::Unmanaged/legacy rulesets also apply and were not reconciled: {', '.join(unmanaged)}. Inspect their effective interaction manually.")
    print(f"live policy matches visible base declaration for {repository}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, json.JSONDecodeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"policy check failed: {error}", file=sys.stderr)
        raise SystemExit(1)
