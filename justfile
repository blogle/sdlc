set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

check:
    python3 -m unittest discover -s tests -v
    python3 -m py_compile src/sdlc.py
    actionlint .github/workflows/*.yml

validate-mergify:
    nix run .#mergify-cli -- config validate --config-file .mergify.yml

# Install/update shared SDLC and Mergify skills via Vercel's official CLI.
skills:
    skills add https://github.com/blogle/sdlc/tree/v1/skills/sdlc --skill sdlc --agent opencode --yes
    skills add https://github.com/Mergifyio/mergify-cli --skill '*' --agent opencode --yes

test:
    python3 -m unittest discover -s tests -v

pr-fast:
    sdlc run pr-fast

candidate:
    sdlc run candidate

changelog *args:
    sdlc changelog {{args}}
