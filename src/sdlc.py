#!/usr/bin/env python3
"""Consumer-working-tree changelog tooling for the shared SDLC flake."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import os

POLICY_DIR = Path(os.environ.get("SDLC_POLICY_MODULE_PATH", Path(__file__).resolve().parents[1] / "actions/repository-policy"))
sys.path.insert(0, str(POLICY_DIR))
import repository_policy

ROOT = Path.cwd()
RANK = {"patch": 1, "minor": 2, "major": 3}
VERSION_RE = re.compile(r"^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def fragments(selected=None):
    directory = ROOT / ".changes"
    if selected is None and not directory.exists():
        return []
    result = []
    if selected is None:
        paths = sorted(directory.glob("*.json"))
    else:
        paths = []
        for name in selected:
            relative = Path(name)
            if relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != ".changes" or relative.suffix != ".json":
                raise ValueError(f"invalid fragment path {name!r}; expected .changes/<name>.json")
            paths.append(ROOT / relative)
    for path in sorted(paths):
        try:
            item = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path}: invalid fragment: {exc}") from exc
        if not isinstance(item, dict) or set(("type", "semver", "summary")) - set(item):
            raise ValueError(f"{path}: requires type, semver, and summary")
        if item["semver"] not in RANK or not all(isinstance(item[key], str) and item[key].strip() for key in ("type", "summary")):
            raise ValueError(f"{path}: invalid type, semver, or summary")
        result.append((path, item))
    return result


def bump_intent(entries):
    return max((item["semver"] for _, item in entries), key=RANK.get) if entries else None


def next_version(bump):
    try:
        tags = subprocess.run(["git", "tag", "--list", "v[0-9]*"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.splitlines()
    except (FileNotFoundError, subprocess.CalledProcessError):
        tags = []
    versions = [tuple(map(int, VERSION_RE.match(tag).groups())) for tag in tags if VERSION_RE.match(tag)]
    major, minor, patch = max(versions, default=(0, 0, 0))
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def render(entries):
    return "\n".join(f"- **{item['type']}**: {item['summary']}" for _, item in entries)


def changelog(action, version=None, date=None, json_output=False, selected=None):
    entries = fragments(selected)
    bump = bump_intent(entries)
    planned_version = next_version(bump) if bump else None
    if action == "check":
        print(f"validated {len(entries)} changelog fragment(s)")
    elif action == "plan":
        if json_output:
            print(json.dumps({"release": bool(entries), "bump": bump, "version": planned_version}, sort_keys=True))
        elif not entries:
            print("no changelog fragments; no release")
        else:
            print(f"release {planned_version} ({bump})\n\n{render(entries)}")
    elif action == "finalize":
        if not entries:
            print("no changelog fragments; no release")
            return
        if not version or not VERSION_RE.match(version):
            raise ValueError("finalize requires a valid --version")
        release_date = date or datetime.now(timezone.utc).date().isoformat()
        changelog_path = ROOT / "CHANGELOG.md"
        old = changelog_path.read_text() if changelog_path.exists() else "# Changelog\n"
        title, separator, rest = old.partition("\n")
        if not title.startswith("# "):
            title, rest = "# Changelog", old
        section = f"\n## [{version.removeprefix('v')}] - {release_date}\n\n{render(entries)}\n"
        changelog_path.write_text(title + "\n" + section + ("\n" + rest.lstrip("\n") if rest.strip() else ""))
        for path, _ in entries:
            path.unlink()
        print(f"finalized {len(entries)} fragment(s) as {version}")
    else:
        raise ValueError(f"unknown changelog action {action}")


def policy_context(repo=None):
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    full_name = repo or os.environ.get("GITHUB_REPOSITORY")
    if not full_name:
        remote = git("config", "--get", "remote.origin.url")
        match = re.search(r"github\.com[:/]([^/]+/[^/.]+)(?:\.git)?$", remote)
        if not match:
            raise ValueError("cannot infer GitHub repository; pass --repo OWNER/NAME")
        full_name = match.group(1)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
        raise ValueError("--repo must be OWNER/NAME")
    default_branch = subprocess.run(["gh", "api", f"repos/{full_name}", "--jq", ".default_branch"], check=True, capture_output=True, text=True).stdout.strip()
    return full_name, default_branch


def policy_config():
    path = ROOT / ".github/repository-policy.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc


def policy_command(action, repo=None):
    full_name, branch = policy_context(repo)
    desired = repository_policy.render_policy(policy_config(), branch)
    response = subprocess.run(["gh", "api", f"repos/{full_name}/rulesets?per_page=100"], check=True, capture_output=True, text=True)
    all_rulesets = json.loads(response.stdout)
    matches = [item for item in all_rulesets if item.get("name") == repository_policy.RULESET_NAME]
    if len(matches) > 1:
        raise ValueError(f"found {len(matches)} canonical rulesets named {repository_policy.RULESET_NAME!r}; resolve duplicates manually")
    current = matches[0] if matches else None
    if action == "plan":
        print(json.dumps({"repository": full_name, "default_branch": branch, "action": "create" if current is None else ("update" if repository_policy.normalize_ruleset(current) != repository_policy.normalize_ruleset(desired) else "no-op"), "desired": desired, "current": current, "unmanaged_rulesets": [item for item in all_rulesets if item.get("name") != repository_policy.RULESET_NAME]}, indent=2, sort_keys=True))
        return
    if action == "check":
        repository_policy.check_live(current, desired, full_name)
        print(f"policy matches live ruleset for {full_name}")
        return
    if action == "apply":
        if current is None:
            subprocess.run(["gh", "api", "--method", "POST", f"repos/{full_name}/rulesets", "--input", "-"], input=json.dumps(desired), text=True, check=True, capture_output=True)
        elif repository_policy.normalize_ruleset(current) != repository_policy.normalize_ruleset(desired):
            subprocess.run(["gh", "api", "--method", "PUT", f"repos/{full_name}/rulesets/{current['id']}", "--input", "-"], input=json.dumps(desired), text=True, check=True, capture_output=True)
        verify = json.loads(subprocess.run(["gh", "api", f"repos/{full_name}/rulesets?per_page=100"], check=True, capture_output=True, text=True).stdout)
        verified = [item for item in verify if item.get("name") == repository_policy.RULESET_NAME]
        if len(verified) != 1 or repository_policy.normalize_ruleset(verified[0]) != repository_policy.normalize_ruleset(desired):
            raise ValueError("read-after-write verification failed; rerun `nix develop -c sdlc policy plan`")
        print(f"policy applied and verified for {full_name}")


def main():
    parser = argparse.ArgumentParser(prog="sdlc")
    commands = parser.add_subparsers(dest="command", required=True)
    changes = commands.add_parser("changelog")
    changes.add_argument("action", choices=["check", "plan", "finalize"])
    changes.add_argument("--version")
    changes.add_argument("--date")
    changes.add_argument("--json", action="store_true")
    changes.add_argument("--fragment", nargs="*", help="limit plan/finalize to fragments changed by one merged commit")
    policy = commands.add_parser("policy")
    policy.add_argument("action", choices=["plan", "apply", "check"])
    policy.add_argument("--repo", help="GitHub OWNER/NAME (defaults to this checkout's origin)")
    args = parser.parse_args()
    try:
        if args.command == "changelog":
            changelog(args.action, args.version, args.date, args.json, args.fragment)
        else:
            policy_command(args.action, args.repo)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"sdlc: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
