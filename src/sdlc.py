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

POLICY_DIR = Path(os.environ.get("SDLC_POLICY_MODULE_PATH", Path(__file__).resolve().parents[1] / "actions/repository-policy"))
sys.path.insert(0, str(POLICY_DIR))
import repository_policy
import ruleset_api

ROOT = Path.cwd()
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


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generated_tree_digest(changelog_bytes, bindings):
    """Digest the canonical generated records, excluding the manifest."""
    records = [("CHANGELOG.md", "file", changelog_bytes)]
    records.extend((item["path"], "deleted", item["blob_sha"].encode("ascii")) for item in bindings)
    payload = b"".join(path.encode() + b"\0" + kind.encode() + b"\0" + content + b"\0" for path, kind, content in sorted(records))
    return hashlib.sha256(payload).hexdigest()


def _generated_tree_digest(changelog_path, bindings):
    return generated_tree_digest(changelog_path.read_bytes(), bindings)


def verify_release_tree(source_sha, merged_sha, manifest):
    """Verify the actual merged tree against a reconstructible snapshot."""
    if manifest.get("schema") != 1 or manifest.get("source_main_sha") != source_sha:
        raise ValueError("release manifest schema or source SHA is invalid")
    if _git("rev-parse", f"{merged_sha}^") != source_sha:
        raise ValueError("merged release commit parent does not equal source_main_sha")
    fragments = manifest.get("fragments")
    if not isinstance(fragments, list) or fragments != sorted(fragments, key=lambda item: item.get("path", "")):
        raise ValueError("release manifest fragments must be sorted")
    bindings = []
    for item in fragments:
        if not isinstance(item, dict) or set(("path", "blob_sha")) - set(item):
            raise ValueError("release manifest fragment entries require path and blob_sha")
        path, blob = item["path"], item["blob_sha"]
        if _git("rev-parse", f"{source_sha}:{path}") != blob:
            raise ValueError(f"source fragment blob does not match manifest: {path}")
        try:
            _git("rev-parse", f"{merged_sha}:{path}")
        except subprocess.CalledProcessError:
            pass
        else:
            raise ValueError(f"consumed fragment remains in merged tree: {path}")
        bindings.append(item)
    changed = set(_git("diff", "--name-only", source_sha, merged_sha).splitlines())
    allowed = {"CHANGELOG.md", MANIFEST_PATH} | {item["path"] for item in bindings}
    if changed != allowed:
        raise ValueError(f"merged release changed unexpected paths: {sorted(changed - allowed)}")
    changelog = subprocess.run(["git", "show", f"{merged_sha}:CHANGELOG.md"], cwd=ROOT, check=True, capture_output=True).stdout
    if manifest.get("changelog_sha256") != hashlib.sha256(changelog).hexdigest():
        raise ValueError("merged CHANGELOG.md digest does not match manifest")
    expected = generated_tree_digest(changelog, bindings)
    if manifest.get("generated_tree") != expected:
        raise ValueError("merged generated tree digest does not match manifest")
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
    bindings = []
    for path, item in entries:
        relative = path.relative_to(ROOT).as_posix()
        blob = _git("rev-parse", f"{source_sha}:{relative}")
        bindings.append({"path": relative, "blob_sha": blob, "type": item["type"], "semver": item["semver"], "summary": item["summary"]})
    changelog("finalize", version=version, date=release_date, selected=[item["path"] for item in bindings])
    changelog_path = ROOT / "CHANGELOG.md"
    generated_tree_sha = _generated_tree_digest(changelog_path, bindings)
    manifest = {
        "schema": 1,
        "version": version,
        "prior_released_boundary": previous.get("source_main_sha") if previous else None,
        "source_main_sha": source_sha,
        "fragments": [{key: item[key] for key in ("path", "blob_sha")} for item in bindings],
        "changelog_sha256": _sha256(changelog_path),
        "generated_tree": generated_tree_sha,
        "publication": {"version": version, "source_main_sha": source_sha},
    }
    manifest_path = ROOT / MANIFEST_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


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
    all_rulesets, _ = ruleset_api.read_rulesets(full_name, include_parents=(action in ("plan", "check")))
    named = [item for item in all_rulesets if item.get("name") == repository_policy.RULESET_NAME]
    matches = [item for item in named if item.get("source", full_name) == full_name]
    if len(matches) > 1:
        raise ValueError(f"found {len(matches)} canonical rulesets named {repository_policy.RULESET_NAME!r}; resolve duplicates manually")
    current = matches[0] if matches else None
    if action == "plan":
        print(json.dumps({"repository": full_name, "default_branch": branch, "action": "create" if current is None else ("update" if repository_policy.normalize_ruleset(current, desired) != repository_policy.normalize_ruleset(desired, desired) else "no-op"), "desired": desired, "current": current, "inherited_canonical_rulesets": [item for item in named if item not in matches], "unmanaged_rulesets": [item for item in all_rulesets if item.get("name") != repository_policy.RULESET_NAME]}, indent=2, sort_keys=True))
        return
    if action == "check":
        repository_policy.check_live(current, desired, full_name)
        print(f"policy matches live ruleset for {full_name}")
        return
    if action == "apply":
        if current is None:
            subprocess.run(["gh", "api", "--method", "POST", f"repos/{full_name}/rulesets", "--input", "-"], input=json.dumps(desired), text=True, check=True, capture_output=True)
        elif repository_policy.normalize_ruleset(current, desired) != repository_policy.normalize_ruleset(desired, desired):
            subprocess.run(["gh", "api", "--method", "PUT", f"repos/{full_name}/rulesets/{current['id']}", "--input", "-"], input=json.dumps(desired), text=True, check=True, capture_output=True)
        verify, _ = ruleset_api.read_rulesets(full_name, include_parents=False)
        verified = [item for item in verify if item.get("name") == repository_policy.RULESET_NAME and item.get("source", full_name) == full_name]
        if len(verified) != 1 or repository_policy.normalize_ruleset(verified[0], desired) != repository_policy.normalize_ruleset(desired, desired):
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
    release = commands.add_parser("release")
    release_commands = release.add_subparsers(dest="release_action", required=True)
    snapshot = release_commands.add_parser("snapshot")
    snapshot.add_argument("--source-sha", required=True)
    snapshot.add_argument("--date")
    snapshot.add_argument("--json", action="store_true")
    policy = commands.add_parser("policy")
    policy.add_argument("action", choices=["plan", "apply", "check"])
    policy.add_argument("--repo", help="GitHub OWNER/NAME (defaults to this checkout's origin)")
    args = parser.parse_args()
    try:
        if args.command == "changelog":
            changelog(args.action, args.version, args.date, args.json, args.fragment)
        elif args.command == "release" and args.release_action == "snapshot":
            manifest = release_snapshot(args.source_sha, args.date)
            if args.json:
                print(json.dumps({"release": manifest is not None, "manifest": manifest}, sort_keys=True))
            elif manifest:
                print(f"generated release candidate {manifest['version']} from {manifest['source_main_sha']}")
            else:
                print("no changelog fragments; no release")
        else:
            policy_command(args.action, args.repo)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"sdlc: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
