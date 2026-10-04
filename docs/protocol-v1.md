# Consumer protocol v1

## Primary dependency

Consumers pin `sdlc.url = "github:blogle/sdlc/v1"` and set `sdlc.inputs.nixpkgs.follows = "nixpkgs"`. The consumer's `flake.lock` pins the exact implementation. The shared flake exports `lib.apiVersion = 1`, `lib.validateContract`, `lib.mkConsumer`, `lib.devTools`, and packages for the changelog CLI, Mergify CLI, and Vercel `skills` CLI.

The consumer-owned `ci.nix` is a Nix attrset with `schemaVersion = 1` and exactly two fields under `stages`: `pr-fast` and `candidate`. Each maps directly to one consumer-owned Nix derivation. `mkConsumer { contract; }` validates the names/derivation values and aliases those same derivations to `ci-pr-fast` and `ci-candidate` in `checks`. It does not create an app wrapper, invoke `nix`, or define command syntax. Consumers aggregate multiple checks using ordinary Nix derivations. Release is publication workflow semantics, not a Nix build target.

The consumer merges `ci.checks` into `checks.${system}`; CI invokes `nix build .#checks.x86_64-linux.ci-pr-fast` or `...ci-candidate`. This uses consumer flake outputs and the implementation revision in its lockfile. Reusable workflow major and SDLC library protocol major must match. The consumer's justfile is its own developer interface, not read by shared SDLC tooling except the explicitly defined `release-publish <version>` hook.

## Hestia and scheduling

Reusable workflows checkout the caller, install Nix, set up `Mic92/hestia@v3`, and build the caller's Nix check. Hestia owns cache state and cache transfer using that repository's GitHub Actions cache. Shared SDLC adds no cache names, cache protocol, grouping, or matrix scheduler. Consumers can use normal Nix flake checks and Hestia's native matrix action if their own checks warrant fan-out. Existing blogle workflows configure `cache.nixos.org` and `nix-community.cachix.org` as substituters before Hestia.

## Release and changelog

The release workflow runs on consumer `main` pushes. It plans a version from pending fragment SemVer intents and existing tags; no fragments means no release. It finalizes the consumer's `CHANGELOG.md`, deletes consumed `.changes/*.json`, commits the result and tag, then calls the consumer-owned `just release-publish <version>` recipe inside `nix develop`. That recipe publishes/promotes already-validated artifacts; it must not run CI or rebuild them. A workflow-dispatch retry can publish an existing tag without finalizing a second time. Custom SDLC code is limited to fragment validation, aggregate bump intent, deterministic rendering, and compaction.

## Mergify source sharing constraint

Mergify's documented `extends` value is a repository name (not `owner/repo` or URL), resolved within the same organization. The source also needs the Mergify app installed and must be at least as visible as the consumer. GitHub identifies `blogle` as a personal `User`, not an organization; the minimal fixture carries its policy locally rather than assuming personal-account repositories can extend it. Organization consumers can use `extends: sdlc` only when both repositories are in the same actual Mergify organization.

## Skills

`skills/sdlc/SKILL.md` is the canonical skill. Consumers use the official Vercel `skills` CLI from their pinned Nix dev shell, targeting OpenCode. Updates use native `skills update -p -y`; SDLC owns skill content, not installation/sync code.

## Self-hosting distinction

This repository's root `ci.nix` aliases library tests through `mkConsumer`. Its root `justfile` and `nix develop -c just check` develop/test the shared library. Downstream consumers define their own `ci.nix`, flake outputs, dev shell, and justfile; no consumer invokes the SDLC repository's justfile or reads its working tree.
