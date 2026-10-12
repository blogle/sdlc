# blogle/sdlc

`blogle/sdlc` is a pinned Nix library and shared GitHub workflow source. Consumers own their Nix checks/packages, `ci.nix`, `justfile`, candidate artifact publication, and final artifact promotion. The shared repo does not run a separate build DSL or read consumer files from its checkout.

SDLC owns the canonical repository ruleset. The `sdlc ruleset plan|apply|check`
commands render it from the installed SDLC package and never read consumer
repository-policy files. No App, credential, or release activation is created
by these commands.

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

The copy-pastable consumer input and `flake.lock` use a concrete SDLC release tag. Consumer workflows pin reusable workflows to that same release. Nested reusable workflow calls use `uses: $/.github/workflows/stage.yml`, resolving at the exact outer workflow revision so one consumer pin atomically selects the whole SDLC workflow stack. After this bootstrap is reviewed/merged, maintainers must publish the initial `v1.0.0` tag and establish the moving `v1` preset ref at that release; advance `v1` when compatible preset policy changes land. Later compatible runtime releases use `v1.x.y`, with breaking protocol changes on v2. Renovate groups the Nix input update with all reusable SDLC workflow ref updates. The shared Renovate preset itself remains `github>blogle/sdlc#v1`, a floating compatible policy channel; preset breaking changes require a new major.

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
    uses: blogle/sdlc/.github/workflows/pr-fast.yml@v1.2.5
  candidate:
    uses: blogle/sdlc/.github/workflows/candidate.yml@v1.2.5
  # Stable required-check contexts must be emitted by ordinary caller jobs.
  sdlc-pr-fast:
    name: sdlc / pr-fast
    needs: pr-fast
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: blogle/sdlc/actions/status-gate@v1.2.5
        with:
          result: ${{ needs.pr-fast.result }}
          stage: pr-fast
  sdlc-candidate:
    name: sdlc / candidate
    needs: candidate
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: blogle/sdlc/actions/status-gate@v1.2.5
        with:
          result: ${{ needs.candidate.result }}
          stage: candidate
  policy:
    uses: blogle/sdlc/.github/workflows/policy-check.yml@v1.2.5
  sdlc-policy:
    name: sdlc / policy
    needs: policy
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: blogle/sdlc/actions/status-gate@v1.2.5
        with:
          result: ${{ needs.policy.result }}
          stage: policy
```

Nested reusable-workflow check names include caller/callee prefixes and are not stable required-check contexts. The three ordinary local jobs above are the only consumer-local stubs required by GitHub; `actions/status-gate` owns result validation and Mergify event routing. The shared candidate workflow skips ordinary PRs and runs merge groups and `mergify/merge-queue/*` synthetic PRs. Canonical Mergify continues to require `sdlc / pr-fast` and `sdlc / candidate`.

The reusable workflows checkout the caller and invoke **Hestia's native matrix action** on that consumer's `hydraJobs` stage attrset. Hestia evaluates with the consumer's lockfile-pinned `nix-eval-jobs`, honors native `meta.hestia.group`/`meta.hestia.os`, fans out derivation builds, and owns the GitHub Actions cache. SDLC adds no matrix schema or scheduler. Hestia cache entries are repository-scoped and evictable; a miss only costs performance. It is not a durable artifact store, not a candidate provenance mechanism, and does not promise Nix outputs will be reusable by another repo or developer machine. No cache name or cache secret is used. External Nix binary caches are an optional future optimization and are not a v1 protocol interface.

Future cache research is explicitly outside v1: native Nix S3 caches against custom S3-compatible endpoints (including comparing free-tier providers), niks3's server/DB/GC tradeoffs, and experimental OCI/GHCR-backed caches. No backend abstraction or cost/free-tier promise is part of this protocol.

## Lifecycle and Mergify

`pr-fast` is cheap admission; `candidate` is expensive synthetic Mergify batch validation. Mergify queue batching only optimizes candidate validation: a batch does not imply a release or one combined release note. Each squash-merged PR that added one or more valid fragments may produce **one release for that PR**; an individual merged PR with no fragments produces no release. Squash merge gives each PR one logical main commit. Stacks are dependency/order relationships, batches are candidate grouping, and they remain orthogonal.

Merge Queue performs candidate testing and merging; Workflow Automation is used only as a thin auto-enqueue trigger. GitHub rulesets remain the authoritative policy source, and Mergify Merge Protections are intentionally not enabled. The canonical `.mergify.yml` keeps admission and merge authority in `queue_rules`; matching ready PRs are queued automatically, while the queue command and checkbox controls remain escape hatches. Same-owner consumers (including GitHub personal-account repositories) use `extends: sdlc`; verified by Mergify's green configuration check in `blogle/dotfiles` PR #88. Mergify's `extends` syntax accepts only the bare repository name, not `owner/repo` or arbitrary URLs. The SDLC source repository must have Mergify installed and be at least as visible as consumers. Unlike version-pinned runtime workflows, this imports the SDLC default-branch Mergify policy **live**: review and validate `.mergify.yml` changes as fleet-wide policy rollouts. Cross-owner consumers need a differently owned shared preset or a local policy.

Create exactly one of `integration:auto` or `integration:review` on each PR. Queue conditions require base `main` or `master` (matching the actual destination branch), successful `sdlc / pr-fast`, and exactly one authority mode: auto label without review, or review label without auto plus `#approved-reviews-by >= 1`. Workflow Automation mirrors these conditions and automatically enqueues eligible PRs. `merge_conditions` contains only successful `sdlc / candidate`, so candidate validation remains the merge gate. Auto work progresses without a human comment or checkbox; review work remains blocked/pending until exact-head GitHub approval and all queue conditions pass. Queue commands and controls are manual escape hatches, not authorization—labels, GitHub approval, and checks are. GitHub stale-review dismissal invalidates review authorization after head changes. Missing, unknown, or conflicting labels fail closed. Require `sdlc / pr-fast` in the GitHub ruleset; candidate is checked by the queue's merge conditions.

GitHub rulesets are the authoritative protection layer: PR-only changes, squash-only merge, stale-approval dismissal, non-strict status freshness, stable `sdlc / pr-fast`, deletion protection, and non-fast-forward protection. Mergify Workflow Automation only triggers enqueueing; Merge Queue owns candidate batching/validation and merge execution. Queue command restrictions allow sender permission `write` or `anvil-daemon[bot]` to request queueing as an escape hatch; queue conditions remain the authorization/gating source. Mergify automatically injects supported GitHub branch/ruleset protections into queue behavior.

## Canonical Repository Ruleset

SDLC owns the canonical `SDLC default branch` ruleset. Consumers do not declare repository-policy JSON or ruleset definitions. The installed SDLC package renders the same policy for every repository, discovers the default branch through GitHub, and preserves existing released checks: `sdlc / pr-fast` is required; `sdlc / policy` is retained only when already present. No new check is required before it exists.

Use the existing `policy` command for legacy consumer configuration. For canonical fleet application, dotfiles runs from the consumer checkout:

```sh
nix run .#sdlc -- ruleset plan --repo OWNER/NAME
nix run .#sdlc -- ruleset apply --repo OWNER/NAME
nix run .#sdlc -- ruleset check --repo OWNER/NAME
```

`plan` is read-only. `apply` creates or updates only the owned canonical ruleset when it drifts, then reads it back. `check` is read-only. The command never reads `.github/repository-policy.json`; the consumer flake lock selects the SDLC implementation.

## Release and artifacts

The rolling updater coalesces all current unreleased fragments into one deterministic `sdlc/release-next` PR. It uses native GitHub Actions concurrency (`cancel-in-progress: false`), a GitHub App token, and `--force-with-lease`; it never pushes `main`. The canonical manifest contract is in [`docs/release-manifest.md`](docs/release-manifest.md). A merged manifest remains on `main`; successor allocation checks its exact tag and completed publication ledger rather than treating manifest existence as pending publication.

Release reconciliation is fail-closed until the canonical ruleset, App credentials, release gate, and publisher are all provisioned. `.github/workflows/release.yml` delegates to the rolling updater and rejects `publish_version` until the later publisher milestone; it never pushes `main`. The self-release caller in PR #13 must be rebased against this contract and remains intentionally unmerged. Labels do not bypass rulesets, and an Actions `GITHUB_TOKEN`-created release PR is not an unattended replacement. The fixture's publication hook remains consumer-owned `just release-publish <version>`.

The release App intentionally does not receive ruleset write access. Because GitHub redacts `bypass_actors` from read-only ruleset responses, authenticated bootstrap writes `SDLC_RULESET_ATTESTATION` as a repository variable only after an admin read proves the canonical ruleset has zero bypass actors. The release gate and merge worker require that attestation, bind it to the ruleset ID and current `updated_at`, and fail closed on missing, changed, duplicate, or rolled-back policy. Re-run the admin bootstrap after any intentional canonical ruleset edit; do not hand-edit the variable or grant the release App ruleset access.

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

The existing consumer release bootstrap is one idempotent admin command: `nix run github:blogle/sdlc/v1#sdlc -- onboard --repo OWNER/NAME`. Ruleset application is separate and uses the canonical `sdlc ruleset plan|apply|check` interface above.

Agent loop: `nix develop`; inspect consumer `ci.nix` and `just --list`; implement Nix checks; run `just check` and `just ci-fast`; add a fragment only when warranted; open a PR with exact verification evidence and granted integration mode. `nix develop` is strongly preferred; never recommend `nix --option build-users-group "" develop`.
