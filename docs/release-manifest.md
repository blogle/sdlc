# Rolling Release Manifest

The single manifest contract for the updater, release gate, and publisher is
`.sdlc/release.json`:

```json
{
  "schema": 1,
  "version": "1.2.3",
  "prior_released_boundary": "<previous source main SHA or null>",
  "source_main_sha": "<40 lowercase hex characters>",
  "fragments": [{"path": ".changes/topic.json", "blob_sha": "<blob SHA>"}],
  "changelog_sha256": "<sha256 hex>",
  "generated_tree": "<canonical generated-files digest>",
  "publication": {"version": "1.2.3", "source_main_sha": "<same source SHA>"}
}
```

`fragments` are sorted by path and bind each consumed fragment to the exact
blob in `source_main_sha`. `publication` only binds version and source
identity; it is not an artifact or build-command DSL. Consumers keep their
existing `just release-publish <version>` contract.

`generated_tree` is not a Git tree SHA. It is SHA-256 over sorted records of
the generated `CHANGELOG.md` bytes and each consumed-fragment deletion record:

```text
path NUL kind NUL content NUL
```

The deletion record content is the source fragment blob SHA. The manifest is
excluded to avoid self-reference. The release gate and publisher independently
reconstruct this digest from the actual merged commit, require that only the
manifest, changelog, and listed fragment deletions changed from the source,
and separately verify the actual merged commit tree contains those results.

The manifest becomes authoritative only after its candidate commit merges.
Successor allocation uses the latest manifest as the previous release boundary,
but blocks until its exact tag, version/SHA-keyed release asset digest, and
published (non-draft) GitHub Release are verified. Manifest existence or draft
release existence alone is not proof of publication.
