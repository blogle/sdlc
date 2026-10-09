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
  "schema": 1,
  "version": "1.2.3",
  "source_main_sha": "<40 lowercase hex characters>",
  "fragments": [{"path": ".changes/topic.json", "blob_sha": "<blob SHA>"}],
  "changelog_sha256": "<sha256 hex>",
  "generated_tree": "<commit tree SHA>",
  "publication": {
    "artifacts": [{"name": "package", "path": "dist/package", "digest": "sha256:<hex>"}],
    "build_command": "consumer-owned command that builds the listed paths"
  }
}
```

`fragments` are sorted and describe the exact blobs consumed by the generated
commit. `publication.artifacts` is a list of immutable output names and paths;
the optional digest is checked when supplied. The publisher runs the recorded
build command once from the merged SHA, computes missing digests, and records
them before tagging. The consumer's existing `just release-publish <version>`
hook then promotes/releases those recorded bytes without rebuilding or
inventing a registry interface.

## Publisher guarantees

- The actual merged commit must be on `main`, have exactly one parent, and that
  parent must equal `source_main_sha`.
- The manifest tree, changelog digest, fragment deletion, version, and artifact
  declarations are checked before any tag or release write.
- `.sdlc/release-ledger.json` is atomically replaced after each transition and
  uploaded as a durable, release-keyed Actions artifact. A retry may resume a
  build, tag, or publication, but cannot change a recorded digest or release
  identity.
- An existing `v<version>` tag is accepted only when it points to the exact
  merged SHA. A conflict is a hard failure. Tagging uses the merged SHA, never
  the workflow event SHA or the replaceable release branch.
- The workflow has non-cancelable per-repository serialization. It is a
  reusable workflow with no `push` trigger in this PR and defaults to
  `activate: false`, so no live release is activated before the updater and
  release gate are deployed.

The updater may add fields, but it must preserve these fields and semantics.
If it cannot provide the manifest or a buildable artifact declaration, the
publisher fails closed rather than guessing a source range or rebuilding a
different release.
