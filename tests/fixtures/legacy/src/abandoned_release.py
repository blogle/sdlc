"""Source-controlled exception for one exact, still-draft release identity."""
import json
import re
import subprocess


SHA = re.compile(r"[0-9a-f]{40}\Z")
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")


class RetirementError(ValueError):
    pass


def git(*args, raw=False):
    result = subprocess.run(["git", *args], check=True, capture_output=True)
    return result.stdout if raw else result.stdout.decode().strip()


def marker_path(version):
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise RetirementError("invalid retired release version")
    return f".sdlc/abandoned-releases/{version}.json"


def marker_at(main_sha, version):
    """Read from the committed main tree, never from the working directory."""
    if not isinstance(main_sha, str) or not SHA.fullmatch(main_sha):
        raise RetirementError("current main SHA is required")
    path = marker_path(version)
    result = subprocess.run(["git", "cat-file", "-e", f"{main_sha}:{path}"], capture_output=True)
    if result.returncode:
        # A missing marker is normal; an unavailable main commit is not.
        git("cat-file", "-e", f"{main_sha}^{{commit}}")
        return None
    try:
        def unique_fields(pairs):
            fields = {}
            for key, value in pairs:
                if key in fields:
                    raise RetirementError(f"duplicate field in retirement marker {path}")
                fields[key] = value
            return fields

        return json.loads(git("show", f"{main_sha}:{path}", raw=True), object_pairs_hook=unique_fields)
    except (UnicodeError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        raise RetirementError(f"invalid retirement marker {path}") from exc


def validate_marker(marker, version, source_sha, merged_sha):
    if (not isinstance(marker, dict) or set(marker) != {"schema", "version", "source_main_sha", "merged_sha", "reason"}
            or type(marker["schema"]) is not int or marker["schema"] != 1
            or marker["version"] != version or marker["source_main_sha"] != source_sha
            or marker["merged_sha"] != merged_sha
            or not isinstance(source_sha, str) or not SHA.fullmatch(source_sha)
            or not isinstance(merged_sha, str) or not SHA.fullmatch(merged_sha)
            or not isinstance(marker["reason"], str) or not marker["reason"].strip()
            or len(marker["reason"]) > 500):
        raise RetirementError(f"retirement marker for v{version} does not match the exact release identity")


def reject_abandoned(main_sha, version, source_sha, merged_sha):
    """No historical recovery or publisher may rerun a marked identity."""
    marker = marker_at(main_sha, version)
    if marker is not None:
        validate_marker(marker, version, source_sha, merged_sha)
        raise RetirementError(f"release v{version} is abandoned; refusing replay/publication")


def verify_draft_marker(main_sha, version, source_sha, merged_sha, release_state):
    marker = marker_at(main_sha, version)
    if marker is None:
        return False
    validate_marker(marker, version, source_sha, merged_sha)
    if not isinstance(release_state, dict) or release_state.get("isDraft") is not True:
        raise RetirementError(f"release v{version} is no longer a live draft")
    return True
