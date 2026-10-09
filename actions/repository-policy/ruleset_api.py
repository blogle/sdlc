"""GitHub ruleset REST reads shared by the local CLI and reusable action."""

import json
import subprocess
from urllib.parse import urljoin
from urllib.request import Request, urlopen


API_ROOT = "https://api.github.com"
RULESET_NAME = "SDLC default branch"


def _gh_json(path, paginate=False):
    command = ["gh", "api"]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    command.append(path)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _anonymous_json(url):
    request = Request(url, headers={"Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=30) as response:
        return json.load(response), response.headers.get("Link", "")


def _anonymous_pages(url):
    records = []
    seen = set()
    while url:
        if url in seen:
            raise ValueError("GitHub ruleset pagination repeated a page URL")
        seen.add(url)
        if len(seen) > 100:
            raise ValueError("GitHub ruleset pagination exceeded 100 pages")
        page, links = _anonymous_json(url)
        if not isinstance(page, list):
            raise ValueError("GitHub ruleset list response was not an array")
        records.extend(page)
        next_url = None
        for link in links.split(","):
            if 'rel="next"' in link:
                next_url = urljoin(url, link[link.find("<") + 1:link.find(">")])
                break
        url = next_url
    return records


def _read_pages(repository, include_parents, anonymous_fallback):
    include = "true" if include_parents else "false"
    path = f"repos/{repository}/rulesets?includes_parents={include}&per_page=100"
    try:
        pages = _gh_json(path, paginate=True)
        if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
            raise ValueError("gh api --paginate --slurp returned an invalid ruleset page set")
        return [item for page in pages for item in page], False
    except subprocess.CalledProcessError:
        if not anonymous_fallback:
            raise
        return _anonymous_pages(f"{API_ROOT}/{path}"), True


def read_rulesets(repository, include_parents=True, anonymous_fallback=False):
    """List rulesets, then replace canonical summaries with full detail records.

    List responses are discovery-only: GitHub omits ``rules`` and ``conditions``
    there. Any failed detail read raises; it is never interpreted as an empty set.
    """
    records, used_anonymous = _read_pages(repository, include_parents, anonymous_fallback)
    for index, summary in enumerate(records):
        if summary.get("name") != RULESET_NAME:
            continue
        ruleset_id = summary.get("id")
        if ruleset_id is None:
            raise ValueError("canonical ruleset list entry has no id")
        path = f"repos/{repository}/rulesets/{ruleset_id}"
        try:
            detail = _gh_json(path)
        except subprocess.CalledProcessError:
            if not anonymous_fallback:
                raise
            detail, _ = _anonymous_json(f"{API_ROOT}/{path}")
            used_anonymous = True
        if not isinstance(detail, dict) or "rules" not in detail or "conditions" not in detail:
            raise ValueError(f"GitHub returned an incomplete detail response for canonical ruleset id {ruleset_id}")
        records[index] = detail
    return records, used_anonymous
