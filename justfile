set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

check:
    python3 -m unittest discover -s tests -v
    python3 -m py_compile src/sdlc.py
    python3 -m py_compile src/repository_policy.py
    bash -n scripts/reconcile-repository-policy.sh
    python3 -m json.tool examples/minimal/renovate.json >/dev/null
    python3 -m json.tool examples/clean-room/renovate.json >/dev/null
    python3 -m json.tool examples/minimal/.github/repository-policy.json >/dev/null
    actionlint -ignore 'unexpected key "queue" for "concurrency" section' .github/workflows/*.yml examples/minimal/.github/workflows/*.yml
    renovate-config-validator --strict --no-global default.json
    nix run nixpkgs#yq-go -- eval '.' .mergify.yml >/dev/null
    nix run nixpkgs#yq-go -- eval '.' examples/minimal/.mergify.yml >/dev/null
    nix run nixpkgs#yq-go -- eval -e '.concurrency.queue == "max" and .concurrency."cancel-in-progress" == false' .github/workflows/release.yml >/dev/null
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
    nix flake lock --override-input sdlc "github:blogle/sdlc/$ref" "$tmp"
    nix flake check "$tmp"
    nix run "$tmp#nix-eval-jobs" -- --flake "$tmp#hydraJobs.x86_64-linux.ci-pr-fast" --force-recurse --meta | python3 -c 'import json,sys; jobs=[json.loads(line) for line in sys.stdin if line.strip()]; assert len(jobs) == 2, jobs'
    nix run "$tmp#nix-eval-jobs" -- --flake "$tmp#hydraJobs.x86_64-linux.ci-candidate" --force-recurse --meta | python3 -c 'import json,sys; jobs=[json.loads(line) for line in sys.stdin if line.strip()]; assert len(jobs) == 2, jobs'

changelog *args:
    sdlc changelog {{args}}
