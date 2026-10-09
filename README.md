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

`pr-fast` is cheap admission; `candidate` is expensive synthetic Mergify batch validation. Mergify queue batching only optimizes candidate validation: a batch does not imply a release or one combined release note. Each squash-merged PR that added one or more valid fragments may produce **one release for that PR**; an individual merged PR with no fragments produces no release. Squash merge gives each PR one logical main commit. Stacks are dependency/order relationships, batches are candidate grouping, and they remain orthogonal.

Merge Queue performs candidate testing and merging; Workflow Automation is used only as a thin auto-enqueue trigger. GitHub rulesets remain the authoritative policy source, and Mergify Merge Protections are intentionally not enabled. The canonical `.mergify.yml` keeps admission and merge authority in `queue_rules`; matching ready PRs are queued automatically, while the queue command and checkbox controls remain escape hatches. Same-owner consumers (including GitHub personal-account repositories) use `extends: sdlc`; verified by Mergify's green configuration check in `blogle/dotfiles` PR #88. Mergify's `extends` syntax accepts only the bare repository name, not `owner/repo` or arbitrary URLs. The SDLC source repository must have Mergify installed and be at least as visible as consumers. Unlike version-pinned runtime workflows, this imports the SDLC default-branch Mergify policy **live**: review and validate `.mergify.yml` changes as fleet-wide policy rollouts. Cross-owner consumers need a differently owned shared preset or a local policy.

Create exactly one of `integration:auto` or `integration:review` on each PR. Queue conditions require base `main` or `master` (matching the actual destination branch), successful `sdlc / pr-fast`, and exactly one authority mode: auto label without review, or review label without auto plus `#approved-reviews-by >= 1`. Workflow Automation mirrors these conditions and automatically enqueues eligible PRs. `merge_conditions` contains only successful `sdlc / candidate`, so candidate validation remains the merge gate. Auto work progresses without a human comment or checkbox; review work remains blocked/pending until exact-head GitHub approval and all queue conditions pass. Queue commands and controls are manual escape hatches, not authorization—labels, GitHub approval, and checks are. GitHub stale-review dismissal invalidates review authorization after head changes. Missing, unknown, or conflicting labels fail closed. Require `sdlc / pr-fast` in the GitHub ruleset; candidate is checked by the queue's merge conditions.

GitHub rulesets are the authoritative protection layer: PR-only changes, squash-only merge, stale-approval dismissal, non-strict status freshness, stable `sdlc / pr-fast`, deletion protection, and non-fast-forward protection. Mergify Workflow Automation only triggers enqueueing; Merge Queue owns candidate batching/validation and merge execution. Queue command restrictions allow sender permission `write` or `anvil-daemon[bot]` to request queueing as an escape hatch; queue conditions remain the authorization/gating source. Mergify automatically injects supported GitHub branch/ruleset protections into queue behavior.

## Repository policy as IaC

Consumers declare extra required status contexts plus a small lifecycle flag `require_policy_check` in `.github/repository-policy.json` (defaults to `false`). First-time onboarding is a staged transition: (1) a PR adds the declaration with `require_policy_check: false` and installs the pinned read-only reusable workflow plus ordinary wrapper job named exactly `sdlc / policy`; when the base has no declaration and GitHub's read-only ruleset API confirms no live canonical ruleset, CI validates proposal and caller and emits a distinct `FIRST-ONBOARDING` notice. This interim green outcome **does not prove live repository protection**. If a live canonical ruleset exists despite no base declaration, onboarding fails closed as a lost declaration. (2) Merge the caller/declaration. From a maintainer workstation authenticated with `gh auth login` as a repository administrator, run `nix develop -c sdlc policy plan --repo blogle/REPO`, review the payload, `nix develop -c sdlc policy apply --repo blogle/REPO`, then `nix develop -c sdlc policy check --repo blogle/REPO`. This applies and verifies baseline rules without requiring `sdlc / policy`. (3) In a reviewed PR set `require_policy_check: true`; that PR's live drift comparison uses the base declaration, so the new requirement is not applied prematurely. After merge, run authenticated `nix develop -c sdlc policy apply --repo blogle/REPO` to add `sdlc / policy` to the required contexts. The caller is already merged and has been reliably reporting its stable status throughout; verify again with `policy check`. This precise sequence ensures the check becomes required only after successful baseline apply/check and a reviewed opt-in. Apply is idempotent, only creates/updates the exact `SDLC default branch` ruleset, and reads it back to verify. It does not touch labels, other rulesets, or credentials. `policy check` is read-only. Omitting `--repo` infers the GitHub repository from `origin` and asks GitHub for its default branch. No GitHub App or Actions write secret is used.

The canonical ruleset uses GitHub's `~DEFAULT_BRANCH` selector, documented by GitHub's ruleset API and covered for both `main` and `master`; explicit declaration `default_branch` opts into a literal branch selector. `sdlc / pr-fast` is always required; `sdlc / policy` becomes required when `require_policy_check` is true; declared extra contexts are also required. The check reads rulesets (including inherited rulesets), verifies enforcement, targets, check contexts, PR/squash and stale-review settings, deletion/force-push protections, and visible bypass actors. GitHub only returns bypass actors to users with write access to the ruleset; a read-only Actions token can therefore produce a warning that this field was redacted. All visible fields still must match, and the check explicitly says bypass configuration was not verified. Public repositories can use the anonymous REST fallback if `GITHUB_TOKEN` lacks endpoint access; for private repositories the token must be able to read rulesets, otherwise the check fails closed. Any other applicable rulesets are reported as unmanaged warnings and never mutated.

On policy PRs the proposed JSON is schema-validated, while live drift is evaluated against the committed base-branch declaration. Therefore a policy change can be reviewed without prematurely treating the desired future rules as live. After merge, run the exact `policy apply` command above. Missing or skewed canonical policy fails with the plan/apply commands. If `plan` reports duplicate rulesets named `SDLC default branch`, inspect them and manually resolve the ambiguity before retrying; apply deliberately refuses to guess. Unrelated legacy rulesets are never changed or deleted. For repositories such as dotfiles with two legacy rulesets, inspect overlap and precedence using GitHub's Rulesets page/API, then schedule a separately reviewed manual migration; do not delete them as part of SDLC apply. Existing automatic `reconcile-policy.yml` and `SDLC_POLICY_APP_*` secrets are retired and can be removed after consumers migrate. The clean-room example includes the declaration and caller wiring.

## Release and artifacts

The consumer invokes the reusable release workflow on main pushes only; do not rerun general CI on merged main. It uses native GitHub Actions concurrency (`queue: max`, `cancel-in-progress: false`) to serialize releases, and processes first-parent merge commits individually. For each PR commit it selects only that commit's changed `.changes/*.json` files, replans against current tags/main, finalizes those fragments, and atomically pushes the CHANGELOG commit and version tag. Stale/mutated fragments fail closed. If main/tag advances during the atomic push, neither remote ref changes; the fragments remain pending and retry replans against current main/tags. Tag annotations bind the source commit so reruns can resume publication. A workflow-dispatch with no `publish_version` recovers remaining pending fragments per introducing commit if GitHub's bounded 100-run concurrency queue fills. A dispatch with an existing `publish_version` retries publication for an already-finalized release.

Release finalization remains blocked for protected personal-account repositories: `.github/workflows/release.yml` is deliberately unchanged and still requires the release App credentials. Labels do not bypass rulesets, and an Actions `GITHUB_TOKEN`-created release PR is not an unattended replacement: GitHub requires approval for certain workflow runs triggered by its generated PRs. Recommended follow-up state machine: serialize source-commit processing; validate the exact source fragment; create an immutable candidate artifact once; open a release PR containing the changelog and fragment removal; require ordinary policy checks and human approval/merge without bypass; use the merged release commit to create an immutable version tag; promote the recorded artifact digest; record completion idempotently. Retry before PR merge by updating/reusing the same PR, after merge by reusing the source-bound tag, and after publication failure by republishing that tag's recorded digest. No-fragment source PRs create no release. This still requires proving a reliable trigger/credential for PR creation and post-merge tag/publish, plus a canary; track this separately in [issue #9](https://github.com/blogle/sdlc/issues/9). Do not treat this proposed state machine as implemented or enabled. The fixture's publication hook remains consumer-owned `just release-publish <version>`.

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
