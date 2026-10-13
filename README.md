# blogle/sdlc

SDLC is the versioned delivery contract for the Nix repository fleet. Each
consumer's `flake.lock` selects one immutable SDLC revision; generated workflow
references use that same revision.

## Consumer contract

Consumers expose three application-owned Nix outputs:

```nix
checks.${system}.sdlc-fast = ...;
checks.${system}.sdlc-verify = ...;
apps.${system}.sdlc-publish = ...;
packages.${system}.sdlc = sdlc.packages.${system}.sdlc;
```

`sdlc install` generates thin CI and release callers, a Mergify policy snapshot,
and a Renovate preset reference. `sdlc install --check` verifies those files
against the revision in `flake.lock`. Update SDLC by updating the flake input and
regenerating adapters in the same PR.

The CI workflow runs the fast derivation on pull requests, full verification on
Mergify candidates, and adapter validation. Mergify requires fast checks before
queue admission and full verification before merge. SDLC's canonical ruleset
requires `sdlc / check`, `sdlc / policy`, and `sdlc / release-gate`; status
freshness remains non-strict so Mergify owns candidate freshness.

The generated rolling release PR carries the `release` label. Mergify excludes
it; the SDLC release workflow validates the generated candidate and merges it
through ordinary branch protection. Its required release-gate check is written
to GitHub's test-merge commit, binding the check to the exact current base and
PR head. A base advance produces a different test-merge commit and cannot reuse
the old gate result. Publication runs after merge from the actual squash SHA and
calls the consumer's `sdlc-publish` app.

Add JSON release fragments under `.changes/`:

```json
{
  "type": "fix",
  "semver": "patch",
  "summary": "Correct session recovery after worker restart"
}
```

The shared generator sorts fragments, selects the strongest requested SemVer
bump, writes the changelog and release manifest, and consumes the fragments.
No fragments means no release PR.

## CLI

```sh
nix run .#sdlc -- install [--check]
nix run .#sdlc -- ruleset plan --repo OWNER/REPO
nix run .#sdlc -- ruleset apply --repo OWNER/REPO
nix run .#sdlc -- ruleset check --repo OWNER/REPO
nix run .#sdlc -- enable --repo OWNER/REPO [--dry-run]
nix run .#sdlc -- changelog generate --version 1.4.0
```

The ruleset is defined only by `rulesets/default-branch.json`. The pinned CLI
applies and verifies that payload; it does not read consumer policy files.
`enable` is only a convenience wrapper for ruleset apply/check. GitHub Apps and
credentials are managed by the fleet controller, not this CLI.

Run `nix develop -c just check` for SDLC's unit tests, Nix checks, workflow
validation, and policy syntax checks.
