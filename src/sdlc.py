#!/usr/bin/env python3
"""Consumer-working-tree changelog tooling for the shared SDLC flake."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path.cwd()
RANK = {"patch": 1, "minor": 2, "major": 3}
VERSION_RE = re.compile(r"^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def fragments():
    directory = ROOT / ".changes"
    if not directory.exists():
        return []
    result = []
    for path in sorted(directory.glob("*.json")):
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
        tags = subprocess.run(["git", "tag", "--list", "v[0-9]*"], check=True, capture_output=True, text=True).stdout.splitlines()
    except (FileNotFoundError, subprocess.CalledProcessError):
        tags = []
    versions = [tuple(map(int, VERSION_RE.match(tag).groups())) for tag in tags if VERSION_RE.match(tag)]
    major, minor, patch = max(versions, default=(0, 0, 0))
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def write_outputs(path, entries, version):
    if path is None:
        return
    with Path(path).open("a") as stream:
        stream.write(f"release={'true' if entries else 'false'}\n")
        if entries:
            stream.write(f"version={version}\n")
            stream.write(f"bump={bump_intent(entries)}\n")


def render(entries):
    return "\n".join(f"- **{item['type']}**: {item['summary']}" for _, item in entries)


def changelog(action, github_output=None, version=None, date=None):
    entries = fragments()
    bump = bump_intent(entries)
    planned_version = next_version(bump) if bump else None
    write_outputs(github_output, entries, planned_version)
    if action == "check":
        print(f"validated {len(entries)} changelog fragment(s)")
    elif action == "plan":
        if not entries:
            print("no changelog fragments; no release")
        else:
            print(f"release {planned_version} ({bump})\n\n{render(entries)}")
    elif action == "preview":
        if not entries:
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


def main():
    parser = argparse.ArgumentParser(prog="sdlc")
    commands = parser.add_subparsers(dest="command", required=True)
    changes = commands.add_parser("changelog")
    changes.add_argument("action", choices=["check", "plan", "preview", "finalize"])
    changes.add_argument("--github-output")
    changes.add_argument("--version")
    changes.add_argument("--date")
    args = parser.parse_args()
    try:
        changelog(args.action, args.github_output, args.version, args.date)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"sdlc: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
