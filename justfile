set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

check:
    python3 -m unittest discover -s tests -v
    python3 -m py_compile src/sdlc.py
    actionlint .github/workflows/*.yml

validate-mergify:
    nix run .#mergify-cli -- config validate --config-file .mergify.yml

test:
    python3 -m unittest discover -s tests -v

pr-fast:
    sdlc run pr-fast

candidate:
    sdlc run candidate

changelog *args:
    sdlc changelog {{args}}
