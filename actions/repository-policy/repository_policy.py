"""Render and validate the canonical GitHub repository policy payload."""

import argparse
import json
from pathlib import Path
import sys


RULESET_NAME = "SDLC default branch"
PR_FAST_CONTEXT = "sdlc / pr-fast"
POLICY_CONTEXT = "sdlc / policy"


def normalize_ruleset(ruleset, desired=None):
    """Project API details onto the fields owned by the canonical renderer."""
    if ruleset is None:
        return None
    desired = desired or ruleset
    actual_by_type = {}
    for rule in ruleset.get("rules", []):
        actual_by_type.setdefault(rule.get("type"), []).append(rule)
    desired_rules = []
    actual_rules = []
    for expected in desired.get("rules", []):
        rule_type = expected["type"]
        desired_rules.append(expected)
        found = actual_by_type.get(rule_type, [])
        if len(found) != 1:
            actual_rules.append({"type": rule_type, "missing_or_duplicate": len(found)})
            continue
        actual = found[0]
        expected_parameters = expected.get("parameters", {})
        actual_parameters = actual.get("parameters", {})
        projected_parameters = {}
        for key, expected_value in expected_parameters.items():
            actual_value = actual_parameters.get(key)
            if key == "required_status_checks":
                actual_value = sorted(
                    ({"context": item.get("context")} for item in actual_value or []),
                    key=lambda item: item["context"] or "",
                )
                expected_value = sorted(
                    ({"context": item.get("context")} for item in expected_value),
                    key=lambda item: item["context"] or "",
                )
            projected_parameters[key] = actual_value
        actual_rules.append({"type": rule_type, "parameters": projected_parameters})
    extras = sorted(set(actual_by_type) - {rule["type"] for rule in desired_rules})
    if extras:
        actual_rules.append({"unexpected_rule_types": extras})
    return {
        "name": ruleset.get("name"),
        "target": ruleset.get("target"),
        "enforcement": ruleset.get("enforcement"),
        "conditions": ruleset.get("conditions"),
        "rules": actual_rules,
        "bypass_actors": ruleset.get("bypass_actors"),
    }


def check_live(current, desired, repository):
    fix = f"nix develop -c sdlc policy apply --repo {repository}"
    if current is None:
        raise ValueError(f"canonical ruleset is missing; repair with `{fix}`")
    visible = normalize_ruleset(current, desired)
    expected = normalize_ruleset(desired, desired)
    if "bypass_actors" not in current:
        visible.pop("bypass_actors", None)
        expected.pop("bypass_actors", None)
        print("::warning::GitHub redacted bypass_actors; all visible policy fields matched, but bypass configuration was not verified.", file=sys.stderr)
    if visible != expected:
        raise ValueError(f"live canonical ruleset drift detected; inspect with `nix develop -c sdlc policy plan --repo {repository}` and repair with `{fix}`")


def render_policy(config, default_branch):
    if not isinstance(config, dict):
        raise ValueError("policy declaration must be a JSON object")
    unknown = set(config) - {"default_branch", "extra_required_status_checks", "require_policy_check"}
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

    require_policy_check = config.get("require_policy_check", False)
    if not isinstance(require_policy_check, bool):
        raise ValueError("require_policy_check must be a boolean")
    contexts = [PR_FAST_CONTEXT, *([POLICY_CONTEXT] if require_policy_check else []), *sorted(extra)]
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
