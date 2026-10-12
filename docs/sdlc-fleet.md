# SDLC Fleet: Unattended Repository Lifecycle

**Status:** revised architecture approved for prepare-only implementation.
Implementation canary: `blogle/anvil`. Authority: `blogle/sdlc` and
`blogle/dotfiles`.

This document is the authoritative SDLC-side specification. It is intentionally
not a request to create an App, install an App, provision credentials, mutate a
ruleset, or activate a release. Those operations require an explicitly
authorized later landing after the canary is proven.

## 1. Objective

Unattended onboarding, policy management, SDLC upgrades, credential
provisioning, and rolling releases are the target. An agent creates a
repository with a pinned SDLC flake dependency, project-specific CI derivations
and generated thin adapters in a repository PR; another agent registers it in
the dotfiles manifest in a fleet PR. After protected merge, fleet converges
repository policy, credentials, release readiness, and activation.

Manual GitHub App installation per repository is acceptable and is the only
recurring manual enrollment prerequisite. Do not create or install Apps now.
Anvil is the production canary, not a disposable repository.

## 2. SDLC is the source of truth

`blogle/sdlc` exclusively defines public check names and semantics, reusable CI
and release workflows, canonical rulesets, versioned declarative policy,
Mergify queue policy, generated minimum GitHub caller adapters, release
configuration and credentials, and migration/compatibility rules. Consumers
declare only an immutable pinned SDLC revision, project-specific build/test
derivations, and generated thin adapters. Consumer policy logic is forbidden.

Stable public check names are `sdlc-check` (fast PR gate before queue),
`sdlc-verify` (real integration candidate), and `sdlc-policy` (policy
compliance). Legacy names are `sdlc / pr-fast`, `sdlc / candidate`, and
`sdlc / policy`. Upstream-owned migration publishes stable checks, verifies
their availability, migrates required contexts and Mergify only after checks
exist, upgrades consumers, and removes aliases last. Check-contract
compatibility is preserved across releases. GitHub still receives minimal
local caller workflow files.

## 3. Component ownership

- **sdlc:** canonical contract/policy publisher, schema and renderer,
  generation and migration, version-aware fleet plan/reconcile/verify library
  and CLI, and release workflows.
- **dotfiles:** declarative fleet manifest and trusted controller independently
  pinned to a known reconciler. It owns App credentials, identity, live
  rulesets, secrets, variables, and ready state; it does not execute releases.
- **sdlc-fleet App:** only Administration write for rulesets, Actions Secrets
  write, Actions Variables write, and Contents/Metadata read for verification.
  Installation is manual and deferred.
- **sdlc-release App:** Contents write, pull-request write, Checks write, and
  Actions write only if dispatch is proven necessary. It has no ruleset,
  secrets, variables, or workflow write privileges unless later proven.

Privileges are least-scoped to selected installed repositories. Each consumer
executes its own release pipeline.

## 4. Manifest

The dotfiles manifest has this shape; IDs must come from authorized read-only
APIs immediately before privileged mutation and must never be fabricated:

```yaml
schema: 1
owner:
  login: blogle
  id: <immutable-GitHub-account-id>
repositories:
  - name: sdlc
    id: <immutable-repository-id>
    enabled: true
  - name: dotfiles
    id: <immutable-repository-id>
    enabled: true
  - name: anvil
    id: <immutable-repository-id>
    enabled: true
```

The controller validates owner and repository identity immediately before
mutation and rejects transfers or name replacement. `enabled: false` disables
release activation without deleting protections or credentials. A separate
explicit `suspended` state pauses automated mutation.

## 5. Versioned policy and upgrades

The consumer default branch pins the desired SDLC revision in `flake.lock`.
Only a trusted immutable SDLC release, or an approved commit demonstrably
reachable from trusted protected SDLC history, is accepted. The trusted
controller is independently pinned and never executes consumer-selected code
with fleet administration credentials. Policy data is strictly typed.

Effective policy is the fleet security floor plus pinned consumer policy. The
floor requires protected default branches and release tags, no unauthorized
force push or destructive change, trusted check publishers, an approved
integration path, and exactly one designated personal emergency bypass actor.
Neither coding agents nor either App is a bypass.

An upgrade validates the proposed pin, prospective policy, and generated
callers; establishes check availability before requiring a check; pre-stages a
compatible protection migration; validates the integration candidate; merges
normally; and finishes reconciliation. Old/new aliases remain during the
serial per-consumer migration. Unsupported schemas fail closed. Downgrades
never weaken the floor. Corrective adapter changes are proposed as PRs, never
pushed to a protected default branch.

## 6. Human-only break-glass

Exactly one fleet-configured personal GitHub user ID may be an explicit,
auditable ruleset bypass. It is not an exemption. No agent, App, or broad
administrator role is allowed. The effective bypass set is verified exactly.
Break-glass is for CI wedges and critical production emergencies only and
requires follow-up audit. An owner emergency workflow can pause fleet
independently of a broken controller; suspended state stops repair.

## 7. Credential management MVP

Trusted dotfiles automation will eventually store
`SDLC_FLEET_APP_ID`, `SDLC_FLEET_APP_PRIVATE_KEY`, `SDLC_RELEASE_APP_ID`, and
`SDLC_RELEASE_APP_PRIVATE_KEY`. The fleet installation token distributes the
release App credentials to authorized consumer Actions secrets, preferring
branch-restricted release environment secrets and called-workflow wiring.
Ordinary PR checks cannot read them.

Keys never appear in logs, diffs, or agent context. The known MVP risk is that
the release App private key spans its selected installations if a trusted
release workflow is compromised. There is no broker in the MVP. Key generation
or revocation may remain human, while propagation, generation tracking,
verification, and partial recovery are automated. For this implementation no
secrets exist or are provisioned, no App is created or authorized, and no
release is activated.

## 8. Reconciliation

The trusted workflow runs only on protected dotfiles default-branch pushes,
schedule, or authorized manual dispatch, never on secret-bearing PR events.
Untrusted read-only plan/validation jobs are separate from privileged mutation
jobs. For every enabled repository it validates identity and enrollment,
installation access, trusted SDLC pin, generated CI/Mergify adapters, effective
protection, credential authorization, exact-head checks, release readiness,
and only then enables `SDLC_RELEASE_ACTIVATE`.

Reconciliation is idempotent, retryable, level-triggered, isolated per
repository, drift-reporting, and safely interruptible. Confirmed unsafe state
disables activation and alerts; unknown/transient state retains current
activation but never enables it; suspended state does not mutate. Offboarding
does not flap or destructively delete protections.

## 9. Self-host/bootstrap

The dotfiles workflow must work when SDLC publication is broken and regardless
of dotfiles' consumer pin. Prepare-only work has dry-run behavior and no
credential dependency in CI tests. Test Anvil first and preserve its existing
CI/release architecture until replacement is proven.

The ordered later rollout is: Anvil protected queue, release, policy and
credential readiness, upgrade, self-hosted SDLC and dotfiles migration, then
other consumers. Manual paths retire only after success. The controller's own
ruleset is never tightened in the same action that changes the controller;
recovery and rollback remain independent.

## 10. Authority

Ordinary registration and dependency PRs may be auto-merged only through a
trusted enrollment admission policy that verifies allowed changes and identity.
Fleet executable/workflow, trusted pin, security floor, bypass identity,
credential authority, and Mergify gate changes require stronger review outside
untrusted PR code. An untrusted PR never executes with App private keys.

## 11. Acceptance criteria

1. Authorized onboarding needs only repository PR, fleet PR, and manual App
   installation.
2. SDLC defines shared CI names, generated callers, Mergify, and rulesets.
3. Pinned revisions and corrective PRs propagate contract changes.
4. Anvil completes protected queue, readiness, and unattended release.
5. Anvil upgrades automatically.
6. Upgrade and rollback have no check deadlock.
7. Key propagation and partial rotation need no consumer edits.
8. Exactly one personal emergency bypass exists and Apps have none.
9. SDLC and dotfiles operate as managed consumers.
10. Reconciliation is idempotent and interruption-safe.
11. Unauthorized identity, weak policy, bad pin, and unknown permissions fail
    closed.
12. Consumers retain no SDLC integration logic beyond builds and generated
    minimum files.

## 12. Engineering priorities and handoff

Use the existing Python policy, onboarding, Nix, and reusable workflows. Do not
add a domain-specific language, long-running controller, or release service.
This prepare-only handoff creates implementation and tests but does not merge
anything until a human creates/installs the App, provisions credentials, and
authorizes ordered landing. Existing pipelines and release activation remain
unchanged.

Peer instruction: consume `src/fleet.py` and this document as the authority;
use `sdlc fleet plan|reconcile|verify` with immutable manifest/policy files;
keep the controller's pin independent of consumer pins; treat an empty
`mutations` list and `prepare_only: true` as mandatory; and propose consumer
adapter corrections as PRs. Do not persist this full specification in a
consumer repository, create fleet infrastructure, or alter protected settings.

The command requires `--attestation` from the trusted dotfiles controller and
`--consumer-pin` from the consumer's exact lockfile. It does not infer
reachability from a claimed boolean. The injected controller client must verify
the immutable SDLC repository/commit identity and protected ancestry, then emit
the attestation. `reconcile_repository` accepts an injected authenticated
read-only client and returns corrective-PR operations; `apply=True` is rejected
until a separately reviewed post-bootstrap mutation implementation exists.
Credential propagation and rotation are represented by readiness contracts,
but no key material or GitHub mutation is implemented in this canary.

Verification is `nix develop -c just check` plus actionlint. Do not use build
users-group overrides.
