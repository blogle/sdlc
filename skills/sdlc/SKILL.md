---
name: sdlc
description: Use the pinned SDLC contract for shared CI, policy, and rolling releases.
---

# Shared SDLC contract

The consumer's `flake.lock` selects SDLC. The consumer owns only its Nix
`checks.<system>.sdlc-fast`, `checks.<system>.sdlc-verify`, and
`apps.<system>.sdlc-publish` outputs. Shared workflows own CI stages, runners,
Nix setup, caching, rulesets, Mergify policy, and release orchestration.

## Development and CI

- Use `nix develop` and the consumer's `justfile` for local work.
- Fast checks gate admission to Mergify. Full verification runs on Mergify's
  synthetic candidate and gates queue merge.
- Use the generated SDLC workflow callers and Mergify snapshot. After changing
  the SDLC pin, run `nix run .#sdlc -- install` and commit regenerated adapters
  with `flake.lock`. `nix run .#sdlc -- install --check` verifies them.
- Keep public check names stable: `sdlc / check`, `sdlc / verify`,
  `sdlc / policy`, and `sdlc / release-gate`.
- Mergify queues only after `sdlc / check` and integration authorization. It
  requires `sdlc / verify` on the candidate before squash merge. `release`
  labeled PRs are excluded from Mergify.

## Changes and releases

- Add a unique `.changes/<topic>.json` fragment only for externally meaningful
  changes. Include `type`, `semver` (`patch`, `minor`, or `major`), and `summary`.
- The updater coalesces outstanding fragments into one replaceable
  `sdlc/release-next` PR and applies the `release` label. Do not edit its
  generated changelog or manifest by hand.
- The release gate validates the exact base/head test-merge commit. GitHub's
  required check prevents a base advance from reusing an earlier gate result.
- Publication uses the actual squash-merged release SHA. Consumer hooks must
  verify immutable registry digests on retry and write receipts to
  `$SDLC_RELEASE_RECEIPTS`.
- Never add a ruleset bypass for the release App. GitHub's protected merge
  enforces the required checks.

## Validate

Before handing off, run `nix develop -c just check`, the consumer's fast stage,
and its candidate-equivalent checks when practical. Validate changed workflow
YAML with `actionlint`. Report exactly which checks ran and any hosted GitHub
steps still needed for the canary.
