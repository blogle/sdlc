# Release Publisher Contract

This is the publisher-side contract for the rolling-release architecture in
[release-manifest.md](release-manifest.md). Canonical protection is active on
`blogle/sdlc` as ruleset
[24821903](https://github.com/blogle/sdlc/rules/24821903), with squash-only
merges, required `sdlc / pr-fast`, non-strict freshness, and no bypass actors.

The release GitHub App is `sdlc-release[bot]`. Actions reads its credentials
from `SDLC_RELEASE_APP_ID` and `SDLC_RELEASE_APP_PRIVATE_KEY`, and verifies
the App slug against `vars.SDLC_RELEASE_BOT_LOGIN`. Publication is controlled
by `vars.SDLC_RELEASE_ACTIVATE`; `sdlc onboard --repo OWNER/NAME` initializes
it to `false`, while `sdlc release enable --repo OWNER/NAME` is the explicit,
fail-closed operator action after preflight. `sdlc release disable` is the
emergency stop. The status command is read-only and never displays secret
values. App installation access and permissions can be verified/provisioned
from secure local credentials without printing private material.

## Automatic workflow

1. A feature PR with a valid `.changes/*.json` fragment passes
   `sdlc / pr-fast` and enters the normal Mergify queue
   (`integration:auto`). Only Mergify merges feature PRs.
2. A `push` to `main` runs `self-release.yml`, which calls the shared
   `release.yml` and `release-reconcile.yml`. The reconciler first proves
   the previous tagged release has a published, digest-identified artifact.
   It then opens or replaces `sdlc/release-next` as the release App.
3. The release PR is excluded from Mergify. Its ordinary PR CI and the
   independent `sdlc / release-gate` verify exact candidate bytes, a
   deterministic replay, App identity, and the live source parent. After CI
   completes, the trusted gate workflow revalidates the exact release head.
4. The trusted release merger rechecks the required head checks and live
   `main`, then squash-merges through the GitHub API with the expected head
   SHA. It dispatches `release.yml` with the *actual* merge commit SHA and
   `activate` from `vars.SDLC_RELEASE_ACTIVATE`.
5. The publisher verifies this identity, prepares one source archive,
   creates or verifies the exact annotated version tag, persists immutable
   artifact bytes in a GitHub Release, and finalizes the non-draft release
   through the consumer-owned `just release-publish <version>` hook.

No human review or branch-protection bypass is part of the release path.
A failed prerequisite blocks the release; it must not be represented as
successful publication.

## Manifest

The updater writes `.sdlc/release.json` in the replaceable release PR. The
manifest is authoritative only after its generated commit is squash-merged to
`main` and must contain:

```json
{
  "schema": 1,
  "version": "1.2.3",
  "prior_released_boundary": "<prior source SHA or null>",
  "source_main_sha": "<40 lowercase hex characters>",
  "fragments": [{"path": ".changes/topic.json", "blob_sha": "<blob SHA>"}],
  "changelog_sha256": "<sha256 hex>",
  "generated_tree": "<deterministic generated-file digest>",
  "publication": {"version": "1.2.3", "source_main_sha": "<same source SHA>"}
}
```

`fragments` are sorted and describe the exact blobs consumed by the generated
commit. `generated_tree` is deliberately not a Git tree SHA: the Git tree
contains `.sdlc/release.json`, so embedding its Git tree identity would be
self-referential. The generator computes the digest over sorted records
`path NUL kind NUL content NUL`, containing `CHANGELOG.md/file/<bytes>`
and each consumed fragment `path/deleted/<source blob SHA ASCII>`. The
manifest is excluded. The publisher and gate call the exported coordinator
verifier and independently check the complete source-to-merged tree diff.
`publication` is identity metadata only, not an artifact DSL.

## Publisher guarantees and recovery

- The merged commit must be on `main`, have exactly one parent, and that
  parent must equal `source_main_sha`.
- The manifest tree, changelog digest, fragment deletion, version, and
  publication metadata are checked before any tag or release write.
- `.sdlc/release-ledger.json` is atomically replaced as an audit record.
  Durable bytes are stored as an asset named with the version and exact
  merged SHA. On retry the publisher downloads and verifies an existing
  asset; it never relies on a 90-day Actions artifact or overwrites bytes.
- An existing `v<version>` annotated tag is accepted only if it resolves
  to the exact merged SHA. A conflicting tag is a hard failure.
- Publication is serialized per repository without canceling previous work.
  There is no `push` trigger in the reusable publisher; the trusted merger
  dispatches it after the release PR merges.

After a transient failure, inspect the release-merge and publisher Actions
runs, identify the **actual squash-merged release commit**, and only then
retry the publisher with that SHA and `activate=true`. Do not substitute
the release PR's replaceable head SHA, the workflow event SHA, or current
`main`. A successful retry must retain the existing release asset ID and
SHA-256 digest, not silently replace an existing artifact.

The first SDLC release, [v1.2.0](https://github.com/blogle/sdlc/releases/tag/v1.2.0),
required a manual recovery dispatch after publisher hotfixes. The successor
release must prove the full path from normal Mergify feature merge through
automatic tagging and publication with **no manual dispatch**.
