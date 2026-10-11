"""Idempotent GitHub App and release-variable bootstrap helpers."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import subprocess
import time


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


def onboard(repo: str, app_id: str | None = None, private_key_file: str | None = None, *, dry_run: bool = False) -> None:
    current_activation = _variable(repo, "SDLC_RELEASE_ACTIVATE")
    if current_activation != "true" and not dry_run:
        _set_variable(repo, "SDLC_RELEASE_ACTIVATE", "false")
    if not app_id or not private_key_file:
        if _variable(repo, "SDLC_RELEASE_BOT_LOGIN") is None:
            raise ValueError("provide --app-id and --private-key-file to verify the App and set its bot variable")
        print(f"onboarding initialized for {repo}; activate={current_activation == 'true'}")
        return
    installation, key = _installation(repo, app_id, private_key_file)
    app_slug = installation.get("app_slug") or installation.get("app_name")
    if not isinstance(app_slug, str) or not app_slug:
        raise ValueError("GitHub did not return the installed App slug")
    bot_login = f"{app_slug}[bot]"
    if dry_run:
        print(f"verified GitHub App installation for {repo}; would set {bot_login} and activate=false")
        return
    _set_variable(repo, "SDLC_RELEASE_BOT_LOGIN", bot_login)
    subprocess.run(["gh", "secret", "set", "SDLC_RELEASE_APP_ID", "--repo", repo, "--body", app_id], check=True)
    subprocess.run(["gh", "secret", "set", "SDLC_RELEASE_APP_PRIVATE_KEY", "--repo", repo], input=key, check=True)
    print(f"verified App installation and provisioned release variables/secrets for {repo}; activate=false")


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
