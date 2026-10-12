"""Fail-closed, prepare-only SDLC fleet contract.

GitHub authentication and mutation belong to the trusted dotfiles controller.
This module supplies the typed contract, canonical renderers, generated caller
files, and a reconciler with an injected read client.  There is intentionally no
default verifier and no mutation fallback.
"""

import argparse
import json
from pathlib import Path
import re
import sys


SCHEMA_VERSION = 1
POLICY_SCHEMA_VERSION = 1
TRUSTED_SOURCE = "blogle/sdlc"
CHECK_NAMES = {"check": "sdlc-check", "verify": "sdlc-verify", "policy": "sdlc-policy"}
LEGACY_ALIASES = {
    "sdlc-check": ("sdlc / pr-fast",),
    "sdlc-verify": ("sdlc / candidate",),
    "sdlc-policy": ("sdlc / policy",),
}
PR_REQUIRED_CHECKS = ("sdlc-check", "sdlc-policy")
QUEUE_CHECK = "sdlc-verify"
ALL_CHECK_NAMES = frozenset(CHECK_NAMES.values()) | frozenset(
    alias for aliases in LEGACY_ALIASES.values() for alias in aliases
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


class FleetContractError(ValueError):
    """A fail-closed contract, attestation, or observed-state error."""


def _object(value, label):
    if not isinstance(value, dict):
        raise FleetContractError(f"{label} must be an object")
    return value


def _sha(value, label):
    if not isinstance(value, str) or not SHA_RE.fullmatch(value.lower()):
        raise FleetContractError(f"{label} must be a 40-character lowercase commit SHA")
    return value.lower()


def _known_checks(values, label):
    if not isinstance(values, list) or values != sorted(set(values)) or not set(values).issubset(ALL_CHECK_NAMES):
        raise FleetContractError(f"{label} must be a sorted list of known checks")
    return values


def validate_manifest(manifest):
    manifest = _object(manifest, "manifest")
    if manifest.get("schema") != SCHEMA_VERSION:
        raise FleetContractError("unsupported fleet manifest schema")
    owner = _object(manifest.get("owner"), "manifest.owner")
    if not isinstance(owner.get("login"), str) or not owner["login"]:
        raise FleetContractError("manifest.owner.login is required")
    if not isinstance(owner.get("id"), int) or owner["id"] <= 0:
        raise FleetContractError("manifest.owner.id must be a positive immutable account ID")
    repositories = manifest.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise FleetContractError("manifest.repositories must be a non-empty list")
    seen = set()
    for repository in repositories:
        repository = _object(repository, "manifest repository")
        name = repository.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise FleetContractError("repository names must be plain GitHub names")
        if name in seen:
            raise FleetContractError(f"duplicate repository: {name}")
        seen.add(name)
        if not isinstance(repository.get("id"), int) or repository["id"] <= 0:
            raise FleetContractError(f"{name}: immutable repository id is required")
        if not isinstance(repository.get("enabled"), bool):
            raise FleetContractError(f"{name}: enabled must be boolean")
        if repository.get("state", "active") not in ("active", "suspended"):
            raise FleetContractError(f"{name}: state must be active or suspended")
    return manifest


def _validate_floor(floor):
    floor = _object(floor, "policy.security_floor")
    required = _known_checks(floor.get("required_checks"), "security_floor.required_checks")
    if "sdlc-check" not in required or "sdlc-verify" in required:
        raise FleetContractError("floor must require sdlc-check and never require sdlc-verify on ordinary PRs")
    booleans = (
        "protected_default_branch", "protected_release_tags", "no_force_push",
        "no_destructive_changes", "require_pull_requests", "squash_only",
        "dismiss_stale_reviews",
    )
    if any(floor.get(name) is not True for name in booleans):
        raise FleetContractError("security floor is missing a mandatory protection")
    if floor.get("integration_path") != "merge_queue":
        raise FleetContractError("security floor must use the approved merge queue integration path")
    publishers = floor.get("trusted_publishers")
    if not isinstance(publishers, dict):
        raise FleetContractError("security floor must bind every SDLC check to an observed publisher app")
    legacy_unverified = True
    for name in PR_REQUIRED_CHECKS + (QUEUE_CHECK,):
        binding = publishers.get(name)
        if isinstance(binding, dict):
            if not isinstance(binding.get("app_id"), int) or binding["app_id"] <= 0 or binding.get("source") != "github-actions":
                raise FleetContractError("security floor publisher bindings require positive GitHub Actions app IDs")
            legacy_unverified = False
        elif binding != TRUSTED_SOURCE:
            raise FleetContractError("security floor publisher binding is invalid")
    tag_pattern = floor.get("release_tag_pattern", "refs/tags/v*")
    if tag_pattern != "refs/tags/v*":
        raise FleetContractError("release tag protection must match immutable version tags refs/tags/v*")
    return {**floor, "publisher_binding_verified": not legacy_unverified}


def _validate_codeowners(policy):
    kind = policy.get("repository_kind", "consumer")
    codeowners = policy.get("codeowner_protection", {})
    if not isinstance(codeowners, dict):
        raise FleetContractError("codeowner_protection must be an object")
    required = codeowners.get("required", False)
    paths = codeowners.get("sensitive_paths", [])
    if not isinstance(required, bool) or not isinstance(paths, list) or any(not isinstance(path, str) or not path.startswith("/") for path in paths):
        raise FleetContractError("codeowner protection requires absolute sensitive paths")
    if kind == "dotfiles" and (required is not True or not paths):
        raise FleetContractError("dotfiles must require codeowner approval for declared sensitive paths")
    if kind != "dotfiles" and required:
        raise FleetContractError("only the dotfiles policy may enable sensitive codeowner protection")


def validate_policy(policy, trusted_bypass_user_id=None):
    policy = _object(policy, "policy")
    if policy.get("schema") != POLICY_SCHEMA_VERSION:
        raise FleetContractError("unsupported fleet policy schema")
    check_policy_compatibility(policy.get("version", POLICY_SCHEMA_VERSION))
    provenance = _object(policy.get("provenance"), "policy.provenance")
    if provenance.get("source") != TRUSTED_SOURCE:
        raise FleetContractError("policy provenance source must be blogle/sdlc")
    _sha(provenance.get("revision"), "policy.provenance.revision")
    _validate_floor(policy.get("security_floor"))
    _validate_codeowners(policy)
    bypass = _object(policy.get("bypass"), "policy.bypass")
    user_id = bypass.get("user_id")
    if not isinstance(user_id, int) or user_id <= 0 or trusted_bypass_user_id != user_id:
        raise FleetContractError("bypass identity must come from trusted fleet configuration")
    if bypass.get("kind") != "personal_user" or bypass.get("actors") != [user_id] or bypass.get("count") != 1:
        raise FleetContractError("exactly one personal user must be the bypass actor")
    return policy


def verify_provenance(provenance, *, verifier, consumer_pin, expected_head=None):
    """Verify source, immutable pin, and protected ancestry using an injected API client.

    ``verifier`` must be a trusted controller object whose implementation reads
    GitHub commit/ref data with authenticated immutable IDs.  A boolean in the
    input document is never accepted as proof.
    """
    provenance = _object(provenance, "provenance")
    if provenance.get("source") != TRUSTED_SOURCE:
        raise FleetContractError("untrusted SDLC source")
    revision = _sha(provenance.get("revision"), "provenance.revision")
    if not isinstance(consumer_pin, str) or consumer_pin != revision:
        raise FleetContractError("consumer flake.lock pin does not equal policy provenance")
    if verifier is None or not callable(getattr(verifier, "verify", None)):
        raise FleetContractError("authenticated provenance verifier is required")
    attestation = _object(verifier.verify(TRUSTED_SOURCE, revision), "provenance attestation")
    if attestation.get("source") != TRUSTED_SOURCE or attestation.get("repository_id") is None:
        raise FleetContractError("provenance attestation has incomplete source identity")
    if _sha(attestation.get("commit_sha"), "attestation.commit_sha") != revision:
        raise FleetContractError("provenance attestation commit differs from pinned revision")
    if (
        attestation.get("reachable_from_protected") is not True
        or attestation.get("ancestry_verified") is not True
        or attestation.get("protected_ref") != "refs/heads/main"
        or attestation.get("protected_repository_id") != attestation.get("repository_id")
        or attestation.get("immutable") is not True
    ):
        raise FleetContractError("SDLC revision is not proven reachable from protected history")
    if expected_head is not None and revision != _sha(expected_head, "expected_head"):
        raise FleetContractError("SDLC revision does not match the verified head")
    return {**attestation, "source": TRUSTED_SOURCE, "revision": revision}


def verify_release_identity(*, expected_head, actual_head, expected_version, actual_version, source_main_sha):
    expected_head = _sha(expected_head, "expected_head")
    actual_head = _sha(actual_head, "actual_head")
    source_main_sha = _sha(source_main_sha, "source_main_sha")
    if expected_head != actual_head:
        raise FleetContractError("release head changed during verification")
    if not isinstance(expected_version, str) or not VERSION_RE.fullmatch(expected_version):
        raise FleetContractError("expected_version is invalid")
    if actual_version != expected_version:
        raise FleetContractError("release version changed during verification")
    return {"head": actual_head, "version": actual_version, "source_main_sha": source_main_sha}


def check_policy_compatibility(policy_version, *, supported=(POLICY_SCHEMA_VERSION,)):
    if not isinstance(policy_version, int) or policy_version not in supported:
        raise FleetContractError(f"unsupported policy version: {policy_version!r}")
    return {"policy_version": policy_version, "supported": list(supported)}


def render_canonical_policy(default_branch, policy, *, trusted_bypass_user_id):
    """Render branch and release-tag rulesets from the upstream floor."""
    validate_policy(policy, trusted_bypass_user_id)
    floor = _validate_floor(policy["security_floor"])
    if not floor["publisher_binding_verified"]:
        raise FleetContractError("canonical rendering is blocked until observed GitHub Actions publisher app IDs are configured")
    bypass_actor = {"actor_type": "User", "actor_id": trusted_bypass_user_id, "bypass_mode": "always"}
    require_code_owner = policy.get("repository_kind", "consumer") == "dotfiles"
    if not isinstance(default_branch, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", default_branch):
        raise FleetContractError("default branch must be a plain branch name")
    contexts = [{"context": name} for name in floor["required_checks"]]
    branch = {
        "name": "SDLC default branch",
        "target": "branch", "enforcement": "active", "bypass_actors": [bypass_actor],
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {"type": "deletion"}, {"type": "non_fast_forward"},
            {"type": "pull_request", "parameters": {
                "allowed_merge_methods": ["squash"],
                "dismiss_stale_reviews_on_push": True,
                "require_code_owner_review": require_code_owner,
                "require_last_push_approval": False,
                "required_approving_review_count": 0,
                "required_review_thread_resolution": False,
            }},
            {"type": "required_status_checks", "parameters": {
                "strict_required_status_checks_policy": False,
                "do_not_enforce_on_create": False,
                "required_status_checks": [
                    {**item, "app_id": floor["trusted_publishers"][item["context"]]["app_id"]}
                    for item in contexts
                ],
            }},
        ],
    }
    tags = {
        "name": "SDLC release tags", "target": "tag", "enforcement": "active", "bypass_actors": [bypass_actor],
        "conditions": {"ref_name": {"include": [floor.get("release_tag_pattern", "refs/tags/v*")], "exclude": []}},
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
    }
    return {"branch": branch, "release_tags": tags}


def render_caller_workflows(revision):
    """Generate ordinary always-run caller jobs and legacy compatibility aliases."""
    if not isinstance(revision, str) or not (VERSION_RE.fullmatch(revision) or SHA_RE.fullmatch(revision)):
        raise FleetContractError("caller revision must be an immutable release or commit")
    return {".github/workflows/sdlc.yml": f"""name: SDLC generated callers
on:
  pull_request:
  merge_group:
  push:
    branches: [main, master]
permissions:
  contents: read
jobs:
  check:
    uses: blogle/sdlc/.github/workflows/pr-fast.yml@{revision}
  verify:
    if: github.event_name == 'merge_group' || (github.event_name == 'pull_request' && startsWith(github.head_ref, 'mergify/merge-queue/')) || (github.event_name == 'push' && github.ref == format('refs/heads/{{0}}', github.event.repository.default_branch))
    uses: blogle/sdlc/.github/workflows/candidate.yml@{revision}
  policy:
    uses: blogle/sdlc/.github/workflows/policy-check.yml@{revision}
  sdlc-check:
    name: sdlc-check
    needs: check
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: blogle/sdlc/actions/status-gate@{revision}
        with:
          result: ${{{{ needs.check.result }}}}
          stage: check
  sdlc-verify:
    name: sdlc-verify
    needs: verify
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: blogle/sdlc/actions/status-gate@{revision}
        with:
          result: ${{{{ needs.verify.result }}}}
          stage: verify
  sdlc-policy:
    name: sdlc-policy
    needs: policy
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: blogle/sdlc/actions/status-gate@{revision}
        with:
          result: ${{{{ needs.policy.result }}}}
          stage: policy
  legacy-pr-fast:
    name: sdlc / pr-fast
    needs: sdlc-check
    if: always()
    runs-on: ubuntu-latest
    steps:
      - run: test "${{{{ needs.sdlc-check.result }}}}" = success
  legacy-candidate:
    name: sdlc / candidate
    needs: sdlc-verify
    if: always()
    runs-on: ubuntu-latest
    steps:
      - run: test "${{{{ needs.sdlc-verify.result }}}}" = success
  legacy-policy:
    name: sdlc / policy
    needs: sdlc-policy
    if: always()
    runs-on: ubuntu-latest
    steps:
      - run: test "${{{{ needs.sdlc-policy.result }}}}" = success
"""}


def generated_callers(revision):
    """Compatibility alias for callers of the initial sketch."""
    return {name: {"name": name, "uses": f"blogle/sdlc/.github/workflows/{stage}.yml@{revision}"}
            for stage, name in (("pr-fast", "sdlc-check"), ("candidate", "sdlc-verify"), ("policy-check", "sdlc-policy"))}


def verify_ruleset_readback(live, desired, *, trusted_bypass_user_id):
    """Verify exact bypass identity and owned policy fields after a controller read."""
    live = _object(live, "live ruleset")
    desired = _object(desired, "desired ruleset")
    if live.get("bypass_actors") != desired.get("bypass_actors"):
        raise FleetContractError("live ruleset bypass actors do not match the exact trusted User")
    actor = live.get("bypass_actors", [{}])
    if actor != [{"actor_type": "User", "actor_id": trusted_bypass_user_id, "bypass_mode": "always"}]:
        raise FleetContractError("live ruleset bypass must contain exactly one always-bypass personal User")
    if live.get("name") != desired.get("name") or live.get("target") != desired.get("target"):
        raise FleetContractError("live ruleset identity differs from canonical policy")
    live_status = next((rule for rule in live.get("rules", []) if rule.get("type") == "required_status_checks"), None)
    desired_status = next((rule for rule in desired.get("rules", []) if rule.get("type") == "required_status_checks"), None)
    if live_status != desired_status:
        raise FleetContractError("live required checks or publisher app binding differ from canonical policy")
    return True


def verify_check_publishers(check_runs, floor):
    """Reject same-name check spoofing by requiring the observed Actions app ID."""
    normalized_floor = _validate_floor(floor)
    if not normalized_floor["publisher_binding_verified"]:
        raise FleetContractError("check readiness is blocked until GitHub Actions publisher app IDs are observed")
    publishers = normalized_floor["trusted_publishers"]
    observed = {}
    for check in check_runs:
        context = check.get("name") or check.get("context")
        app = check.get("app") or {}
        if context in publishers:
            if context in observed:
                raise FleetContractError(f"duplicate observed publisher check: {context}")
            if app.get("id") != publishers[context]["app_id"] or app.get("slug") != "github-actions":
                raise FleetContractError(f"check publisher spoofed for {context}")
            observed[context] = check
    missing = set(PR_REQUIRED_CHECKS + (QUEUE_CHECK,)) - set(observed)
    if missing:
        raise FleetContractError("trusted check publisher observations are missing: " + ", ".join(sorted(missing)))
    return observed


def _migration_state(repository, migration_state):
    state = _object(migration_state, "migration_state")
    if state.get("repository") != repository or not isinstance(state.get("generation"), int) or state["generation"] < 0:
        raise FleetContractError("migration state is not scoped to this repository")
    if state.get("in_flight") is True:
        raise FleetContractError("another migration is in flight; serialized per-repository migration required")
    if state.get("phase") not in ("bootstrap", "dual", "cutover", "retire"):
        raise FleetContractError("migration state has an unsupported phase")
    return state


def migration_plan(required_checks, available_checks, *, repository="", migration_state=None, phase="bootstrap", exact_head=False, all_consumers_upgraded=False, mergify_migrated=False):
    """Return a serial, alias-first migration plan with no implicit writes."""
    required = _known_checks(list(required_checks), "required_checks")
    available = set(available_checks)
    if phase not in ("bootstrap", "dual", "cutover", "retire"):
        raise FleetContractError("unknown migration phase")
    state = _migration_state(repository, migration_state) if migration_state is not None else None
    if state is not None and state["phase"] != phase:
        raise FleetContractError("requested migration phase does not match serialized state")
    if phase == "bootstrap":
        return {"phase": phase, "required_checks": [], "operations": [{"kind": "publish_wrappers", "preconditions": {"repository": repository, "generation": state["generation"] if state else 0}}], "prepare_only": True}
    if not exact_head:
        raise FleetContractError("migration requires checks verified at the exact default-branch head")
    missing = set(PR_REQUIRED_CHECKS) - available
    if missing:
        raise FleetContractError("cannot require checks before they exist: " + ", ".join(sorted(missing)))
    if phase == "dual":
        return {"phase": phase, "required_checks": list(PR_REQUIRED_CHECKS), "retain_aliases": True, "operations": [{"kind": "require_checks", "preconditions": {"exact_head": True, "generation": state["generation"] if state else 0}, "checks": list(PR_REQUIRED_CHECKS)}, {"kind": "retain_aliases"}], "prepare_only": True}
    if phase == "cutover" and not mergify_migrated:
        raise FleetContractError("cutover requires Mergify to use stable names first")
    if phase == "retire" and not all_consumers_upgraded:
        raise FleetContractError("retire requires every consumer adapter to be upgraded")
    return {"phase": phase, "required_checks": list(PR_REQUIRED_CHECKS), "retain_aliases": phase != "retire", "operations": [{"kind": "migrate_required_contexts", "preconditions": {"exact_head": True, "mergify_migrated": True}, "checks": list(PR_REQUIRED_CHECKS)}, {"kind": "remove_aliases" if phase == "retire" else "retain_aliases", "preconditions": {"all_consumers_upgraded": all_consumers_upgraded}}], "prepare_only": True}


def verify_identity(expected_owner_id, expected_repository_id, observed):
    observed = _object(observed, "observed repository")
    if observed.get("owner_id") != expected_owner_id or observed.get("repository_id") != expected_repository_id:
        raise FleetContractError("repository identity changed or was replaced")
    if observed.get("transferred") is True or not observed.get("default_branch"):
        raise FleetContractError("repository transfer or missing default branch is unsafe")
    return {"owner_id": expected_owner_id, "repository_id": expected_repository_id, "default_branch": observed["default_branch"]}


def credential_readiness(observed):
    observed = _object(observed, "credential readiness")
    permissions = observed.get("permissions", {})
    required = {"administration": "write", "actions_secrets": "write", "actions_variables": "write", "contents": "read"}
    if any(permissions.get(name) != value for name, value in required.items()):
        raise FleetContractError("fleet App permissions are insufficient")
    if not observed.get("installation_verified"):
        raise FleetContractError("fleet App installation is not verified")
    if observed.get("private_key_exposed") is True:
        raise FleetContractError("private key exposure blocks reconciliation")
    return {"ready": True, "generation": observed.get("generation")}


def next_state(current, observation):
    if current not in ("unknown", "ready", "active", "unsafe", "suspended"):
        raise FleetContractError("unknown fleet state")
    if observation == "suspended":
        return "suspended"
    if observation == "unsafe":
        return "unsafe"
    if observation == "transient":
        return current if current != "unknown" else "unknown"
    if observation == "ready":
        return "ready" if current in ("unknown", "unsafe") else "active"
    raise FleetContractError("unknown reconciliation observation")


def reconcile_repository(desired, observed, *, client=None, apply=False):
    """Compute an observed-to-desired diff through an injected read client.

    ``apply=True`` is intentionally rejected until operator bootstrap defines a
    separately reviewed mutation implementation.
    """
    if apply:
        raise FleetContractError("privileged fleet mutation is disabled in prepare-only SDLC")
    desired = _object(desired, "desired policy")
    observed = _object(observed, "observed repository")
    if client is None or not callable(getattr(client, "read", None)):
        raise FleetContractError("injected authenticated fleet read client is required")
    live = _object(client.read(observed), "live repository observation")
    operations = []
    if live.get("rulesets") != desired.get("rulesets"):
        operations.append({"kind": "ruleset_diff", "preconditions": {"repository_id": observed.get("repository_id"), "observed_revision": live.get("revision")}, "desired": desired.get("rulesets")})
    if live.get("workflow_digest") != desired.get("workflow_digest"):
        operations.append({"kind": "caller_diff", "preconditions": {"repository_id": observed.get("repository_id"), "observed_head": live.get("head_sha")}, "desired_digest": desired.get("workflow_digest")})
    return {"state": next_state(observed.get("state", "unknown"), "ready" if not operations else "unsafe"), "operations": operations, "prepare_only": True, "mutations": []}


def plan(manifest, policy, *, trusted_bypass_user_id, verifier, consumer_pin, expected_head=None, phase="bootstrap", current_checks=(), available_checks=(), exact_head=False):
    validate_manifest(manifest)
    validate_policy(policy, trusted_bypass_user_id)
    provenance = verify_provenance(policy["provenance"], verifier=verifier, consumer_pin=consumer_pin, expected_head=expected_head)
    migration = migration_plan(current_checks, available_checks, phase=phase, exact_head=exact_head)
    return {"schema": SCHEMA_VERSION, "mode": "prepare", "prepare_only": True, "provenance": provenance, "migration": migration, "repositories": [item["name"] for item in manifest["repositories"] if item["enabled"] and item.get("state", "active") == "active"], "suspended": [item["name"] for item in manifest["repositories"] if item.get("state") == "suspended"], "mutations": []}


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise FleetContractError(f"cannot read {path}: {error}") from error


class FileAttestationVerifier:
    """Read an attestation emitted by the trusted controller; never create one."""
    def __init__(self, path):
        self.attestation = _read(path)

    def verify(self, source, revision):
        result = self.attestation
        if result.get("source") != source or result.get("commit_sha") != revision:
            raise FleetContractError("attestation does not match requested source revision")
        if result.get("verified_by") != "dotfiles-trusted-controller":
            raise FleetContractError("attestation was not emitted by the trusted controller")
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sdlc fleet")
    parser.add_argument("command", choices=("plan", "reconcile", "verify"))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--attestation", required=True)
    parser.add_argument("--consumer-pin", required=True)
    parser.add_argument("--trusted-bypass-user-id", required=True, type=int)
    parser.add_argument("--expected-head")
    parser.add_argument("--phase", choices=("bootstrap", "dual", "cutover", "retire"), default="bootstrap")
    parser.add_argument("--exact-head", action="store_true")
    parser.add_argument("--current-check", action="append", default=[])
    parser.add_argument("--available-check", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        result = plan(_read(args.manifest), _read(args.policy), verifier=FileAttestationVerifier(args.attestation), consumer_pin=args.consumer_pin, trusted_bypass_user_id=args.trusted_bypass_user_id, expected_head=args.expected_head, phase=args.phase, current_checks=args.current_check, available_checks=args.available_check, exact_head=args.exact_head)
        result["mode"] = args.command
        if args.command == "reconcile":
            result["action"] = "prepare-corrective-prs"
        print(json.dumps(result, indent=2, sort_keys=True))
    except FleetContractError as error:
        print(f"sdlc fleet: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
