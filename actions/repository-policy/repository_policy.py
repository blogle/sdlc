"""Render and validate the canonical GitHub repository policy payload."""

import argparse
import json
from pathlib import Path
import sys


RULESET_NAME = "SDLC default branch"
PR_FAST_CONTEXT = "sdlc / pr-fast"
POLICY_CONTEXT = "sdlc / policy"


def normalize_ruleset(ruleset):
    """Compare only the canonical ruleset's policy-bearing API fields."""
    if ruleset is None:
        return None
    rules = []
    for rule in ruleset.get("rules", []):
        projected = {key: value for key, value in rule.items() if key in ("type", "parameters")}
        if projected.get("type") == "required_status_checks":
            params = dict(projected.get("parameters", {}))
            params["required_status_checks"] = sorted(
                ({"context": item["context"]} for item in params.get("required_status_checks", [])), key=lambda item: item["context"]
            )
            projected["parameters"] = params
        rules.append(projected)
    rules.sort(key=lambda item: item["type"])
    result = {key: rules if key == "rules" else ruleset.get(key) for key in ("name", "target", "enforcement", "conditions", "rules", "bypass_actors")}
    return result


def check_live(current, desired, repository):
    fix = f"nix develop -c sdlc policy apply --repo {repository}"
    if current is None:
        raise ValueError(f"canonical ruleset is missing; repair with `{fix}`")
    visible = normalize_ruleset(current)
    expected = normalize_ruleset(desired)
    if "bypass_actors" not in current:
        visible.pop("bypass_actors", None)
        expected.pop("bypass_actors", None)
        print("::warning::GitHub redacted bypass_actors; all visible policy fields matched, but bypass configuration was not verified.", file=sys.stderr)
    if visible != expected:
        raise ValueError(f"live canonical ruleset drift detected; inspect with `nix develop -c sdlc policy plan --repo {repository}` and repair with `{fix}`")


def render_policy(config, default_branch):
    if not isinstance(config, dict):
        raise ValueError("policy declaration must be a JSON object")
    unknown = set(config) - {"default_branch", "extra_required_status_checks"}
    if unknown:
        raise ValueError(f"unknown policy declaration field(s): {', '.join(sorted(unknown))}")

    branch = config.get("default_branch", default_branch)
    if not isinstance(branch, str) or not branch or branch.startswith("refs/heads/") or "*" in branch:
        raise ValueError("default_branch must be a plain, non-empty branch name")
    extra = config.get("extra_required_status_checks", [])
    if not isinstance(extra, list) or any(not isinstance(check, str) or not check.strip() for check in extra):
        raise ValueError("extra_required_status_checks must be a list of non-empty strings")
    if len(extra) != len(set(extra)):
        raise ValueError("extra_required_status_checks must not contain duplicates")
    if PR_FAST_CONTEXT in extra:
        raise ValueError(f"{PR_FAST_CONTEXT!r} is canonical; do not repeat it as an extra check")

    contexts = [PR_FAST_CONTEXT, POLICY_CONTEXT, *sorted(extra)]
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {
            "ref_name": {
                "include": ["~DEFAULT_BRANCH" if branch == default_branch else f"refs/heads/{branch}"],
                "exclude": [],
            }
        },
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "pull_request",
                "parameters": {
                    "allowed_merge_methods": ["squash"],
                    "dismiss_stale_reviews_on_push": True,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_approving_review_count": 0,
                    "required_review_thread_resolution": False,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [{"context": context} for context in contexts],
                },
            },
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        config = json.loads(args.config.read_text())
        desired = render_policy(config, args.default_branch)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))

    rendered = json.dumps(desired, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
