# Rolling release PR specification

Status: **Target design — not implemented or enabled.** Supersedes the release-finalization portions of [Consumer protocol v1](protocol-v1.md) and the earlier proposal in [issue #9](https://github.com/blogle/sdlc/issues/9). The existing `.github/workflows/release.yml` still uses protected-main direct-push finalization and must not be activated as the implementation of this spec.

## Objective and non-goals

Keep ordinary feature integration under Mergify while automatically generating a checked-in, release-exact `CHANGELOG.md` and version state. Publish from an immutable commit that has actually landed on `main`. Coalesce merged changes by continuously replacing **one rolling release PR**, without a debounce timer and without admitting that PR to Mergify.

**Not in v1:** canceling obsolete Mergify candidate runs, artificial release delays, per-PR release guarantees, a separate changelog-sync PR, a second changelog generator, or a new generic artifact/registry interface. Obsolete candidate cancellation can be added later without blocking this design.

## Non-negotiable invariants

1. **Integration:** ordinary PRs alone use Mergify for admission, batch/candidate validation and squash-merging to `main`. They contribute uniquely named `.changes/*.json` fragments and never edit `CHANGELOG.md`.
2. **Release identity:** one release PR at a time, on bot-controlled branch `sdlc/release-next`, targeting `main`. Its generated commit includes the complete changelog/version changes, consumes exactly the included fragments, and records a machine-readable release manifest.
3. **Coalescing:** every eligible advancement of `main` may replace the *unmerged* release candidate with a newly generated superset. No waiting period; the branch update is deliberately non-fast-forward and uses a lease. Once the release PR merges, that exact merged commit is frozen and never amended.
4. **Independent priority:** the release PR is **excluded from Mergify** and can be auto-merged by the release bot immediately after its own required checks pass. It does **not** require review. This is a separate expedited lane, not a promise to preempt a feature merge already in progress.
5. **No global strict freshness:** retain GitHub repository-wide **non-strict** required checks; **do not enable “Require branches to be up to date before merging”**. Mergify's candidate validation continues to provide freshness for normal PRs. Only release PRs receive special head-vs-base freshness validation and merge gating.
6. **Publication consistency:** the GitHub Release, tag, changelog, version and artifacts describe the same **actual merged release commit**. If a stale candidate somehow merges, **do not tag or publish it**; fail closed, reconcile and repair through a subsequent release candidate.
7. **Idempotency:** already committed release identities and completed artifact digests are reused on retry. Never rebuild or substitute new bytes under an already committed release identity.
8. **One writer per surface:** serialized updater writes the release branch, release bot merges release PRs, serialized publisher tags/publishes. Neither updater nor publisher directly pushes changelog commits to protected `main`.

## Inputs and decisions

- Preserve the existing SDLC Python `sdlc changelog check|plan|finalize` implementation and `.changes/*.json` input schema (`type`, `semver`, `summary`). PR fast checks validate fragments. No fragments means no release; CI/docs/internal-only changes normally omit fragments.
- One release covers *all* unreleased fragments present in the selected `main` snapshot, regardless of the number of feature PRs or Mergify batches. Aggregate SemVer by strongest fragment intent (major > minor > patch; patch default for release-worthy fragments).
- Version sequence derives from the latest **committed release manifest**, including a merged release awaiting publication; never allocate a later version while an earlier committed release remains unpublished or unreconciled.
- `CHANGELOG.md` and release/version metadata are generated deterministically from the exact fragment blobs in that snapshot. The release commit removes only those consumed fragments; their contents remain recoverable from Git history.
- The release manifest (proposed `.sdlc/release.json`) must record schema version, SemVer, prior released boundary, source `main` SHA, ordered fragment path/blob-SHA list, changelog digest, generated-tree identity, and publication inputs. Its authority begins **only after** its release commit merges. An unmerged candidate is replaceable.

## State machine and workflows

### A. Reconcile / prepare (eventually consistent; replaceable)

Trigger on every `push` to `main`, plus `workflow_dispatch` for repair. The event is only a wake-up signal: always fetch fresh `origin/main` rather than assuming `github.sha` remains HEAD. Serialize updates per repository; a newer updater may supersede an incomplete older one. Rerunning is safe.

1. Before planning a new release, discover any merged release manifest lacking a completed tag/publication. Hand it to the publisher/recovery path; do not allocate a new version against an incomplete predecessor.
2. Read the committed previous-release boundary and current `origin/main`; if no unreleased fragments exist, ensure no obsolete open release candidate is treated as publishable and stop.
3. Snapshot the current `main` SHA; compile `sdlc changelog plan` and `finalize` in a clean worktree rooted at that SHA. Generate `CHANGELOG.md`, applicable version files and `.sdlc/release.json`, remove consumed fragments, create **one generated commit** atop that exact snapshot.
4. Recheck the baseline/source before pushing; replace `sdlc/release-next` using `--force-with-lease` (never force-push `main`). On lease conflict retry from current refs. Upsert exactly one open PR, labeled/identified as bot-owned release work, using a GitHub App installation token.
5. Ordinary feature PR merges may happen at any point. A later wake-up regenerates the same PR against the latest `main` and supersedes its old head. If a release PR has already merged, the updater must open a fresh candidate only for subsequent changes.

### B. Release validation and expedited merge

The release PR is kept **out** of Mergify by explicit head/label exclusions and by omitting `integration:auto` / `integration:review`. Never enqueue a release PR in the normal queue. The release lane may use an independent merge worker but never weaken normal feature gates.

- A stable `sdlc / release-gate` check identifies release PRs by repository-owned branch **and authenticated bot identity**, not by a user-writable label alone. For a normal PR this check passes without imposing branch freshness; for a release PR it must:
  - fetch live `main` and release head; verify that the generated commit's **parent** equals the current `main` SHA;
  - rerun deterministic generation from the recorded snapshot and fragment blobs, validating `CHANGELOG.md`, version, fragment deletion, manifest and allowed file paths;
  - require normal applicable CI/required checks to have passed for this exact release head.
- Configure the check as an applicable required context **without** enabling strict/up-to-date status checks for the repository. If made globally required, its non-release path must always report a stable, unambiguous success. The check also needs to be refreshed/invalidated when `main` advances: a green PR-head check from yesterday does **not** prove its base is current today.
- The release bot alone performs release auto-merges. Immediately before merge it fetches `main` and PR head, verifies the parent/base match and successful checks, then calls GitHub's PR merge API with the expected **head SHA** using `squash`. Do not rely only on GitHub auto-merge or a historic green PR check.
- A merge into `main` by another PR may race with the final API call. GitHub's documented merge API provides a head-SHA precondition, **not an atomic expected-base-SHA precondition**. Therefore the pre-merge check is necessary but not sufficient as a transaction boundary. The independent post-merge publisher check below is mandatory and the design must not promise that a stale release commit can never land.
- If base changes, fail the release gate / decline the bot merge, regenerate `sdlc/release-next` and revalidate. Do not enable the globally strict branch-protection option to solve this.

### C. Freeze, build and publish (not preemptable)

Trigger from the **merged release PR** / observed release manifest on `main`, and support manual retry. Serialize by repository and release version; no cancellation after commitment.

1. Discover the **actual squash-merged release commit** (never assume it is the PR branch head). Verify the commit is on `main`, its first parent equals the manifest's source `main` SHA, the release commit's resulting tree corresponds to the expected generated changes, and the fragment boundary/version/changelog are complete and non-duplicated. A mismatch is a **hard stop before tagging/publishing**. Reconciliation must repair this, rather than silently widening the release range.
2. Freeze the manifest against this merged commit; build all release artifacts **from this exact immutable commit**. Persist durable artifact IDs/digests keyed by release commit and version. Nix/Hestia caches speed rebuilding but are not publication records.
3. With serialized version allocation, create an immutable version tag pointing to the verified merged commit, then publish its changelog and previously built artifacts. Existing matching tag and artifact digests mean resume, not re-create or rebuild. Conflicting tags, missing artifacts, unexpected digests or mutated history fail closed.
4. Distinguish a committed-but-unpublished release from unreleased feature changes. `main` can continue receiving ordinary feature merges while publication finishes, but a *new* release may not consume the predecessor's state until its identity is reconciled.
5. A release PR's own merge removes the consumed fragments, so the `push main` handler must not interpret that release commit as a new feature release.

## Credentials, policy and triggers

- The GitHub App token used to create/update release branches/PRs must have the minimal necessary contents and pull-request permissions; publishing needs minimal tag/Release write permissions. If updating commit statuses/checks is required to invalidate stale release heads, scope that permission explicitly.
- Do **not** use `GITHUB_TOKEN` to create the release PR if unattended `pull_request` CI must execute: GitHub can require manual workflow approval for PRs created/updated with `GITHUB_TOKEN`. A dedicated App token avoids that trigger limitation.
- Preserve protected `main`, linear/squash history, existing non-strict required checks and Mergify candidate validation. Do not add a global strict freshness rule, bypass normal required checks, or give the release bot direct-push rights to `main`.
- Release PRs have no human-review requirement; if existing repo policy demands reviews universally, reconcile that explicitly before enabling automatic release merges. Labels alone do not waive GitHub rulesets.
- Keep stable CI context names for feature PRs. Release jobs must expose an auditable `sdlc / release-gate` result. All update, merge, publish and recovery operations log the PR, baseline, proposed version, exact SHA and artifact digests.

## Failure and recovery requirements

| Event | Required behavior |
| --- | --- |
| Multiple feature PRs or a Mergify batch merge while release PR open | Regenerate one rolling candidate against latest `main`; consume the superset; no waiting interval |
| Repeated updater triggers / lease conflict | Deduplicate or retry safely; do not create competing release PRs |
| Feature merge occurs after release validation | Invalidate/recheck release gate; bot must not intentionally merge stale head |
| Feature merge races the final release merge call | Publisher checks actual merged commit's parent/manifest; fail closed without tag or publication and schedule repair |
| Release merge invalidates a Mergify speculative batch | Let Mergify reset/revalidate; proactive cancellation is **follow-on**, not a prerequisite |
| Release PR merges; artifact build fails | Keep committed release identity pending; resume same source/version on retry |
| Tag or release already exists | Verify exact tag SHA and artifact digests; resume idempotently; conflicting identity fails |
| No release fragments | No version bump/release; do not generate a no-op release PR |
| Bot-created PR CI does not trigger | Fail closed and report misconfigured App/permissions; no bypass |
| GitHub event loss or interrupted worker | Periodic/manual reconcile scans Git refs, open PR and committed manifests; never rely solely on individual webhooks |

## Acceptance criteria / canary

1. Ordinary PRs still enter the Mergify queue; release PRs never do. `main` retains non-strict status-check freshness and squash/linear history. No broad branch-protection changes.
2. Two sequential feature merges produce one regenerated rolling release PR incorporating both fragments. A third merge during release CI invalidates/regenerates the branch; no delay is imposed.
3. Release gate is green only when candidate parent equals current `main` and generation is deterministic. It does not impose up-to-date requirements on normal PRs.
4. A merged release PR has exactly one generated release commit, with the corresponding `CHANGELOG.md`, version metadata and consumed fragments; publisher builds/tags/releases that exact merged SHA.
5. Reproduce a base-update race **after** release gate success and before the merge API call; no stale or mismatched commit may be tagged or published. Document any stale commit that can land and its automated recovery path.
6. Interrupt artifact build and publication separately; retries reuse durable completed artifacts, preserve immutable version/tag identity, and never double publish.
7. Merge a release while Mergify candidate checks are running; the queue is allowed to reset and correct checks rerun. **Do not** hold release implementation for cancellation optimizations.
8. Dogfood in `blogle/sdlc`: run a real release PR, merge, build, tag, publish, verify changelog and successful idempotent retry. Only then enable the reusable release workflow in other repos.

## Implementation sequence

1. Adapt existing changelog tooling to generate a deterministic rolling snapshot manifest from all unreleased fragments; add unit tests for coalescing/version selection.
2. Implement release updater workflow (App auth, one branch/PR, lease-aware regeneration, reconciliation/recovery).
3. Implement release-only freshness gate and independent auto-merge worker, preserving non-strict global protection and Mergify exclusions. Verify live GitHub status/merge semantics before enabling.
4. Implement post-merge identity validator and non-cancelable idempotent builder/tag/publisher. Wire consumer `just release-publish <version>` hook without inventing a registry abstraction.
5. Replace the current direct-push `release.yml` flow and adapt SDLC self-release work in PR #13. Complete canary before rollout.
6. **Separate follow-on:** cancel obsolete Mergify candidate CI on external release merges and improve cache reuse.

## References

- [SDLC issue #9](https://github.com/blogle/sdlc/issues/9) — previous release-finalization proposal to replace
- [SDLC PR #13](https://github.com/blogle/sdlc/pull/13) — existing self-release scaffolding to adapt
- [GitHub required checks: strict versus loose](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [GitHub pull-request merge REST API (head-SHA precondition)](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request)
- [GitHub Actions token and generated PR events](https://docs.github.com/en/actions/concepts/security/github_token)
