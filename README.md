# blogle/sdlc

`blogle/sdlc` is a shared **library/tooling dependency**. Consumer repositories pin it as a Nix flake input; they own their flake, Nix targets, `justfile`, CI contract, and release hooks. A consumer never checks out this repository or reads its files at runtime.

## Consumer API v1

Add the input and follow the consumer's Nixpkgs pin:

```nix
inputs = {
  nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  sdlc.url = "github:blogle/sdlc/v1";
  sdlc.inputs.nixpkgs.follows = "nixpkgs";
};
```

Run `nix flake lock` and commit the resulting consumer `flake.lock`; it fixes the precise compatible implementation and dependency revisions. Upgrade deliberately with `nix flake lock --update-input sdlc`. The reusable workflow major (`@v1`) must match the input's protocol major. Workflow jobs checkout the caller and build its local Nix checks, which are direct aliases created from the locked input—there is no separate remote runner fetch.

Expose targets from the consumer's own flake and use the SDLC helper to alias the two standard stages into native Nix check outputs:

```nix
outputs = { self, nixpkgs, sdlc, ... }:
  let
    systems = [ "x86_64-linux" ];
    forAllSystems = nixpkgs.lib.genAttrs systems;
  in {
    checks = forAllSystems (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        contract = import ./ci.nix { inherit self system; };
        ci = sdlc.lib.mkConsumer { inherit contract; };
      in ci.checks // {
        fast = pkgs.runCommand "consumer-fast" { } ''run-fast-checks; touch $out'';
        candidate = pkgs.runCommand "consumer-candidate" { } ''run-candidate-checks; touch $out'';
      });
    packages = forAllSystems (system:
      { sdlc = sdlc.packages.${system}.sdlc; });
    apps = forAllSystems (system: {
      sdlc = { type = "app"; program = "${sdlc.packages.${system}.sdlc}/bin/sdlc"; };
    });
    devShells = forAllSystems (system:
      let pkgs = nixpkgs.legacyPackages.${system};
      in {
        default = pkgs.mkShell {
          packages = sdlc.lib.devTools {
            inherit pkgs;
            sdlcCli = sdlc.packages.${system}.sdlc;
          } ++ [ pkgs.just ];
        };
      });
  };
```

`ci.nix` is consumer-owned Nix data, not a second DSL:

```nix
{ self, system }:
{
  schemaVersion = 1;
  stages = {
    pr-fast = self.checks.${system}.fast;
    candidate = self.checks.${system}.candidate;
  };
}
```

The tiny v1 helper validates stage names and aliases the same consumer derivations under stable check names. It creates no shell wrappers, Nix sub-runner, or task DSL. CI invokes `nix build --no-link .#checks.x86_64-linux.ci-pr-fast` / `nix build --no-link .#checks.x86_64-linux.ci-candidate`; projects with multiple checks aggregate them in their own flake using Nix. Release is not a Nix build stage: the shared publication workflow finalizes changelogs and delegates publication to the consumer's own `just release-publish <version>` recipe. `sdlc.lib.devTools` adds the shared changelog CLI and official `skills` CLI; Mergify CLI is independently exposed as a flake package.

See [`examples/minimal`](examples/minimal) for a runnable consumer fixture. Its `path:../..` input exists only so this repository can integration-test the library without depending on a released tag; actual consumers use `github:blogle/sdlc/v1` as shown above.

## Consumer-owned commands and workflows

The consumer owns its `justfile`. For example:

```make
ci-fast:
    nix build --no-link .#checks.x86_64-linux.ci-pr-fast

ci-candidate:
    nix build --no-link .#checks.x86_64-linux.ci-candidate

skills:
    skills add https://github.com/blogle/sdlc/tree/v1/skills/sdlc --skill sdlc --agent opencode --yes
    skills add https://github.com/Mergifyio/mergify-cli --skill '*' --agent opencode --yes

skills-update:
    skills update -p -y
```

Consumers call the platform workflows from their own workflows:

```yaml
jobs:
  pr-fast:
    uses: blogle/sdlc/.github/workflows/pr-fast.yml@v1
  candidate:
    if: github.event_name == 'merge_group' || startsWith(github.event.pull_request.head.ref, 'mergify/merge-queue/')
    uses: blogle/sdlc/.github/workflows/candidate.yml@v1
```

The shared jobs check out the caller, install Nix, configure the proven Hestia action, then build the caller's Nix-native stage check. Nix/Hestia/cache setup is centralized; derivations and targets remain consumer-owned. Hestia uses GitHub Actions cache scoped by GitHub, requires no cache name, Cachix account, or cache secret, and can evict entries (which simply causes rebuilds). The workflow also configures the Nix Community substituter used in blogle's existing CI pattern.

Do not run general CI on merged `main`. The consumer invokes the reusable release workflow only on a main push:

```yaml
on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      publish_version:
        description: Existing version to republish after a failed publication
        type: string
        required: false
jobs:
  publish:
    permissions:
      contents: write
    uses: blogle/sdlc/.github/workflows/release.yml@v1
    secrets: inherit
```

The release workflow plans from pending fragments, no-ops if none exist, compacts fragments into `CHANGELOG.md`, commits that finalization and tags the computed version, then invokes the consumer-owned `just release-publish <version>` recipe inside `nix develop`. The minimal example creates a GitHub Release for the tag using GitHub-generated notes; repositories with external artifacts can extend that local recipe to promote candidate-validated artifact identities. It must not rerun CI or rebuild. If publication fails after finalization/tagging, rerun the consumer's release workflow manually with the existing `publish_version` (without the leading `v`); it verifies the tag and retries publication without consuming/finalizing fragments again. Install a GitHub App with contents-write permission, configure it as a branch-ruleset bypass actor for the finalizer commit/tag, and store `SDLC_RELEASE_APP_ID` and `SDLC_RELEASE_APP_PRIVATE_KEY` as repository or organization Actions secrets. The release workflow requires those secrets; they are not needed for PR admission/candidate or Hestia. The example release caller uses `secrets: inherit` so those named secrets reach the reusable workflow.

## Mergify sharing and integration

Mergify's documented `extends` syntax accepts a **repository name only**, looked up in the same organization; it does not accept `owner/repo`, arbitrary URLs, or a filesystem path. Mergify must be installed on both source and consumer repositories. GitHub identifies `blogle` as a personal `User`, not an organization. The complete consumer fixture therefore uses a local policy rather than assuming `extends: sdlc` works for this account. When the shared repository is hosted in the same Mergify organization as a consumer, its `.mergify.yml` can be as small as:

```yaml
extends: sdlc
```

For other owners, central sharing needs an organization that contains both source and consumers; otherwise keep the policy local. Do not pretend `extends: blogle/sdlc` or arbitrary URL syntax is valid.

The canonical policy auto-enqueues after `sdlc / pr-fast`, requires candidate validation at merge, batches synthetic candidates, and squash-merges each PR for linear one-commit-per-PR history. Batching and stacks are independent. Contributors need no `@mergifyio queue` comment and source PRs need not be rebased to zero commits behind main. Enable Mergify Merge Queue and Merge Protections, permit squash in repository settings, and require the admission check—not expensive candidate checks—on ordinary PRs.

One-time repository setup: install/authorize the Mergify GitHub App for the repository; enable its Merge Queue and Merge Protections products; allow squash and disable merge-commit/rebase merge methods; require `sdlc / pr-fast` on ordinary pull requests; and create the two integration labels below. Do not require `sdlc / candidate` on source PRs—the queue candidate is where that gate applies. The example CI and release YAML files show the only workflows needed; no Cachix secret or Hestia cache name is configured.

Every PR selects exactly one `integration:auto` or `integration:review` label; create these labels in the consuming repository. Auto means the worker has delegated integration authority. Review means stop after implementation and wait for a successful `sdlc-integration-authorized` check bound to the exact head SHA; any head change invalidates it. Missing, unknown, or conflicting labels fail closed. A trusted GitHub App/workflow must produce that check; comments are not an authorization protocol and agents must not upgrade their own authority. This bootstrap defines the check contract, not the authorization service. Enable Mergify Merge Queue and Merge Protections, configure squash as the only allowed merge method, require `sdlc / pr-fast` for normal PR admission, and do not require candidate checks on source PRs. Hestia needs no secret; the head-attestation producer requires its own narrowly scoped credential if it reports the check through a GitHub App.

## Skills

The canonical SDLC skill lives at `skills/sdlc/SKILL.md` in this repository and has standard Agent Skills frontmatter. The consumer dev shell provides the `skills` 1.7.0 CLI from the pinned Nixpkgs revision. Consumer-local `just skills` invokes only that official CLI, non-interactively, targeting OpenCode; its SDLC source is the direct compatible `v1` GitHub tree URL. This skill source follows the moving `v1` channel independently of `flake.lock`; `skills update -p -y` uses the CLI's native source metadata. Mergify's skills can be installed with the same CLI from `Mergifyio/mergify-cli`. Mergify CLI binary packaging (`sdlc.packages.<system>.mergify-cli`) is independent of skill distribution.

## Changelogs and local agent loop

Add `.changes/<topic>.json` only for externally meaningful release-worthy changes, with `type`, `semver` (`patch|minor|major`), and `summary`. CI/docs/tests/internal refactors normally need no fragment. A PR never edits `CHANGELOG.md` to add notes. `sdlc changelog check|plan|finalize` operates on the consumer working tree; plan aggregates maximum SemVer intent and finds the next version from tags (`--json` is consumed by GitHub Actions), finalize renders a dated version section and deletes consumed fragments. No fragments means no release. Git history archives fragment contents.

Example fragment:

```json
{"type":"feature","semver":"minor","summary":"Add project-facing capability"}
```

Agent loop in a consumer: `nix develop`; inspect the consumer `ci.nix` and `just --list`; implement targets locally; run `just check` and `just ci-fast` (or `nix build --no-link .#checks.x86_64-linux.ci-pr-fast`); add a fragment only if warranted; open a PR with exact command/result evidence and the assigned integration policy. `nix develop` is strongly preferred; never recommend `nix --option build-users-group "" develop`. The shared repo's own `justfile` and tests develop this library only—they are not part of the downstream interface.
