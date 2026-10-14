# blogle/sdlc

`blogle/sdlc` is the pinned fleet CI contract and shared workflow source. Consumers own named Nix check derivations in `ci.nix` and one executable publication hook. SDLC owns check evaluation, scheduling, Hestia caching, stable status aggregation, and reusable workflows.

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

Consumers declare independent derivations for the fast and verify stages and an app for publication:

```nix
fast = {
  fmt = checks.fmt;
  clippy = checks.clippy;
};

verify = {
  tests = checks.tests;
  frontend = checks.frontend;
  process = checks.process;
  kubernetes = checks.kubernetes;
};

publish = {
  type = "app";
  program = "${publishHook}/bin/publish";
};
```

`sdlc.lib.mkConsumer` validates and returns these three outputs without prescribing a runner representation. The consumer flake exposes them as `ci.<system>.fast`, `ci.<system>.verify`, and `ci.<system>.publish`. SDLC's workflows privately evaluate and schedule the named derivations; Hestia's flake interface is not part of the consumer contract. Every named derivation remains independently buildable, diagnosable, and cacheable; no synthetic stage-success derivation is created. The standard required contexts are `sdlc / check`, `sdlc / verify`, and `sdlc / policy`.

```make
ci-fast:
    nix build --no-link .#ci.x86_64-linux.fast

ci-verify:
    nix build --no-link .#ci.x86_64-linux.verify
```

The consumer flake assembles the declared derivations behind the standard contract:

```nix
ci = nixpkgs.lib.genAttrs [ "x86_64-linux" ] (system:
  let contract = import ./ci.nix { inherit pkgs system; };
  in sdlc.lib.mkConsumer contract);

packages = forAllSystems (system:
  { sdlc = sdlc.packages.${system}.sdlc; });

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

`sdlc install` generates minimal pinned callers for CI, release workflows, and cache maintenance. The shared CI workflow reads the consumer's pinned `ci` outputs and obtains its evaluator from the same pinned SDLC revision; consumers do not expose runner tools, define CI matrices, or add stage aggregators.

```yaml
jobs:
  sdlc:
    uses: blogle/sdlc/.github/workflows/ci.yml@<PINNED_SHA>
```

The shared workflow emits stable `sdlc / check` and `sdlc / verify` contexts. Check is the admission stage; verify runs on Mergify merge candidates and on default-branch pushes to warm Hestia's cache. `sdlc / policy` checks generated adapters and canonical policy.

Hestia fans out independent checks, substitutes cached outputs, and exposes individual results in logs. Hestia cache entries are scoped to the consumer repository and can be evicted. SDLC warms the default-branch cache after merge and provides a generated scheduled GC caller. The cache is not artifact provenance, a durable store, or cross-repository sharing.

Future cache research is explicitly outside v1: native Nix S3 caches against custom S3-compatible endpoints (including comparing free-tier providers), niks3's server/DB/GC tradeoffs, and experimental OCI/GHCR-backed caches. No backend abstraction or cost/free-tier promise is part of this protocol.

## Lifecycle and Mergify

`check` is cheap admission; `verify` is full validation of the actual synthetic Mergify merge candidate. Mergify queue batching only optimizes verification: a batch does not imply a release or one combined release note. Each squash-merged PR that added one or more valid fragments may produce **one release for that PR**; an individual merged PR with no fragments produces no release. Squash merge gives each PR one logical main commit. Stacks are dependency/order relationships, batches are candidate grouping, and they remain orthogonal.

Merge Queue performs candidate testing and merging; Workflow Automation is used only as a thin auto-enqueue trigger. GitHub rulesets remain the authoritative policy source, and Mergify Merge Protections are intentionally not enabled. The canonical `.mergify.yml` keeps admission and merge authority in `queue_rules`; matching ready PRs are queued automatically, while the queue command and checkbox controls remain escape hatches. Same-owner consumers (including GitHub personal-account repositories) use `extends: sdlc`; verified by Mergify's green configuration check in `blogle/dotfiles` PR #88. Mergify's `extends` syntax accepts only the bare repository name, not `owner/repo` or arbitrary URLs. The SDLC source repository must have Mergify installed and be at least as visible as consumers. Unlike version-pinned runtime workflows, this imports the SDLC default-branch Mergify policy **live**: review and validate `.mergify.yml` changes as fleet-wide policy rollouts. Cross-owner consumers need a differently owned shared preset or a local policy.

Create exactly one of `integration:auto` or `integration:review` on each PR. Queue conditions require base `main` or `master` (matching the actual destination branch), successful `sdlc / check`, and exactly one authority mode: auto label without review, or review label without auto plus `#approved-reviews-by >= 1`. Workflow Automation mirrors these conditions and automatically enqueues eligible PRs. `merge_conditions` contains only successful `sdlc / verify`, so full candidate validation remains the merge gate. Auto work progresses without a human comment or checkbox; review work remains blocked/pending until exact-head GitHub approval and all queue conditions pass. Queue commands and controls are manual escape hatches, not authorization—labels, GitHub approval, and checks are. GitHub stale-review dismissal invalidates review authorization after head changes. Missing, unknown, or conflicting labels fail closed. Require `sdlc / check` in the GitHub ruleset; verify is checked by the queue's merge conditions.

GitHub rulesets are the authoritative protection layer: PR-only changes, squash-only merge, stale-approval dismissal, non-strict status freshness, stable `sdlc / check`, deletion protection, and non-fast-forward protection. Mergify Workflow Automation only triggers enqueueing; Merge Queue owns candidate batching/validation and merge execution. Queue command restrictions allow sender permission `write` or `anvil-daemon[bot]` to request queueing as an escape hatch; queue conditions remain the authorization/gating source. Mergify automatically injects supported GitHub branch/ruleset protections into queue behavior.

## Repository policy as IaC

Consumers declare extra required status contexts plus a small lifecycle flag `require_policy_check` in `.github/repository-policy.json` (defaults to `false`). First-time onboarding is a staged transition: (1) a PR adds the declaration with `require_policy_check: false` and installs the pinned read-only reusable workflow plus ordinary wrapper job named exactly `sdlc / policy`; when the base has no declaration and GitHub's read-only ruleset API confirms no live canonical ruleset, CI validates proposal and caller and emits a distinct `FIRST-ONBOARDING` notice. This interim green outcome **does not prove live repository protection**. If a live canonical ruleset exists despite no base declaration, onboarding fails closed as a lost declaration. (2) Merge the caller/declaration. From a maintainer workstation authenticated with `gh auth login` as a repository administrator, run `nix develop -c sdlc policy plan --repo blogle/REPO`, review the payload, `nix develop -c sdlc policy apply --repo blogle/REPO`, then `nix develop -c sdlc policy check --repo blogle/REPO`. This applies and verifies baseline rules without requiring `sdlc / policy`. (3) In a reviewed PR set `require_policy_check: true`; that PR's live drift comparison uses the base declaration, so the new requirement is not applied prematurely. After merge, run authenticated `nix develop -c sdlc policy apply --repo blogle/REPO` to add `sdlc / policy` to the required contexts. The caller is already merged and has been reliably reporting its stable status throughout; verify again with `policy check`. This precise sequence ensures the check becomes required only after successful baseline apply/check and a reviewed opt-in. Apply is idempotent, only creates/updates the exact `SDLC default branch` ruleset, and reads it back to verify. It does not touch labels, other rulesets, or credentials. `policy check` is read-only. Omitting `--repo` infers the GitHub repository from `origin` and asks GitHub for its default branch. No GitHub App or Actions write secret is used.

The reusable checker checks out the caller for its declaration/base SHA, then invokes `uses: $/actions/repository-policy`; the `$` self-reference loads the composite action from the exact SDLC revision that supplied the called workflow. The script runs from `GITHUB_ACTION_PATH` while reading config from the caller's workspace, so consumers do not vendor SDLC scripts. REST reads paginate the ruleset list for IDs, then fetch full canonical details by ID; list summaries are never mistaken for complete rulesets. The canonical ruleset uses GitHub's `~DEFAULT_BRANCH` selector, documented by GitHub's ruleset API and covered for both `main` and `master`; explicit declaration `default_branch` opts into a literal branch selector. `sdlc / check` is always required; `sdlc / policy` becomes required when `require_policy_check` is true; declared extra contexts are also required. The check reads rulesets (including inherited rulesets), verifies enforcement, targets, check contexts, PR/squash and stale-review settings, deletion/force-push protections, and visible bypass actors. GitHub only returns bypass actors to users with write access to the ruleset; a read-only Actions token can therefore produce a warning that this field was redacted. All visible fields still must match, and the check explicitly says bypass configuration was not verified. Public repositories can use the anonymous REST fallback if `GITHUB_TOKEN` lacks endpoint access; for private repositories the token must be able to read rulesets, otherwise the check fails closed. Any other applicable rulesets are reported as unmanaged warnings and never mutated.

On policy PRs the proposed JSON is schema-validated, while live drift is evaluated against the committed base-branch declaration. Therefore a policy change can be reviewed without prematurely treating the desired future rules as live. After merge, run the exact `policy apply` command above. Missing or skewed canonical policy fails with the plan/apply commands. If `plan` reports duplicate rulesets named `SDLC default branch`, inspect them and manually resolve the ambiguity before retrying; apply deliberately refuses to guess. Unrelated legacy rulesets are never changed or deleted. For repositories such as dotfiles with two legacy rulesets, inspect overlap and precedence using GitHub's Rulesets page/API, then schedule a separately reviewed manual migration; do not delete them as part of SDLC apply. Existing automatic `reconcile-policy.yml` and `SDLC_POLICY_APP_*` secrets are retired and can be removed after consumers migrate. The clean-room example includes the declaration and caller wiring.

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

Consumer release bootstrap is one idempotent admin command, not a task for coding agents: `nix run github:blogle/sdlc/v1#sdlc -- onboard --repo OWNER/NAME`. It prompts for the local App ID and private-key path when flags are omitted, preflights committed workflows, successful CI, artifact hook, package permission, App installation/secrets, and canonical policy, then safely plans/applies/checks policy and enables `SDLC_RELEASE_ACTIVATE=true` only after all checks pass. `--dry-run` performs prepare-only verification with no writes. Private keys are read locally and passed to `gh secret set` via stdin; they are never printed, checked in, or passed to coding agents. Optional `sdlc release status` and `disable` remain read-only/emergency conveniences. Nexus currently exposes ruleset operations but not Actions variables/secrets read/write, so fully hands-off fleet onboarding depends on a narrow authorized vars/secrets gateway; SDLC does not claim that automation exists until the gateway is available. No Anvil activation is implied.

Agent loop: `nix develop`; inspect consumer `ci.nix` and `just --list`; implement Nix checks; run `just check` and `just ci-fast`; add a fragment only when warranted; open a PR with exact verification evidence and granted integration mode. `nix develop` is strongly preferred; never recommend `nix --option build-users-group "" develop`.
