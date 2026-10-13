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
import hashlib

ROOT = Path.cwd()
CONTRACT_ROOT = Path(os.environ.get("SDLC_SOURCE_PATH", Path(__file__).resolve().parents[1]))
RANK = {"patch": 1, "minor": 2, "major": 3}
VERSION_RE = re.compile(r"^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
MANIFEST_PATH = ".sdlc/release.json"


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


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def _manifest_at(ref="HEAD"):
    try:
        raw = _git("show", f"{ref}:{MANIFEST_PATH}")
    except subprocess.CalledProcessError:
        return None
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{MANIFEST_PATH} at {ref}: invalid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or not VERSION_RE.match(str(manifest.get("version", ""))):
        raise ValueError(f"{MANIFEST_PATH} at {ref}: missing valid version")
    return manifest


def _version_after(previous, bump):
    if previous:
        match = VERSION_RE.match(str(previous))
        if not match:
            raise ValueError(f"invalid previous release version {previous!r}")
        major, minor, patch = map(int, match.groups())
    else:
        major, minor, patch = max(_tag_versions(), default=(0, 0, 0))
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _tag_versions():
    try:
        tags = _git("tag", "--list", "v[0-9]*").splitlines()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    return [tuple(map(int, VERSION_RE.match(tag).groups())) for tag in tags if VERSION_RE.match(tag)]


def _fragment_digest(entries):
    payload = b"".join(
        path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes() + b"\0"
        for path, _ in sorted(entries, key=lambda entry: entry[0].as_posix())
    )
    return hashlib.sha256(payload).hexdigest()


def verify_release_tree(source_sha, merged_sha, manifest):
    """Verify the minimal source-bound release manifest and consumed fragments."""
    if manifest.get("source_sha") != source_sha or not VERSION_RE.fullmatch(str(manifest.get("version", ""))):
        raise ValueError("release manifest version or source SHA is invalid")
    if _git("rev-parse", f"{merged_sha}^") != source_sha:
        raise ValueError("merged release commit parent does not equal source_sha")
    paths = _git("ls-tree", "-r", "--name-only", source_sha, "--", ".changes").splitlines()
    paths = sorted(path for path in paths if path.endswith(".json"))
    if not paths:
        raise ValueError("release source has no change fragments")
    payload = b""
    for path in paths:
        content = subprocess.run(["git", "show", f"{source_sha}:{path}"], cwd=ROOT, check=True, capture_output=True).stdout
        payload += path.encode() + b"\0" + content + b"\0"
        try:
            _git("rev-parse", f"{merged_sha}:{path}")
        except subprocess.CalledProcessError:
            pass
        else:
            raise ValueError(f"consumed fragment remains in merged tree: {path}")
    if hashlib.sha256(payload).hexdigest() != manifest.get("fragments_sha256"):
        raise ValueError("release fragment digest does not match source")
    changed = set(_git("diff", "--name-only", source_sha, merged_sha).splitlines())
    allowed = {"CHANGELOG.md", MANIFEST_PATH, *paths}
    if changed != allowed:
        raise ValueError(f"merged release changed unexpected paths: {sorted(changed ^ allowed)}")
    return True


def release_snapshot(source_sha, date=None):
    """Generate one deterministic rolling candidate from an exact main snapshot."""
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("--source-sha must be a full commit SHA")
    if _git("rev-parse", source_sha) != source_sha:
        raise ValueError(f"source SHA {source_sha} is not available")
    if _git("rev-parse", "HEAD") != source_sha:
        raise ValueError("snapshot must be generated from the source SHA checkout")
    entries = fragments()
    if not entries:
        return None
    previous = _manifest_at(source_sha)
    bump = bump_intent(entries)
    version = _version_after(previous.get("version") if previous else None, bump)
    release_date = date or _git("show", "-s", "--format=%cs", source_sha)
    fragment_hash = _fragment_digest(entries)
    changelog("finalize", version=version, date=release_date, selected=[path.relative_to(ROOT).as_posix() for path, _ in entries])
    manifest = {"version": version, "source_sha": source_sha, "fragments_sha256": fragment_hash}
    manifest_path = ROOT / MANIFEST_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def render(entries):
    return "\n".join(f"- **{item['type']}**: {item['summary']}" for _, item in entries)


def changelog(action, version=None, date=None, json_output=False, selected=None):
    if action == "generate":
        source_sha = _git("rev-parse", "HEAD")
        manifest = release_snapshot(source_sha, date)
        if json_output:
            print(json.dumps({"release": manifest is not None, "manifest": manifest}, sort_keys=True))
        elif manifest:
            print(f"generated release {manifest['version']} from {manifest['source_sha']}")
        else:
            print("no changelog fragments; no release")
        return
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
    elif action in ("finalize", "generate"):
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


def _canonical_ruleset():
    path = CONTRACT_ROOT / "rulesets/default-branch.json"
    return json.loads(path.read_text())


def _ruleset(repo):
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/rulesets"], check=True,
        capture_output=True, text=True,
    )
    matches = [item for item in json.loads(result.stdout) if item.get("name") == "SDLC default branch"]
    if len(matches) > 1:
        raise ValueError(f"multiple SDLC default branch rulesets exist on {repo}")
    if not matches:
        return None
    return subprocess.run(
        ["gh", "api", f"repos/{repo}/rulesets/{matches[0]['id']}"],
        check=True, capture_output=True, text=True,
    )


def _matches_ruleset(actual, desired):
    # Project the API response onto this pinned contract's exact payload.
    # GitHub adds metadata (for example required-check app IDs) and can reorder
    # arrays; fields outside the desired payload are not policy.
    def project(value, shape):
        if isinstance(shape, dict):
            if not isinstance(value, dict):
                return None
            return {key: project(value.get(key), expected) for key, expected in shape.items()}
        if isinstance(shape, list):
            if not isinstance(value, list):
                return None
            values = [project(item, shape[0]) for item in value] if shape else value
            return sorted(values, key=lambda item: json.dumps(item, sort_keys=True))
        return value

    if not isinstance(actual, dict):
        return False
    for key, expected in desired.items():
        if key != "rules" and project(actual.get(key), expected) != project(expected, expected):
            return False
    actual_rules = actual.get("rules", [])
    for expected in desired["rules"]:
        matches = [item for item in actual_rules if item.get("type") == expected["type"]]
        if len(matches) != 1 or project(matches[0], expected) != project(expected, expected):
            return False
    return len(actual_rules) == len(desired["rules"])


def ruleset_command(action, repo, dry_run=False):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("--repo must be OWNER/NAME")
    desired = _canonical_ruleset()
    try:
        current_raw = _ruleset(repo)
    except subprocess.CalledProcessError:
        if dry_run:
            print(json.dumps({"repository": repo, "action": "read-unavailable", "ruleset": desired}, indent=2, sort_keys=True))
            return
        raise
    current = json.loads(current_raw.stdout) if current_raw else None
    equal = current is not None and _matches_ruleset(current, desired)
    operation = "create" if current is None else ("no-op" if equal else "update")
    if action == "plan" or dry_run:
        print(json.dumps({"repository": repo, "action": operation, "ruleset": desired}, indent=2, sort_keys=True))
        return
    if action == "check":
        if not equal:
            raise ValueError(f"canonical SDLC ruleset is {operation} on {repo}; run `sdlc ruleset apply --repo {repo}`")
        print(f"canonical ruleset matches for {repo}")
        return
    if not equal:
        endpoint = f"repos/{repo}/rulesets" if current is None else f"repos/{repo}/rulesets/{current['id']}"
        method = "POST" if current is None else "PUT"
        subprocess.run(["gh", "api", "--method", method, endpoint, "--input", "-"], input=json.dumps(desired), text=True, check=True)
    ruleset_command("check", repo)


def install_adapters(check=False):
    lock = json.loads((ROOT / "flake.lock").read_text())
    inputs = lock["nodes"]["root"]["inputs"]
    if "sdlc" not in inputs:
        if check:
            print("SDLC self-onboarding is deferred")
            return
        raise ValueError("add a pinned sdlc input before generating consumer adapters")
    node = inputs["sdlc"]
    if isinstance(node, list):
        node = node[0]
    revision = lock["nodes"][node]["locked"].get("rev")
    if not revision or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("flake.lock must pin SDLC to a full GitHub commit")
    workflow = f"""# Generated by sdlc install; do not edit.\nname: SDLC CI\non:\n  pull_request:\n  merge_group:\n  push:\n    branches: [main, master]\npermissions:\n  contents: read\njobs:\n  sdlc:\n    uses: blogle/sdlc/.github/workflows/ci.yml@{revision}\n"""
    release_workflow = f"""# Generated by sdlc install; do not edit.\nname: SDLC release\non:\n  pull_request_target:\n    types: [opened, synchronize, reopened, labeled, unlabeled]\n  push:\n    branches: [main, master]\n  schedule:\n    - cron: '17 * * * *'\n  workflow_dispatch:\npermissions:\n  contents: read\n  pull-requests: read\njobs:\n  release:\n    uses: blogle/sdlc/.github/workflows/release.yml@{revision}\n    secrets: inherit\n"""
    generated = {
        ".github/workflows/sdlc.yml": workflow,
        ".github/workflows/sdlc-release.yml": release_workflow,
        ".mergify.yml": (CONTRACT_ROOT / ".mergify.yml").read_text(),
        "renovate.json": json.dumps({"$schema": "https://docs.renovatebot.com/renovate-schema.json", "extends": ["github>blogle/sdlc#v1"]}, indent=2) + "\n",
    }
    mismatches = []
    for relative, content in generated.items():
        path = ROOT / relative
        if not path.exists() or path.read_text() != content:
            mismatches.append(relative)
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
    if check and mismatches:
        raise ValueError("generated SDLC adapters are stale: " + ", ".join(mismatches))
    if not check:
        (ROOT / ".changes").mkdir(exist_ok=True)
    print("SDLC adapters match the locked contract" if check else f"generated SDLC adapters for {revision}")


def main():
    parser = argparse.ArgumentParser(prog="sdlc")
    commands = parser.add_subparsers(dest="command", required=True)
    changes = commands.add_parser("changelog")
    changes.add_argument("action", choices=["check", "plan", "finalize", "generate"])
    changes.add_argument("--version")
    changes.add_argument("--date")
    changes.add_argument("--json", action="store_true")
    changes.add_argument("--fragment", nargs="*", help="limit plan/finalize to fragments changed by one merged commit")
    release = commands.add_parser("release")
    release_commands = release.add_subparsers(dest="release_action", required=True)
    snapshot = release_commands.add_parser("snapshot")
    snapshot.add_argument("--source-sha", required=True)
    snapshot.add_argument("--date")
    snapshot.add_argument("--json", action="store_true")
    for action in ("gate", "merge"):
        operation = release_commands.add_parser(action)
        operation.add_argument("--repo", required=True)
        operation.add_argument("--pr", type=int, required=True)
        operation.add_argument("--release-bot", required=True)
        operation.add_argument("--app-slug", default="")
    release_commands.choices["gate"].add_argument("--historical-merged-sha", default="")
    release_commands.choices["merge"].add_argument("--expected-head")
    publish = release_commands.add_parser("publish")
    publish.add_argument("merged_sha")
    reconcile = release_commands.add_parser("reconcile")
    reconcile.add_argument("--repo")
    ruleset = commands.add_parser("ruleset")
    ruleset_commands = ruleset.add_subparsers(dest="ruleset_action", required=True)
    for action in ("plan", "apply", "check"):
        operation = ruleset_commands.add_parser(action)
        operation.add_argument("--repo", required=True, help="GitHub OWNER/NAME")
    enable = commands.add_parser("enable")
    enable.add_argument("--repo", required=True)
    enable.add_argument("--dry-run", action="store_true")
    install = commands.add_parser("install")
    install.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "changelog":
            changelog(args.action, args.version, args.date, args.json, args.fragment)
        elif args.command == "release" and args.release_action == "snapshot":
            manifest = release_snapshot(args.source_sha, args.date)
            if args.json:
                print(json.dumps({"release": manifest is not None, "manifest": manifest}, sort_keys=True))
            elif manifest:
                print(f"generated release candidate {manifest['version']} from {manifest['source_sha']}")
            else:
                print("no changelog fragments; no release")
        elif args.command == "release" and args.release_action in ("gate", "merge"):
            import release_gate
            return release_gate._gate_command(args) if args.release_action == "gate" else release_gate._merge_command(args)
        elif args.command == "release" and args.release_action == "publish":
            import release_publisher
            identity = release_publisher.validate_checkout(args.merged_sha)
            print(json.dumps(identity.__dict__, sort_keys=True, default=list))
        elif args.command == "release" and args.release_action == "reconcile":
            import release_reconcile
            return release_reconcile.reconcile(args.repo)
        elif args.command == "ruleset":
            ruleset_command(args.ruleset_action, args.repo)
        elif args.command == "enable":
            ruleset_command("apply", args.repo, dry_run=args.dry_run)
        elif args.command == "install":
            install_adapters(check=args.check)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"sdlc: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
