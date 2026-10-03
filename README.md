# Shared SDLC

Shared control plane for when and how project-owned Nix targets are built, tested, and published. Projects declare *what* their targets mean; this repository owns the common runner, reusable workflows, queue policy, and release-fragment tooling.

## Lifecycle

1. `pr-fast` performs cheap admission on ordinary PRs.
2. Mergify automatically queues eligible PRs. Its synthetic candidate/batch runs the expensive `candidate` stage against the candidate commit; no source-branch rebase/up-to-date requirement is imposed.
3. Passing candidates are squash-merged, yielding one logical mainline commit per PR. Batching is just speculative validation; it does not change history shape.
4. Post-merge release work publishes already-validated artifacts only. It must not rerun general CI or rebuild them.

Mergify merge protections must be enabled for `auto_merge_conditions`. Repositories enable squash as their allowed GitHub merge method. The canonical config is consumed using `extends: sdlc`; repositories may add specifically named local rules/overrides.

## One-time downstream setup

1. Add the v1 contract at `ci.nix.json` (JSON is used as a deliberately tiny, safely-evaluable subset of a Nix attrset):

```json
{
  "schemaVersion": 1,
  "integration": "integration:review",
  "stages": {
    "pr-fast": { "targets": [ ".#checks.x86_64-linux.fast" ] },
    "candidate": { "targets": [ ".#checks.x86_64-linux.candidate" ] },
    "release": { "targets": [], "commands": [["./scripts/publish-validated-artifacts"]] }
  }
}
```

Targets are arguments to `nix build`; optional commands are argv arrays and are escape hatches, not a second build system. Valid stage names are `pr-fast`, `candidate`, and `release`. Release is a publication-only hook. Missing stages and unknown schema versions fail closed. See [`docs/protocol-v1.md`](docs/protocol-v1.md).

2. Call the centrally versioned workflows, e.g. `blogle/sdlc/.github/workflows/pr-fast.yml@v1`, `candidate.yml@v1`, and `release.yml@v1`. Pin an immutable tag/SHA for stricter supply-chain control. `v1` is the moving compatible major channel; breaking protocol changes use a new major.
3. Add `.mergify.yml` containing `extends: sdlc`, select exactly one integration label, and enable Merge Queue + Merge Protections. Example in [`examples/minimal`](examples/minimal).
4. Ensure the project's required check for admission is `sdlc / pr-fast`. Candidate workflow is required only for the synthetic candidate. Keep branch protection from requiring candidate checks on ordinary source PRs.
5. Install shared agent and Mergify skills together with `nix run github:blogle/sdlc/v1#installSkills`. The installer places SDLC skills in `.agents/skills/` and delegates Mergify skill installation to [`blogle/mergify-nix`](https://github.com/blogle/mergify-nix). The Mergify CLI is exposed as `.#mergify-cli` from that packaging flake. Add the setup recipe shown in [`examples/minimal/justfile`](examples/minimal/justfile) so a new checkout installs them with `just setup`.

## Integration authority

Every PR must carry exactly one `integration:auto` or `integration:review` label; absent/unknown policy blocks automatic integration. Auto policy permits automatic queueing after ordinary protections. Review policy also requires a successful `sdlc-integration-authorized` check before it may queue/merge. The check producer must read the current PR head SHA, bind authorization to that exact SHA, and publish the named check on that SHA; any head update invalidates the authorization. A GitHub App or trusted workflow with narrowly scoped credentials can implement this attestation. This bootstrap defines the check contract and fails closed but deliberately does not deploy an authorization service or interpret comments.

## Changelogs

Only user-visible/release-worthy changes need `.changes/*.json` fragments, for example `{"type":"feature","semver":"minor","summary":"..."}`. CI/docs/test/refactor-only changes need none. `sdlc changelog check|preview|finalize` validates, renders deterministically in filename order, computes max SemVer intent, compacts into `CHANGELOG.md`, and deletes consumed fragments. No fragments means no release. Individual PRs never edit `CHANGELOG.md`; finalization owns it. Git history archives the fragments.

## Contributor/agent happy path

Use `nix develop` (never an ad-hoc Nix option workaround), then `just check`. For a project, run `sdlc run pr-fast` while iterating, attach a release fragment only when warranted, and let the candidate workflow validate the queue's synthetic candidate. Agents need no queue comments. Keep language/framework behavior in project Nix targets; common timing and policy live here.

## Central cache and deferred items

Reusable workflows centralize Nix setup and the Hestia Cachix endpoint (`hestia`). Forks without cache credentials still build without pushing. v1 does not prescribe artifact-registry promotion: projects provide a publication hook that promotes artifacts produced by candidate validation. A production head-bound authorization app and registry-specific promotion are deliberate follow-up integrations.
