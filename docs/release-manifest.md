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
blob in `source_main_sha`. `publication` only binds release version and source
identity. Consumers publish only through the Nix app at
`ci.<system>.publish`; SDLC resolves that app from the merged consumer flake.
The hook returns immutable artifact receipts bound to the same release
identity. This contract adds no artifact/build DSL.

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
but blocks only when its exact version tag or completed publication ledger is
missing. Manifest existence alone is not an unpublished-state indicator.

## Audited retirement of an incomplete draft

If a predecessor is tagged but its GitHub Release remains a draft, a reviewed
commit on the protected default branch may add exactly
`.sdlc/abandoned-releases/<version>.json` for that predecessor:

```json
{
  "schema": 1,
  "version": "0.1.0",
  "source_main_sha": "<40 lowercase hex characters>",
  "merged_sha": "<40 lowercase hex characters>",
  "reason": "Why this exact draft cannot be completed"
}
```

The reason must contain non-whitespace text and be at most 500 characters.
No extra fields are accepted. Reconciliation and the trusted successor gate
read the committed marker on current main, check the tag's peeled commit and
parent, require byte-for-byte agreement of the tagged and current predecessor
manifests, prove the tag is an ancestor of current main, and query the live
GitHub Release to confirm it is still draft. A missing or invalid marker
continues to block a draft predecessor. Published predecessors follow the
ordinary complete-publication path. Historical merge recovery and publication
refuse the exact retired identity; the tag, draft Release, and any source
assets remain untouched. The successor version advances from the predecessor
manifest (for example, a patch after 0.1.0 is 0.1.1).
