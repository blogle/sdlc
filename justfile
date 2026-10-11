set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

check:
    python3 -m unittest discover -s tests -v
    python3 -m py_compile src/sdlc.py
    python3 -m py_compile actions/repository-policy/repository_policy.py
    python3 -m py_compile actions/repository-policy/policy_check.py
    python3 -m py_compile actions/repository-policy/ruleset_api.py
    python3 -m json.tool examples/minimal/renovate.json >/dev/null
    python3 -m json.tool examples/clean-room/renovate.json >/dev/null
    python3 -m json.tool examples/minimal/.github/repository-policy.json >/dev/null
    python3 -m json.tool examples/clean-room/.github/repository-policy.json >/dev/null
    python3 -m json.tool .github/repository-policy.json >/dev/null
    actionlint -ignore 'specifying action "\$/actions/repository-policy" in invalid format because ref is missing' -ignore 'reusable workflow call "\$/\.github/workflows/stage\.yml".*not following the format' .github/workflows/*.yml examples/minimal/.github/workflows/*.yml examples/clean-room/.github/workflows/*.yml
    renovate-config-validator --strict --no-global default.json
    nix run nixpkgs#yq-go -- eval '.' .mergify.yml >/dev/null
    nix run nixpkgs#yq-go -- eval '.' examples/minimal/.mergify.yml >/dev/null
    nix run nixpkgs#yq-go -- eval -e '.concurrency."cancel-in-progress" == false and (.concurrency | has("queue") | not)' .github/workflows/release-reconcile.yml >/dev/null
    nix run .#sdlc -- changelog check
    nix flake check --no-build
    nix build --no-link .#checks.x86_64-linux.test
    nix flake check --no-build ./examples/minimal
    nix run ./examples/minimal#nix-eval-jobs -- --flake ./examples/minimal#hydraJobs.x86_64-linux.ci-pr-fast --force-recurse --meta | python3 -c 'import json,sys; jobs=[json.loads(line) for line in sys.stdin if line.strip()]; assert len(jobs) == 2, jobs'
    nix run ./examples/minimal#nix-eval-jobs -- --flake ./examples/minimal#hydraJobs.x86_64-linux.ci-candidate --force-recurse --meta | python3 -c 'import json,sys; jobs=[json.loads(line) for line in sys.stdin if line.strip()]; assert len(jobs) == 2, jobs'
    just clean-room

validate-mergify:
    nix run .#mergify-cli -- config validate --config-file .mergify.yml

# Install/update shared SDLC and Mergify skills via Vercel's official CLI.
skills:
    skills add ./skills/sdlc --skill sdlc --agent opencode --yes
    skills add https://github.com/Mergifyio/mergify-cli --skill '*' --agent opencode --yes

test:
    python3 -m unittest discover -s tests -v

pr-fast:
    nix build --no-link .#checks.x86_64-linux.test

candidate:
    nix build --no-link .#checks.x86_64-linux.test

clean-room:
    #!/usr/bin/env bash
    set -euo pipefail
    ref="${SDLC_REMOTE_REF:-$(git rev-parse HEAD)}"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    cp examples/clean-room/flake.nix "$tmp/flake.nix"
    cp examples/clean-room/ci.nix "$tmp/ci.nix"
    cp examples/clean-room/justfile "$tmp/justfile"
    cp -R examples/clean-room/.github "$tmp/.github"
    nix flake lock --override-input sdlc "github:blogle/sdlc/$ref" "$tmp"
    nix flake check "$tmp"
    nix run "$tmp#nix-eval-jobs" -- --flake "$tmp#hydraJobs.x86_64-linux.ci-pr-fast" --force-recurse --meta | python3 -c 'import json,sys; jobs=[json.loads(line) for line in sys.stdin if line.strip()]; assert len(jobs) == 2, jobs'
    nix run "$tmp#nix-eval-jobs" -- --flake "$tmp#hydraJobs.x86_64-linux.ci-candidate" --force-recurse --meta | python3 -c 'import json,sys; jobs=[json.loads(line) for line in sys.stdin if line.strip()]; assert len(jobs) == 2, jobs'
    (cd "$tmp" && SDLC_RELEASE_VERSION=1.2.3 SDLC_RELEASE_MERGED_SHA=$(printf 'a%.0s' {1..40}) SDLC_RELEASE_SOURCE_SHA=$(printf 'b%.0s' {1..40}) SDLC_RELEASE_RECEIPTS=.sdlc/publication-receipts.json just --justfile "$tmp/justfile" release-publish 1.2.3)
    test "$(jq '.artifacts | length' "$tmp/.sdlc/publication-receipts.json")" = 2
    test "$(jq -r '[.artifacts[].name] | sort | join(",")' "$tmp/.sdlc/publication-receipts.json")" = anvil,sandbox
    test "$(jq -r '.artifacts[] | .version' "$tmp/.sdlc/publication-receipts.json" | sort -u)" = 1.2.3

changelog *args:
    sdlc changelog {{args}}

# Consumer publication contract. The publisher builds and records immutable
# bytes in a version/SHA-keyed GitHub Release asset; this hook only finalizes
# that exact tagged identity and never rebuilds.
release-publish version:
    test -n "{{version}}"
    test -n "${SDLC_RELEASE_LEDGER:-}"
    test -f "${SDLC_RELEASE_LEDGER}"
    test "$(jq -r .version "${SDLC_RELEASE_LEDGER}")" = "{{version}}"
    test "$(jq -r .tagged "${SDLC_RELEASE_LEDGER}")" = true
    test "$(git rev-parse HEAD)" = "$(git rev-parse "refs/tags/v{{version}}^{commit}")"
    gh release view "v{{version}}" >/dev/null
    gh release edit "v{{version}}" --draft=false
