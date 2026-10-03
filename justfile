set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

check:
    python3 -m unittest discover -s tests -v
    python3 -m py_compile src/sdlc.py
    actionlint .github/workflows/*.yml examples/minimal/.github/workflows/*.yml
    nix run nixpkgs#yq-go -- eval '.' .mergify.yml >/dev/null
    nix run nixpkgs#yq-go -- eval '.' examples/minimal/.mergify.yml >/dev/null
    nix flake check --no-build
    nix run .#ci-pr-fast
    nix run .#ci-candidate
    nix flake check --no-build ./examples/minimal
    nix run ./examples/minimal#ci-pr-fast
    nix run ./examples/minimal#ci-candidate

validate-mergify:
    nix run .#mergify-cli -- config validate --config-file .mergify.yml

# Install/update shared SDLC and Mergify skills via Vercel's official CLI.
skills:
    skills add ./skills/sdlc --skill sdlc --agent opencode --yes
    skills add https://github.com/Mergifyio/mergify-cli --skill '*' --agent opencode --yes

test:
    python3 -m unittest discover -s tests -v

pr-fast:
    nix run .#ci-pr-fast

candidate:
    nix run .#ci-candidate

changelog *args:
    sdlc changelog {{args}}
