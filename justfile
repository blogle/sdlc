set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

check:
    nix develop -c go test ./...
    nix develop -c go vet ./...
    nix develop -c golangci-lint run --enable-only exhaustive ./...
    test -z "$(nix develop -c gofmt -l cmd/sdlc internal)"
    python3 -m unittest discover -s tests -v
    python3 -m py_compile tests/fixtures/legacy/src/sdlc.py
    python3 -m py_compile tests/fixtures/legacy/src/release_gate.py tests/fixtures/legacy/src/release_publisher.py tests/fixtures/legacy/src/release_reconcile.py
    bash -n scripts/sdlc-release-publish.sh
    python3 -m json.tool rulesets/default-branch.json >/dev/null
    actionlint .github/workflows/*.yml
    renovate-config-validator --strict --no-global default.json
    nix run nixpkgs#yq-go -- eval '.' .mergify.yml >/dev/null
    nix run nixpkgs#yq-go -- eval -e '.concurrency."cancel-in-progress" == false and (.concurrency | has("queue") | not)' .github/workflows/release-reconcile.yml >/dev/null
    nix run .#sdlc -- changelog check
    nix-instantiate --eval --strict tests/consumer-contract.nix
    nix eval --json .#ci.x86_64-linux --apply builtins.attrNames | jq -e '. == ["check", "publish", "verify"]' >/dev/null
    nix flake check --no-build
    nix eval .#lib.skills --apply builtins.attrNames --json | python3 -c 'import json,sys; names=json.load(sys.stdin); assert {"discoverCatalog", "selectSkills", "mkBundle", "mkLocalInstallProgram"} <= set(names), names'
    nix build --no-link .#checks.x86_64-linux.test
    nix build --no-link .#checks.x86_64-linux.skills-install

validate-mergify:
    nix run .#mergify-cli -- config validate --config-file .mergify.yml

# Install/update shared SDLC and Mergify skills via Vercel's official CLI.
skills:
    skills add ./skills/sdlc --skill sdlc --agent opencode --yes
    skills add https://github.com/Mergifyio/mergify-cli --skill '*' --agent opencode --yes

test:
    python3 -m unittest discover -s tests -v

ci-check:
    nix build --no-link .#ci.x86_64-linux.check.syntax .#ci.x86_64-linux.check.config

ci-verify:
    nix build --no-link .#ci.x86_64-linux.verify.tests .#ci.x86_64-linux.verify.skills-install

changelog *args:
    sdlc changelog {{args}}
