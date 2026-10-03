# Consumer protocol v1

## Primary dependency

Consumers add `sdlc.url = "github:blogle/sdlc/v1"` and `sdlc.inputs.nixpkgs.follows = "nixpkgs"`. Their own `flake.lock` pins the exact implementation. The shared flake exports `lib.apiVersion = 1`, `lib.mkConsumer`, `lib.validateContract`, `lib.devTools`, and packages for `sdlc`, Mergify CLI, and Vercel `skills` (the latter two are separate tools). Workflow major and library protocol major must match.

`mkConsumer { pkgs; contract; }` validates the consumer-owned `ci.nix` attrset and returns `packages` and `apps` named `ci-pr-fast`, `ci-candidate`, `ci-release`. Targets are consumer Nix derivations; generated apps build their derivation paths with Nix. Optional commands are argv arrays executed in the consumer working directory. A v1 contract has `schemaVersion = 1`, all three stages, derivation lists for `targets`, and argv-array lists for `commands`. The release stage must have no targets: it promotes candidate-validated artifacts only. Unknown stages/schema and malformed declarations fail during flake evaluation.

The consumer flake exports `apps = ci.apps` and `packages = ci.packages`, plus the shared CLI package as `sdlc` for consumer-local changelog work. CI invokes `nix run .#ci-pr-fast` or `nix run .#ci-candidate`, so it uses the caller repository and pinned lock. A consumer-local justfile can alias those entrypoints but is not read by the shared library or workflows.

## Workflows and cache

Reusable workflows are secondary: they checkout the caller, install Nix, set up `Mic92/hestia@v3`, and invoke caller-local apps. Hestia caches Nix build results in that repository's GitHub Actions cache; it uses no Cachix name/secret, and cache misses are ordinary rebuilds. Existing blogle workflows configure `cache.nixos.org` and `nix-community.cachix.org` as substituters before Hestia. Consumer derivations and targets remain consumer-owned.

## Stages and release

`pr-fast` is cheap admission; `candidate` is expensive synthetic queue/batch validation; release runs after merge, only when fragments exist. The reusable release workflow plans a version from fragments + tags, finalizes `CHANGELOG.md`, deletes consumed `.changes/*.json`, tags/commits the finalization, then invokes `.#ci-release`. Publication commands use already-validated artifact identities. They must not run broad tests/builds or rebuild candidate outputs on main. No fragments means no changelog commit, tag, or publication.

## Mergify source sharing constraint

Mergify's documented `extends` value is a repository name (not `owner/repo` or URL), resolved within the same organization. The source repository must also have the Mergify app installed and be at least as visible as the consumer. `blogle` is a GitHub personal `User`, not an organization; the minimal example therefore carries the full local policy rather than assuming that personal-account repositories can extend it. Organization consumers can use `extends: sdlc` only when source and consumer are in the same actual Mergify organization. Other owners need a shared organization or local policy; do not guess a cross-owner syntax.

## Agent skills

`skills/sdlc/SKILL.md` is the canonical skill. Consumers use the official Vercel `skills` CLI provided by their Nix dev shell: `skills add https://github.com/blogle/sdlc/tree/v1/skills/sdlc --skill sdlc --agent opencode --yes`. It discovers a direct skill directory and installs project-local OpenCode skills. For current upstream behavior see [vercel-labs/skills](https://github.com/vercel-labs/skills). Updates use the CLI's native `skills update -p -y`; there is no SDLC-owned skill sync/copy mechanism.

## Self-hosting distinction

This repository's root `ci.nix` dogfoods `mkConsumer` against library tests. Its root `justfile` and `nix develop -c just check` are for developing this library. Downstream consumers define their own `ci.nix`, flake outputs, dev shell, and `justfile`; no consumer invokes the SDLC repository's justfile or reads its working tree.
