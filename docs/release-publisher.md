# Release Publisher Contract

This is the publisher-side contract for the rolling release design in
`docs/rolling-release-pr.md`. Canonical protection is active on `blogle/sdlc`
as ruleset [24821903](https://github.com/blogle/sdlc/rules/24821903), with
squash-only, `sdlc / pr-fast`, non-strict freshness, and no bypass actors. The
publisher remains inactive until the updater, release gate, and credentials
are provisioned; the release-gate check must be added to required checks only
in that activation change.

## Manifest

The updater writes `.sdlc/release.json` in the replaceable release PR. The
manifest is authoritative only after its generated commit is squash-merged to
`main` and must contain:

```json
{
  "schemaVersion": 1,
  "version": "1.2.3",
  "prior_released_boundary": "<prior source SHA or null>",
  "source_main_sha": "<40 lowercase hex characters>",
  "fragments": [{"path": ".changes/topic.json", "blob_sha": "<blob SHA>"}],
  "changelog_sha256": "<sha256 hex>",
  "generated_tree_sha256": "<deterministic generated-file digest>",
  "publication": {"version": "1.2.3", "source_main_sha": "<same source SHA>"}
}
```

`fragments` are sorted and describe the exact blobs consumed by the generated
commit. `generated_tree_sha256` is deliberately not a Git tree SHA: the Git
tree contains `.sdlc/release.json`, so embedding its Git tree identity would be
self-referential. The generator computes the digest over the sorted generated
file set, currently `CHANGELOG.md`, as `path + NUL + bytes`; the publisher
reconstructs that digest from the merged commit and independently verifies the
complete source-to-merged tree diff. `publication` is metadata only and is not
an artifact DSL. The publisher creates one standard source archive from the
exact merged commit and stores it as a GitHub Release asset.

## Publisher guarantees

- The actual merged commit must be on `main`, have exactly one parent, and that
  parent must equal `source_main_sha`.
- The manifest tree, changelog digest, fragment deletion, version, and
  publication metadata are checked before any tag or release write.
- `.sdlc/release-ledger.json` is atomically replaced as an audit record. The
  actual bytes are stored durably in a draft GitHub Release asset named with
  the version and exact merged SHA. A retry reads and verifies that asset
  before building or uploading; it never relies on a 90-day Actions artifact
  and never overwrites an existing asset.
- An existing `v<version>` tag is accepted only when it points to the exact
  merged SHA. A conflict is a hard failure. Tagging uses the merged SHA, never
  the workflow event SHA or the replaceable release branch.
- The workflow has non-cancelable per-repository serialization. It is a
  reusable workflow with no `push` trigger in this PR and defaults to
  `activate: false`, so no live release is activated before the updater and
  release gate are deployed.

The updater may add fields, but it must preserve these fields and semantics.
The release-gate worker must use the same `schemaVersion`, digest names, and
publication shape. If the manifest is absent or differs from the actual
merged tree, the publisher fails closed rather than guessing a source range or
rebuilding a different release.
