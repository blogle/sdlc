---
name: sdlc
description: Follow the shared SDLC CI, Mergify integration, and release protocol in consuming repositories.
---

# Shared SDLC for consuming repositories

Use this skill whenever changing CI, build/test contracts, Mergify policy, changelog fragments, or release workflows in a repository that consumes `blogle/sdlc`.

## Ownership and lifecycle

The project owns **what** can be built, tested, and published: Nix packages/checks and any narrow publication hook. Consumer-local `ci.nix` maps those project-owned derivations to the versioned `pr-fast`, `candidate`, and `release` stages. The pinned SDLC flake library owns contract validation and stable local app generation; shared workflows own GitHub orchestration, Nix/Hestia setup, queue policy, and changelog mechanics. No part of a consumer build assumes the SDLC repository checkout is present.

1. **Local/dev:** enter the project environment with `nix develop`, then use its `justfile` as the human/agent interface (`just --list`, `just check`, and project-specific recipes). Never recommend `nix --option build-users-group "" develop`.
2. **`pr-fast`:** cheap admission checks only—format, lint, focused/unit tests, contract validation, and other fast feedback. Put expensive builds, broad/integration suites, image builds, and release work in `candidate`, not ordinary PR admission.
3. **Mergify candidate/batch:** eligible PRs are automatically enqueued after admission and policy gates. Mergify validates the synthetic candidate/batch with the declared `candidate` stage. This is the expensive integration/build gate; a passing source-branch check alone does not replace it.
4. **Integration authorization:** `integration:auto` means the task has delegated integration authority; eligible work may auto-queue once protections pass. `integration:review` means implement autonomously, open/update the PR, then stop and wait for review/authorization bound to the exact current head SHA before queue eligibility. A changed head invalidates authorization. Never infer authorization from comment text, and never upgrade your own authority or change review to auto.
5. **Squash merge:** Mergify merges each PR as one squash commit, keeping linear main history. Do not require or perform a zero-commits-behind-main/rebase treadmill. Stacks describe dependency/order relationships between PRs; batches group independent queue work for combined candidate testing. They are orthogonal. Normal flow is automatic enqueue—agents do not need `@mergifyio queue` comments.
6. **Release/publication:** after merge, publish/promote the artifacts already validated by candidate work. Do not rerun general CI or rebuild them on main. Registry-specific promotion belongs in the project's explicit publication hook.

## Contract and tooling

Read consumer-local `ci.nix` and the v1 protocol docs before changing stage wiring. Preserve `schemaVersion = 1`; map the project's actual derivations, and use optional argv-array `commands` only for narrow operations Nix targets cannot express (usually publication). Do not silently skip, rename, or replace shared stages. Release targets must remain empty; release commands promote candidate-validated artifacts only. If the target contract genuinely changes, update its versioned documentation and fixtures.

Use the consumer's generated `nix run .#ci-pr-fast` / `nix run .#ci-candidate` entrypoints for stages. Use `sdlc changelog check|plan|preview|finalize` for consumer-local release-fragment operations. Prefer consumer-local `just` recipes where provided; they should be ergonomic aliases over the pinned flake apps and CLI, not calls into the shared repository's justfile.

Install the maintained shared skills with the consumer's `just skills` recipe. It uses Vercel's official `skills` CLI from the pinned Nix dev shell, targeting OpenCode and resolving the compatible `blogle/sdlc` `v1` source plus Mergify's canonical CLI repository. Refresh installed sources using the CLI's native `skills update -p -y`; do not copy, sync, or write custom skill-install logic.

Nix and cache expectations are centralized. Prefer `nix develop`; CI uses the shared Hestia GitHub Actions cache and Nix Community substituter configuration. Hestia is not a named Cachix cache and needs no cache token. Do not invent project-specific cache names, credentials, upload behavior, or Nix setup unless a documented platform gap requires a reviewed shared-protocol change.

## Changelog fragments

Add `.changes/<topic>.json` only when a change is externally meaningful/release-worthy. CI, docs, tests, and internal refactors normally need no fragment. Fragment metadata (`type`, `semver` patch/minor/major, `summary`) determines category and aggregate SemVer intent. PRs never directly edit `CHANGELOG.md` to add individual release notes. Fragments are ephemeral: release finalization renders/compacts consumed fragments into `CHANGELOG.md` and deletes them; Git history is their archive. No fragments means no release note and no manufactured release.

## Validate and report

Before finishing, run `nix develop -c just check` (or the consuming repo's documented equivalent), then `just ci-fast` / `nix run .#ci-pr-fast`; run candidate-equivalent checks locally when practical without moving expensive work into admission. Validate changed contract/changelog data and do not claim a check that was not run. In the PR, summarize intent, list exact verification commands/results, call out deferred work, and leave integration at the task's granted policy. For `integration:review`, stop for authorization; do not queue or merge yourself.
