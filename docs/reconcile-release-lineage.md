# Recovering a failed predecessor lineage check

`release reconcile` treats the committed `.sdlc/release.json` as the
predecessor record. Before it creates or updates a release candidate, it
requires all of the following:

1. `v<version>` exists.
2. The tag commit's parent is exactly `source_main_sha`.
3. The tagged manifest matches `main`.
4. The tag is reachable from the default branch.
5. The GitHub release is final and has the immutable source-archive identity.

If the tag parent and `source_main_sha` disagree, or the histories are
divergent, do not retag, force-push `main`, replace the tag, or overwrite the
published release asset. Those actions would destroy the identity that the
predecessor checks protect.

The reviewed recovery route is:

1. Preserve the existing tag, release, and source archive as evidence.
2. Record the tag commit, its parent, the committed manifest, and the default
   branch tip; confirm the histories and compare result with an operator.
3. Restore the consumer's intended history through the normal reviewed Git
   workflow, or retain the historical line and prepare a new patch release.
4. Re-run reconciliation only after the manifest, tag parent, manifest bytes,
   default-branch ancestry, publication state, and immutable asset identity
   all agree. A failed check must continue to produce no candidate and no
   release-PR mutation.

This procedure deliberately contains no production GitHub writes or tag
recovery automation.
