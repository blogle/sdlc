# blogle/sdlc

`blogle/sdlc` is a pinned Nix library and shared GitHub workflow source. Consumers own their Nix checks/packages, `ci.nix`, `justfile`, candidate artifact publication, and final artifact promotion. The shared repo does not run a separate build DSL or read consumer files from its checkout.

## Pin the shared flake

SDLC owns the default fleet Nixpkgs pin. A consumer normally follows that input:

```nix
inputs = {
  sdlc.url = "github:blogle/sdlc/v1.0.0";
  nixpkgs.follows = "sdlc/nixpkgs";

  # Other project inputs follow the consumer's top-level Nixpkgs node.
  crane.url = "github:ipetkov/crane";
  crane.inputs.nixpkgs.follows = "nixpkgs";
};
```

Run `nix flake lock` and commit the consumer lock. A later SDLC/Nixpkgs rollout is adopted only when that repository deliberately updates its lock, e.g. `nix flake lock --update-input sdlc`. Sharing the same Nixpkgs revision yields identical derivation inputs/store paths and improves local cross-repo reuse and potential future shared-cache hits. It does **not** make Hestia cross-repository or durable. A repository that truly requires its own Nixpkgs revision can set `nixpkgs.url = ...` and `sdlc.inputs.nixpkgs.follows = "nixpkgs"`; other inputs still follow the consumer's top-level `nixpkgs`.

The copy-pastable consumer input and `flake.lock` use a concrete SDLC release tag. Reusable SDLC workflows use the same explicit `@v1.0.0` release. After this bootstrap is reviewed/merged, maintainers must publish the initial `v1.0.0` tag and establish the moving `v1` preset ref at that release; advance `v1` when compatible preset policy changes land. Later compatible runtime releases use `v1.x.y`, with breaking protocol changes on v2. Renovate groups the Nix input update with all reusable SDLC workflow ref updates. The shared Renovate preset itself remains `github>blogle/sdlc#v1`, a floating compatible policy channel; preset breaking changes require a new major.

## Nix contract and local commands

Consumers declare Nix derivations in their own `ci.nix`, with only the two check stages:

```nix
{ self, system }:
{
  schemaVersion = 1;
  stages = {
    pr-fast = {
      format = self.checks.${system}.format;
      unit = self.checks.${system}.unit;
    };
    candidate = {
      integration = self.checks.${system}.integration;
      package = self.checks.${system}.package;
    };
  };
}
```

The pinned SDLC library validates this small v1 attrset and exposes those same derivations under `hydraJobs.<system>.ci-pr-fast` and `hydraJobs.<system>.ci-candidate`. It preserves each derivation and its `meta.hestia.*` metadata unchanged. There are no generated shell runner apps, target-string DSL, worker matrix, or cache abstraction. Project checks remain ordinary Nix flake outputs; local just recipes build their own targets directly:

```make
ci-fast:
    nix build --no-link .#checks.x86_64-linux.format .#checks.x86_64-linux.unit

ci-candidate:
    nix build --no-link .#checks.x86_64-linux.integration .#checks.x86_64-linux.package
```

The consumer exposes the helper's Hestia-native job groups from its flake:

```nix
packages = forAllSystems (system:
  let pkgs = nixpkgs.legacyPackages.${system};
  in {
    sdlc = sdlc.packages.${system}.sdlc;
    nix-eval-jobs = pkgs.nix-eval-jobs;
  });

hydraJobs = forAllSystems (system:
  (sdlc.lib.mkConsumer {
    contract = import ./ci.nix { inherit self system; };
  }).hydraJobs);

packages = forAllSystems (system:
  let pkgs = nixpkgs.legacyPackages.${system};
  in {
    sdlc = sdlc.packages.${system}.sdlc;
    nix-eval-jobs = pkgs.nix-eval-jobs;
  });

devShells = forAllSystems (system:
  let pkgs = nixpkgs.legacyPackages.${system};
  in {
    default = pkgs.mkShell {
      packages = sdlc.lib.devTools {
        inherit pkgs;
        sdlcCli = sdlc.packages.${system}.sdlc;
      } ++ [ pkgs.just pkgs.gh ];
    };
  });
```

`examples/minimal` is the in-repository fixture; `examples/clean-room` has no local SDLC path dependency and is copied to a temporary directory for testing against the pushed remote SDLC commit. The consumer's own justfile is its human/agent interface. SDLC's root justfile is only for developing this library.

## GitHub Actions and Hestia

The consumer calls the platform reusable workflows at the same release tag as its SDLC flake input:

```yaml
jobs:
  pr-fast:
    uses: blogle/sdlc/.github/workflows/pr-fast.yml@v1.0.0
  candidate:
    if: github.event_name == 'merge_group' || startsWith(github.event.pull_request.head.ref, 'mergify/merge-queue/')
    uses: blogle/sdlc/.github/workflows/candidate.yml@v1.0.0
  # Stable required-check contexts must be emitted by ordinary caller jobs.
  sdlc-pr-fast:
    name: sdlc / pr-fast
    needs: pr-fast
    if: always()
    runs-on: ubuntu-latest
    steps:
      - run: test "${{ needs.pr-fast.result }}" = success
  sdlc-candidate:
    name: sdlc / candidate
    needs: candidate
    if: always() && (github.event_name == 'merge_group' || startsWith(github.event.pull_request.head.ref, 'mergify/merge-queue/'))
    runs-on: ubuntu-latest
    steps:
      - run: test "${{ needs.candidate.result }}" = success
```

Nested reusable-workflow check names include caller/callee prefixes and are not stable required-check contexts. The two ordinary local jobs above always run and succeed only when the corresponding reusable job succeeds. Keep the candidate gate condition identical to the reusable candidate job so it runs for merge groups and Mergify synthetic PRs only. Canonical Mergify continues to require `sdlc / pr-fast` and `sdlc / candidate`.

The reusable workflows checkout the caller and invoke **Hestia's native matrix action** on that consumer's `hydraJobs` stage attrset. Hestia evaluates with the consumer's lockfile-pinned `nix-eval-jobs`, honors native `meta.hestia.group`/`meta.hestia.os`, fans out derivation builds, and owns the GitHub Actions cache. SDLC adds no matrix schema or scheduler. Hestia cache entries are repository-scoped and evictable; a miss only costs performance. It is not a durable artifact store, not a candidate provenance mechanism, and does not promise Nix outputs will be reusable by another repo or developer machine. No cache name or cache secret is used. External Nix binary caches are an optional future optimization and are not a v1 protocol interface.

Future cache research is explicitly outside v1: native Nix S3 caches against custom S3-compatible endpoints (including comparing free-tier providers), niks3's server/DB/GC tradeoffs, and experimental OCI/GHCR-backed caches. No backend abstraction or cost/free-tier promise is part of this protocol.

## Lifecycle and Mergify

`pr-fast` is cheap admission; `candidate` is expensive synthetic Mergify batch validation. Mergify queue batching only optimizes candidate validation: a batch does not imply a release or one combined release note. Each squash-merged PR that added one or more valid fragments may produce **one release for that PR**; an individual merged PR with no fragments produces no release. Squash merge gives each PR one logical main commit. Stacks are dependency/order relationships, batches are candidate grouping, and they remain orthogonal. Normal flow auto-enqueues eligible work; no `@mergifyio queue` comment or zero-commits-behind-main rebase treadmill is required.

The canonical `.mergify.yml` uses automatic enqueue, candidate checks at merge, batching, squash, and fail-closed integration policy. Organization-owned consumers in the same actual Mergify organization can use `extends: sdlc`. Mergify's documented `extends` syntax accepts a repository name resolved in that organization; it does not accept `owner/repo` or arbitrary URLs. `blogle` is a personal GitHub User, so the fixture uses the full local policy instead of assuming personal-account extension works. Cross-owner consumers must host the preset in a shared organization or retain local policy.

Create exactly one of `integration:auto` or `integration:review` on each PR. Auto delegates integration authority to the worker. Review workers stop after implementation and wait for native GitHub approval before queue eligibility. For a head-bound approval, configure GitHub to dismiss stale approvals and require approval of the most recent reviewable push; Mergify's `#approved-reviews-by >= 1` condition gates the review mode. Missing/unknown/conflicting labels fail closed; comments are not authorization. Install the Mergify GitHub App, enable Merge Queue and Merge Protections, allow squash only, require `sdlc / pr-fast`, and create both labels. Do not require candidate status on source PRs.

## Repository policy as IaC

SDLC owns the canonical policy renderer and REST reconciler as the self-contained composite action at `actions/repository-policy`, called from `.github/workflows/reconcile-policy.yml`. Each consumer keeps only a small `.github/repository-policy.json` declaration for optional `default_branch` and `extra_required_status_checks`, plus a tiny workflow call pinned to a concrete SDLC release tag. The reusable workflow invokes the composite action with `uses: $/actions/repository-policy`; GitHub resolves it from the same SDLC repository and exact ref/commit as the reusable workflow, so implementation and workflow are atomically versioned. GitHub context supplies the owner, repository name, and—unless explicitly overridden—the default branch. Policy changes are reviewed beside the repository they govern.

Pull requests validate and render the desired ruleset without writing repository settings. A push to the default branch or explicit `workflow_dispatch` performs stateless reconciliation: fetch rulesets, find the canonical `SDLC default branch` ruleset, create it if absent or fully `PUT` the desired representation if present, then upsert both integration labels. The desired payload requires squash-only merges, `sdlc / pr-fast` plus configured checks, stale-review dismissal, and deletion/force-push protections; candidate is not required on source PRs. GitHub REST ruleset `PUT` is replacement-style, so the renderer sends the complete desired ruleset. There is no database, state, backend, cache, or custom controller. GitHub CLI's `gh ruleset` commands are currently read-only, so writes use `gh api` against GitHub's documented repository ruleset and label REST endpoints.

Apply mints a repository-scoped token from a dedicated GitHub App with Administration write (and Issues write for label upserts); do not assume `GITHUB_TOKEN` can edit rulesets. Configure `SDLC_POLICY_APP_ID` and `SDLC_POLICY_APP_PRIVATE_KEY` as Actions secrets, and grant the App only the repository permissions and installation scope it needs. Nexus can trigger the consumer workflow's `workflow_dispatch` for explicit reconciliation. Its current GitHub connector exposes ruleset read/create but not update/delete, so the shared workflow's API reconciliation path is used for convergence. The minimal example at `examples/minimal/.github/repository-policy.json` and `.github/workflows/reconcile-policy.yml` demonstrates consumer wiring.

## Release and artifacts

The consumer invokes the reusable release workflow on main pushes only; do not rerun general CI on merged main. It uses native GitHub Actions concurrency (`queue: max`, `cancel-in-progress: false`) to serialize releases, and processes first-parent merge commits individually. For each PR commit it selects only that commit's changed `.changes/*.json` files, replans against current tags/main, finalizes those fragments, and atomically pushes the CHANGELOG commit and version tag. Stale/mutated fragments fail closed. If main/tag advances during the atomic push, neither remote ref changes; the fragments remain pending and retry replans against current main/tags. Tag annotations bind the source commit so reruns can resume publication. A workflow-dispatch with no `publish_version` recovers remaining pending fragments per introducing commit if GitHub's bounded 100-run concurrency queue fills. A dispatch with an existing `publish_version` retries publication for an already-finalized release.

The release GitHub App needs contents-write and permission to update the protected main/tag refs (configure an organization ruleset bypass actor where supported). Store `SDLC_RELEASE_APP_ID` and `SDLC_RELEASE_APP_PRIVATE_KEY` as Actions secrets. GitHub ruleset bypass actors are not available for personal-account repos; there the branch policy must permit the release writer or release finalization is unsupported until the repo moves to an organization / a supported writer is provisioned. The fixture calls a consumer-owned `just release-publish <version>` recipe that creates a GitHub Release; replace/extend it for project registries.

For a deployable OCI image/package, candidate validation should publish an immutable identity (for example an OCI digest) to the project's existing registry. Record/pass that identity through consumer-owned workflow/artifact metadata. Release-publish promotes or re-tags that exact digest; it does not rebuild. Hestia is only a Nix cache, not artifact provenance. SDLC defines no registry abstraction.

## Changelog fragments

Add `.changes/<topic>.json` only for externally meaningful/release-worthy changes, with `type`, `semver` (`patch|minor|major`), and `summary`. CI/docs/tests/internal refactors normally need no fragment. PRs never directly edit `CHANGELOG.md` for notes. `sdlc changelog check|plan|finalize` validates consumer-local fragments, computes maximum SemVer intent, renders deterministically, compacts selected fragments, and deletes them. Release selects fragments from one squash-merged PR commit, not an entire Mergify batch. No fragments means no release. Git history archives fragment contents.

## Renovate

The shareable preset is [`default.json`](default.json), the Renovate-native default preset filename. A consumer needs only:

```json
{"extends":["github>blogle/sdlc#v1"]}
```

The preset extends `config:recommended`, enables Renovate's beta Nix manager, groups the `sdlc` flake input and `blogle/sdlc` reusable workflow refs into one PR, and does not enable automerge. Renovate's Nix manager updates the input and `flake.lock` natively; its `depName` is the flake input name (`sdlc`), and the preset uses `semver-coerced` because the `git-refs` datasource defaults to Git ref ordering. `helpers:pinGitHubActionDigests` hardens third-party actions while SDLC reusable workflows remain readable SemVer tags and synchronized with the flake input. Consumer lockfiles make Nixpkgs adoption deliberate: the grouped SDLC Renovate PR is also the fleet Nixpkgs rollout boundary because consumer Nixpkgs follows `sdlc/nixpkgs`. The floating `#v1` preset propagates compatible policy updates; consumers pin runtime SDLC refs to concrete `v1.x.y` tags. SDLC CI validates the preset with Renovate's native config validator.

## Skills and agent loop

The canonical skill is `skills/sdlc/SKILL.md`. The pinned Nix dev shell provides the official Vercel `skills` CLI; consumer `just skills` uses `skills add https://github.com/blogle/sdlc/tree/v1/skills/sdlc --skill sdlc --agent opencode --yes`, and `just skills-update` uses native `skills update -p -y`. The Mergify skills use the same official CLI from `Mergifyio/mergify-cli`; Mergify CLI packaging is separate.

Agent loop: `nix develop`; inspect consumer `ci.nix` and `just --list`; implement Nix checks; run `just check` and `just ci-fast`; add a fragment only when warranted; open a PR with exact verification evidence and granted integration mode. `nix develop` is strongly preferred; never recommend `nix --option build-users-group "" develop`.
