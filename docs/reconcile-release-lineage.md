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
3. Reconcile the consumer's recorded history through a normal, reviewed Git
   workflow only if the operator can establish which history is authoritative.
   The exact facts to reconcile are the tag commit
   `17b4640ff5b27d3282cef9ac9e9a384c223ee4e6`, its parent
   `dbd012db5dbefe2212e6d42534931f31ea0ee8da`, `source_main_sha`
   `e7800b33fb83a213dafd1b061acb15c4d8a8a7ba`, the tagged and current
   `.sdlc/release.json` bytes, the default-branch tip, and the compare result.
   A new patch release is not a bypass: this predecessor must pass before any
   successor candidate can be made.
4. For this observed combination of a tag-parent mismatch and disconnected
   history, SDLC has no supported automatic recovery path. Do not retry until
   the operator has resolved that ambiguity without changing the existing
   published identity. Re-run only after the manifest, tag parent, manifest
   bytes, default-branch ancestry, publication state, and immutable asset
   identity all agree. A failed check must continue to produce no candidate
   and no release-PR mutation.

This procedure deliberately contains no production GitHub writes or tag
recovery automation.

## Evidence for the Anvil incident

1. **Predecessor check:** `internal/cli/reconcile.go` gathers
   `v<version>`, its peeled commit and parent, the tagged manifest, default
   branch ancestry, and publication state; `internal/release/predecessor.go`
   rejects the exact `tagParent != source_main_sha` condition before snapshot
   generation and reports disconnected history when `merge-base` has no
   result. In Anvil, runs
   [45008926162](https://github.com/blogle/anvil/actions/runs/45008926162)
   and
   [45051502828](https://github.com/blogle/anvil/actions/runs/45051502828)
   report the same failure. The observed manifest source is
   `e7800b33fb83a213dafd1b061acb15c4d8a8a7ba`; `v0.1.0` peels to
   `17b4640ff5b27d3282cef9ac9e9a384c223ee4e6`, whose parent is
   `dbd012db5dbefe2212e6d42534931f31ea0ee8da`. The reported compare result
   is no common ancestor between the tag commit and `main`; this is an
   observation, not a reason to relax either predecessor check.
2. **Regression:** `internal/cli/reconcile_command_integration_test.go`,
   `TestReconcileCommandRetiresNoFragmentsAndBlocksUnpublishedPredecessor`
   models a mismatched parent and orphaned history with local Git and the
   fake `gh` adapter. It passed and asserts no candidate ref, no PR mutation,
   and no movement of `main`.
3. **Recovery and safety:** this document records the reviewed route above;
   no production GitHub write, tag replacement, publication, or live recovery
   was performed. The existing predecessor checks remain fail-closed.
4. **Verification:** the focused regression passed with
   `nix --extra-experimental-features 'nix-command flakes' develop -c go test
   ./internal/cli -run
   'TestReconcileCommand(RetiresNoFragmentsAndBlocksUnpublishedPredecessor|PushesCandidateAndMaintainsReleasePR)$'
   -count=1 -v`. `NIX_CONFIG='extra-experimental-features = nix-command flakes'
   nix develop -c just check` was also run, but is unverified: 84 of 85
   Python tests passed and the remaining self-consumer publisher test failed
   because its fixture's `gh` script could not execute `/usr/bin/env` inside
   this host's Nix shell. Go tests, vet, exhaustive lint, and formatting all
   passed.
