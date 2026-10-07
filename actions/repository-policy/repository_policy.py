"""Render and validate the canonical GitHub repository policy payload."""

import argparse
import json
from pathlib import Path


RULESET_NAME = "SDLC default branch"
PR_FAST_CONTEXT = "sdlc / pr-fast"


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

    contexts = [PR_FAST_CONTEXT, *sorted(extra)]
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {
            "ref_name": {
                "include": [f"refs/heads/{branch}"],
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
