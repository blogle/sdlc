#!/usr/bin/env python3
"""Minimal, versioned SDLC contract runner and changelog tool."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path.cwd()
CONTRACT = ROOT / "ci.nix.json"
RANK = {"patch": 1, "minor": 2, "major": 3}


def load_contract(path=CONTRACT):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read contract {path}: {exc}") from exc
    if data.get("schemaVersion") != 1:
        raise ValueError("contract schemaVersion must be 1")
    stages = data.get("stages")
    if not isinstance(stages, dict):
        raise ValueError("contract stages must be an object")
    allowed = {"pr-fast", "candidate", "release"}
    if set(stages) - allowed:
        raise ValueError(f"unknown stages: {', '.join(sorted(set(stages)-allowed))}")
    for name, stage in stages.items():
        if not isinstance(stage, dict) or not isinstance(stage.get("targets", []), list):
            raise ValueError(f"stage {name} targets must be an array")
        if not all(isinstance(t, str) and t for t in stage.get("targets", [])):
            raise ValueError(f"stage {name} targets must be non-empty strings")
        if not all(isinstance(c, list) and c and all(isinstance(x, str) for x in c) for c in stage.get("commands", [])):
            raise ValueError(f"stage {name} commands must be arrays of argv strings")
    return data


def plan(contract, stage):
    if stage not in {"pr-fast", "candidate", "release"}:
        raise ValueError(f"unknown stage {stage}")
    spec = contract["stages"].get(stage)
    if spec is None:
        raise ValueError(f"contract does not declare stage {stage}")
    steps = []
    if spec.get("targets"):
        steps.append(("nix", ["build", *spec["targets"]]))
    steps.extend(("command", cmd) for cmd in spec.get("commands", []))
    return steps


def run(stage):
    contract = load_contract()
    if stage == "release":
        print("release stage is publication-only; contract commands are explicit hooks")
    steps = plan(contract, stage)
    for kind, argv in steps:
        command = argv if kind == "command" else ["nix", *argv]
        print("+", " ".join(command), flush=True)
        subprocess.run(command, check=True)


def fragments():
    directory = ROOT / ".changes"
    if not directory.exists():
        return []
    result = []
    for path in sorted(directory.glob("*.json")):
        try:
            item = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid JSON: {exc}") from exc
        if set(("type", "semver", "summary")) - set(item):
            raise ValueError(f"{path}: requires type, semver, and summary")
        if item["semver"] not in RANK or not all(isinstance(item[key], str) and item[key].strip() for key in ("type", "summary")):
            raise ValueError(f"{path}: invalid type, semver, or summary")
        result.append((path, item))
    return result


def changelog(action):
    entries = fragments()
    if action == "check":
        print(f"validated {len(entries)} changelog fragment(s)")
        return
    if not entries:
        print("no changelog fragments; no release")
        return
    bump = max((item["semver"] for _, item in entries), key=RANK.get)
    rendered = "\n".join(f"- **{item['type']}**: {item['summary']}" for _, item in entries)
    if action == "preview":
        print(f"SemVer: {bump}\n\n{rendered}")
        return
    if action != "finalize":
        raise ValueError(f"unknown changelog action {action}")
    changelog_path = ROOT / "CHANGELOG.md"
    old = changelog_path.read_text() if changelog_path.exists() else "# Changelog\n"
    section = f"\n## Unreleased ({bump})\n\n{rendered}\n"
    changelog_path.write_text(old.rstrip() + "\n" + section)
    for path, _ in entries:
        path.unlink()
    print(f"finalized {len(entries)} fragment(s) ({bump})")


def main():
    parser = argparse.ArgumentParser(prog="sdlc")
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("run")
    runner.add_argument("stage", choices=["pr-fast", "candidate", "release"])
    changes = commands.add_parser("changelog")
    changes.add_argument("action", choices=["check", "preview", "finalize"])
    args = parser.parse_args()
    try:
        run(args.stage) if args.command == "run" else changelog(args.action)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"sdlc: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
