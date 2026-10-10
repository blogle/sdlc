# Consumer protocol v1

## Pinned inputs

Pin a concrete SDLC release and use SDLC's default Nixpkgs node:

```nix
inputs.sdlc.url = "github:blogle/sdlc/v1.0.0";
inputs.nixpkgs.follows = "sdlc/nixpkgs";
```

Other project inputs follow consumer `nixpkgs`. A project that requires a different revision sets its own `nixpkgs.url` and `sdlc.inputs.nixpkgs.follows = "nixpkgs"`. The consumer lockfile pins adoption; updating SDLC is the deliberate fleet Nixpkgs rollout boundary. Same Nixpkgs can make derivation store paths identical, enabling local reuse/future shared-cache hits; Hestia remains repository-scoped.

## Nix stages and Hestia

`ci.nix` is static Nix data with `schemaVersion = 1` and `stages.pr-fast` / `stages.candidate` attrsets of derivations already defined by the consumer flake. `sdlc.lib.mkConsumer { contract; }` validates and directly aliases those derivation attrsets under `hydraJobs.<system>.ci-pr-fast` and `ci-candidate`. It does not wrap them in an app or scheduler. Native `meta.hestia.group` and `meta.hestia.os` values remain intact.

Reusable workflows call `Mic92/hestia/matrix@v3` with the consumer's stage `hydraJobs` installable. Nested calls use `uses: $/.github/workflows/stage.yml`, so they resolve at the exact caller workflow revision and the consumer's outer release pin atomically selects the whole SDLC workflow stack. Hestia uses `nix-eval-jobs` for native evaluation/fan-out, its own metadata for grouping/runner choice, and its GitHub Actions-local cache. SDLC does not implement a parallel matrix or cache interface. Consumers can make several direct Nix checks part of a stage; Hestia handles the rows. The fast/candidate workflow checks are named `sdlc / pr-fast` and `sdlc / candidate` for Mergify.

## Release, one rolling PR at a time

Ordinary squash-merged PRs contribute valid `.changes/*.json` fragments and never edit `CHANGELOG.md`. A single replaceable `sdlc/release-next` PR coalesces all current unreleased fragments into one deterministic snapshot. A merged release manifest becomes the next release boundary; it remains on `main` permanently.

The updater uses GitHub Actions concurrency `queue: max`, `cancel-in-progress: false`, a GitHub App token, one bot-owned branch, and `--force-with-lease`; it never pushes `main`. The manifest contract is documented in `docs/release-manifest.md`. Its `generated_tree` is a deterministic digest of explicitly defined generated-file records excluding the manifest, not a self-referential Git tree hash. After a manifest is merged, the updater uses its exact version tag and completed publication ledger to decide whether successor allocation is safe; manifest existence alone does not block a successor.

Fragment validation/aggregate SemVer/render/compaction remains the only changelog-specific shared code; the rolling snapshot reuses it. The consumer's `just release-publish <version>` publishes the GitHub Release or promotes candidate artifact identities. GitHub App contents-write and pull-request rights are required for reconciliation; publication remains separately serialized and fail-closed. No generic registry interface is defined.

For OCI/package-producing consumers, candidate CI publishes an immutable artifact identity (such as a digest) to the project's existing registry. The release recipe re-tags/promotes that exact identity. Hestia cache entries are neither durable release artifacts nor provenance.

## Review authority

GitHub rulesets are the authoritative protection layer: PR-only changes, squash-only merge, stale-approval dismissal, non-strict required-check freshness, stable `sdlc / pr-fast`, deletion prevention, and non-fast-forward prevention. Candidate is not a source-PR requirement. Workflow Automation is used only as a thin auto-enqueue trigger; Merge Queue owns queue admission, candidate batching/validation, and merge execution. Merge Protections remain disabled; supported GitHub protections are automatically included in queue behavior.

The canonical policy uses matching `pull_request_rules` and `queue_rules[].queue_conditions` for admission: base `main` (consumers adapt branch name), successful `sdlc / pr-fast`, and exactly one of auto label with review absent, or review label with auto absent plus `#approved-reviews-by >= 1`. The workflow rule enqueues the `validated candidates` queue; the queue's `merge_conditions` contains only successful `sdlc / candidate`. Queue command restrictions permit sender permission `write` or `anvil-daemon[bot]` to request queueing as an escape hatch; they do not authorize admission. Auto enters once admission is green without a comment/checkbox, while review remains blocked/pending until exact-head approval and all queue conditions pass. A comment/command is never authorization; labels, GitHub approval, and checks are. GitHub stale-review dismissal invalidates review authorization after a head change. Missing/conflicting labels fail closed.

## Renovate

`default.json` is the repo-hosted Renovate preset. Consumers use `{"extends":["github>blogle/sdlc#v1"]}`. It enables the beta Nix manager, groups the `sdlc` flake input and `blogle/sdlc` reusable workflow references, keeps explicit SemVer runtime tags, pins third-party actions, and does not automerge SDLC updates. Nix `depName` is the input name; `semver-coerced` handles `v1.x.y` on the `git-refs` datasource. Runtime input/workflow refs should use the same concrete `v1.x.y` release; the floating major is only for preset policy. Consumers run Renovate's native `renovate-config-validator`; preset validation is part of SDLC CI.

## Clean-room and self-hosting

CI copies `examples/clean-room` to a temporary directory and consumes SDLC from the remote PR commit via a flake input override. This proves there is no local source-tree reach-back. The root `justfile` is library development only. Downstream repos own their justfile, checks, `hydraJobs`, and publication hook.
