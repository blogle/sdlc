"""Idempotent GitHub App and release-variable bootstrap helpers."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys
import time

POLICY_DIR = Path(__file__).resolve().parents[1] / "actions/repository-policy"
sys.path.insert(0, str(POLICY_DIR))
import repository_policy
import ruleset_api

def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _jwt(app_id: str, key_file: Path) -> str:
    header = _b64(b'{"alg":"RS256","typ":"JWT"}')
    payload = _b64(json.dumps({"iat": int(time.time()) - 60, "exp": int(time.time()) + 540, "iss": app_id}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key_file)], input=signing_input, check=True, capture_output=True).stdout
    return f"{header}.{payload}.{_b64(signature)}"


def _gh_api(endpoint: str, *, token: str, method: str = "GET") -> dict:
    result = subprocess.run(
        ["gh", "api", endpoint, "--method", method, "--header", f"Authorization: Bearer {token}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _user_api(endpoint: str) -> dict:
    result = subprocess.run(["gh", "api", endpoint], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def _variable(repo: str, name: str) -> str | None:
    try:
        return str(_user_api(f"repos/{repo}/actions/variables/{name}").get("value"))
    except subprocess.CalledProcessError:
        return None


def _set_variable(repo: str, name: str, value: str) -> None:
    subprocess.run(["gh", "variable", "set", name, "--repo", repo, "--body", value], check=True)
    if _variable(repo, name) != value:
        raise ValueError(f"GitHub variable {name} did not retain the requested value")


def _installation(repo: str, app_id: str, private_key_file: str) -> tuple[dict, bytes]:
    if not app_id.isdigit():
        raise ValueError("--app-id must be numeric")
    key_path = Path(private_key_file)
    key = key_path.read_bytes()
    if b"PRIVATE KEY" not in key:
        raise ValueError("--private-key-file does not contain a PEM private key")
    app_token = _jwt(app_id, key_path)
    installation = _gh_api(f"repos/{repo}/installation", token=app_token)
    installation_id = installation.get("id")
    if not isinstance(installation_id, int):
        raise ValueError("the GitHub App is not installed for this repository")
    permissions = installation.get("permissions", {})
    missing = [name for name in ("contents", "pull_requests", "checks") if permissions.get(name) != "write"]
    if missing:
        raise ValueError(f"installed GitHub App lacks write permission(s): {', '.join(missing)}")
    installation_token = _gh_api(f"app/installations/{installation_id}/access_tokens", token=app_token, method="POST").get("token")
    if not installation_token or _gh_api(f"repos/{repo}", token=installation_token).get("full_name") != repo:
        raise ValueError("the installed GitHub App token cannot access the requested repository")
    return installation, key


def _credentials(app_id: str | None, private_key_file: str | None) -> tuple[str, str]:
    app_id = app_id or input("GitHub App ID: ").strip()
    private_key_file = private_key_file or input("Path to GitHub App private key (never logged): ").strip()
    if not app_id or not private_key_file:
        raise ValueError("App ID and private-key path are required; no settings were changed")
    return app_id, private_key_file


def _workflow_readiness(repo: str, branch: str) -> dict:
    files = _user_api(f"repos/{repo}/contents/.github/workflows?ref={branch}")
    if not isinstance(files, list):
        raise ValueError(".github/workflows is missing; commit the SDLC workflow adapters before onboarding")
    text = []
    for item in files:
        if item.get("type") == "file" and item.get("name", "").endswith((".yml", ".yaml")):
            content = _user_api(f"repos/{repo}/contents/{item['path']}?ref={branch}").get("content", "")
            text.append(base64.b64decode(content).decode())
    combined = "\n".join(text)
    required = {
        "sdlc / pr-fast": "stable sdlc / pr-fast job",
        "sdlc / candidate": "stable sdlc / candidate job",
        "sdlc / policy": "stable sdlc / policy job",
        "release-publish": "consumer release-publish hook wiring",
        "packages: write": "trusted package publication permission",
        "SDLC_RELEASE_APP_ID": "release App secret wiring",
        "SDLC_RELEASE_ACTIVATE": "release activation wiring",
    }
    missing = [description for marker, description in required.items() if marker not in combined]
    if missing:
        raise ValueError("required consumer workflow wiring is not committed: " + ", ".join(missing))
    runs = _user_api(f"repos/{repo}/actions/runs?branch={branch}&per_page=50").get("workflow_runs", [])
    if not any(run.get("name") == "SDLC CI" and run.get("conclusion") == "success" for run in runs):
        raise ValueError("no successful SDLC CI run is reporting yet; keep activation false and retry after CI completes")
    justfile = _user_api(f"repos/{repo}/contents/justfile?ref={branch}")
    if "release-publish" not in base64.b64decode(justfile.get("content", "")).decode():
        raise ValueError("consumer justfile has no release-publish hook; activation remains false")
    return {"workflow_files": len(text), "successful_ci": True, "artifact_hook": True, "packages_write": True}


def _policy(repo: str, branch: str, *, apply: bool) -> dict:
    config_item = _user_api(f"repos/{repo}/contents/.github/repository-policy.json?ref={branch}")
    config = json.loads(base64.b64decode(config_item["content"]).decode())
    desired = repository_policy.render_policy(config, branch)
    all_rulesets, _ = ruleset_api.read_rulesets(repo, include_parents=True, anonymous_fallback=False)
    named = [item for item in all_rulesets if item.get("name") == repository_policy.RULESET_NAME]
    owned = [item for item in named if item.get("source", repo) == repo]
    if len(owned) > 1:
        raise ValueError("duplicate canonical rulesets found; refusing to guess")
    current = owned[0] if owned else None
    plan = "create" if current is None else ("update" if repository_policy.normalize_ruleset(current, desired) != repository_policy.normalize_ruleset(desired, desired) else "no-op")
    if apply:
        if current is None:
            subprocess.run(["gh", "api", "--method", "POST", f"repos/{repo}/rulesets", "--input", "-"], input=json.dumps(desired), text=True, check=True, capture_output=True)
        elif plan == "update":
            subprocess.run(["gh", "api", "--method", "PUT", f"repos/{repo}/rulesets/{current['id']}", "--input", "-"], input=json.dumps(desired), text=True, check=True, capture_output=True)
        verified, _ = ruleset_api.read_rulesets(repo, include_parents=False, anonymous_fallback=False)
        owned = [item for item in verified if item.get("name") == repository_policy.RULESET_NAME and item.get("source", repo) == repo]
        if len(owned) != 1:
            raise ValueError("policy apply did not produce exactly one canonical ruleset; activation remains false")
        repository_policy.check_live(owned[0], desired, repo)
    contexts = [
        item["context"]
        for rule in desired["rules"]
        if rule["type"] == "required_status_checks"
        for item in rule["parameters"]["required_status_checks"]
    ]
    return {"plan": plan, "required_contexts": sorted(contexts), "policy_verified": apply}


def onboard(repo: str, app_id: str | None = None, private_key_file: str | None = None, *, dry_run: bool = False) -> None:
    repo_data = _user_api(f"repos/{repo}")
    branch = repo_data["default_branch"]
    app_id, private_key_file = _credentials(app_id, private_key_file)
    installation, key = _installation(repo, app_id, private_key_file)
    app_slug = installation.get("app_slug") or installation.get("app_name")
    if not isinstance(app_slug, str) or not app_slug:
        raise ValueError("GitHub did not return the installed App slug")
    bot_login = f"{app_slug}[bot]"
    readiness = _workflow_readiness(repo, branch)
    policy_plan = _policy(repo, branch, apply=False)
    if dry_run:
        print(json.dumps({"repo": repo, "mode": "prepare", "bot_login": bot_login, "activation": False, "readiness": readiness, "policy": policy_plan}, indent=2, sort_keys=True))
        return
    _set_variable(repo, "SDLC_RELEASE_ACTIVATE", "false")
    _set_variable(repo, "SDLC_RELEASE_BOT_LOGIN", bot_login)
    subprocess.run(["gh", "secret", "set", "SDLC_RELEASE_APP_ID", "--repo", repo, "--body", app_id], check=True)
    subprocess.run(["gh", "secret", "set", "SDLC_RELEASE_APP_PRIVATE_KEY", "--repo", repo], input=key, check=True)
    policy = _policy(repo, branch, apply=True)
    _set_variable(repo, "SDLC_RELEASE_ACTIVATE", "true")
    print(json.dumps({"repo": repo, "mode": "cutover", "activation": True, "readiness": readiness, "policy": policy}, indent=2, sort_keys=True))


def _preflight(repo: str) -> dict:
    result = {"repo": repo, "activation": _variable(repo, "SDLC_RELEASE_ACTIVATE"), "errors": [], "warnings": []}
    result["bot_login"] = _variable(repo, "SDLC_RELEASE_BOT_LOGIN")
    secret_names = _user_api(f"repos/{repo}/actions/secrets?per_page=100").get("secrets", [])
    names = {item.get("name") for item in secret_names}
    result["secrets_present"] = {name: name in names for name in ("SDLC_RELEASE_APP_ID", "SDLC_RELEASE_APP_PRIVATE_KEY")}
    if not all(result["secrets_present"].values()):
        result["errors"].append("release App secrets are not both provisioned")
    if not result["bot_login"]:
        result["errors"].append("SDLC_RELEASE_BOT_LOGIN is not configured")
    try:
        installation = _user_api(f"repos/{repo}/installation")
        result["app_installation"] = {"id": installation.get("id"), "permissions": installation.get("permissions", {})}
        for permission in ("contents", "pull_requests", "checks"):
            if installation.get("permissions", {}).get(permission) != "write":
                result["errors"].append(f"installed App lacks {permission}:write")
    except subprocess.CalledProcessError:
        result["errors"].append("GitHub App installation could not be verified with the current gh session")
    try:
        rulesets = _user_api(f"repos/{repo}/rulesets?per_page=100")
        canonical = [item for item in rulesets if item.get("name") == "SDLC default branch" and item.get("enforcement") == "active" and item.get("source", repo) == repo]
        if len(canonical) != 1:
            result["errors"].append("exactly one active canonical SDLC ruleset is required")
        else:
            detail = _user_api(f"repos/{repo}/rulesets/{canonical[0]['id']}")
            contexts = {check.get("context") for rule in detail.get("rules", []) if rule.get("type") == "required_status_checks" for check in rule.get("parameters", {}).get("required_status_checks", [])}
            result["required_contexts"] = sorted(contexts)
            if "sdlc / pr-fast" not in contexts:
                result["errors"].append("canonical ruleset does not require sdlc / pr-fast")
    except subprocess.CalledProcessError:
        result["errors"].append("canonical ruleset could not be read")
    try:
        repo_data = _user_api(f"repos/{repo}")
        result["default_branch"] = repo_data.get("default_branch")
        result["artifact_hook"] = "release-publish" in base64.b64decode(_user_api(f"repos/{repo}/contents/justfile?ref={repo_data['default_branch']}")["content"]).decode()
    except (KeyError, ValueError, subprocess.CalledProcessError):
        result["warnings"].append("consumer release-publish hook could not be verified")
    return result


def release_status(repo: str) -> None:
    print(json.dumps(_preflight(repo), indent=2, sort_keys=True))


def release_enable(repo: str) -> None:
    result = _preflight(repo)
    if result["errors"]:
        raise ValueError("release enable preflight failed: " + "; ".join(result["errors"]))
    _set_variable(repo, "SDLC_RELEASE_ACTIVATE", "true")
    print(f"enabled rolling release for {repo}")


def release_disable(repo: str) -> None:
    _set_variable(repo, "SDLC_RELEASE_ACTIVATE", "false")
    print(f"disabled rolling release for {repo}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="sdlc onboard")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--app-id")
    parser.add_argument("--private-key-file")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    onboard(args.repo, args.app_id, args.private_key_file, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
