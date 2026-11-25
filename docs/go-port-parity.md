# Go rewrite parity matrix

The Python implementation now lives under `tests/fixtures/legacy/src` solely as
an oracle. Nix packages and executes `cmd/sdlc` built with `buildGoModule`; the
Python oracle is only on development/test paths. This matrix names Go-side tests
that execute the implementation rather than counting the oracle suite as Go
coverage.

| Legacy contract / invariant | Go implementation | Executed verification |
| --- | --- | --- |
| Fragment path validation, stable ordering, strongest consumer SemVer intent | `internal/changelog` | `TestReadFragmentsSortsAndRejectsInvalidInput`, `TestNextVersionPreservesConsumerBumpSemantics`, `TestNextVersionDoesNotOverflowValidSemVerComponents` |
| Changelog bytes, existing history, header without newline, fragment consumption | `internal/changelog`, Cobra `changelog` | `TestFinalizeBytesMatchesLegacyOutput`, `TestFinalizeHeaderWithoutTrailingNewlineIsNotDuplicated`, `TestFragmentFlagAcceptsNargsStyleSelectionsAndEmptySelection` |
| Snapshot manifest schema 1, changelog SHA256, generated-tree digest, blob bindings, release identity output bytes | `internal/release`, `internal/git` | `TestSnapshotGeneratesLegacyManifestAndConsumesFragments`, `TestValidateIdentityBindsActualSquashTree`, `TestManifestDecodeRetainsUnrecognizedFieldsForReplayRejection`, `test_snapshot_bytes_and_release_identity_match_python_oracle` |
| Pinned caller templates, Mergify copy, Renovate preset, native JSON5 untouched, lock revision | `internal/consumer` | `TestInstallAndCheckPreserveNativeJSON5`, `test_consumer_adapters_match_python_and_leave_json5_untouched` |
| Ruleset policy projection, timestamps, current redacted-actor attestation, create/check/idempotency | `internal/ruleset` service, `internal/github` adapter, thin Cobra handler | `TestSameTimestampComparesInstants`, `TestMatchesIgnoresGitHubMetadataButRequiresExactPolicy`, `TestMatchesOnlyAcceptsRedactedBypassWhenExplicit`, `TestRulesetHandlersPlanCheckAndApplyCanonicalPolicies` |
| Paginated exact-head checks, duplicate/missing metadata, App binding, latest failure, trusted bot, stale base/head and historical recovery | `internal/release.Service`, `internal/github` adapter, thin Cobra handler | `TestRequiredCheckUsesLatestExactHeadAndPublisher`, `TestVerifyReleaseGateRequiresTrustedBotCurrentParentAndGateChecks`, `TestCheckAPIConsumesAllPagesAndRejectsDuplicates`, `TestAuthenticatedAppRequiresMatchingBotAndInstallationRepository`, `TestGoReleaseGateAndMergeHandlersEnforceGitHubEvidenceBeforeMutation` (successful current gate, successful squash merge, historical recovery; rejects wrong App, newest failure, human author, stale head/base, and stale redacted-bypass attestation before any `PUT`) |
| Squash-tree identity, ordered fragments, changelog and generated-tree digests | `internal/git`, `internal/release` | `TestValidateIdentityBindsActualSquashTree`, `test_snapshot_bytes_and_release_identity_match_python_oracle` |
| Atomic ledger resume, conflict refusal, tag/receipt publication ordering, exact ordered receipt identity | `internal/release`, `internal/git`, Cobra ledger/receipt handlers | `TestLedgerRetryAndPublicationProofs`, `TestReceiptsRequireExactIdentityAndUniqueArtifacts`, `TestLoadLedgerRejectsReorderedIdentityFragments`, `TestPublisherCommandsResumeReceiptsAndRefuseIdentityConflicts` |
| Pinned consumer `ci.publish` app evaluation and identity validation before Nix effects | `internal/nix`, Cobra `release invoke` | `TestInvokeCommandValidatesIdentityBeforeCallingNixAndRunsPublishApp` |
| Candidate replay, historical recovery, squash merge checks and no GitHub mutation on failed authorization | `internal/release.Service`, `internal/git`, `internal/github`, thin Cobra handler | `TestGoReleaseGateAndMergeHandlersEnforceGitHubEvidenceBeforeMutation` invokes `release gate`/`release merge`: validates current and historical candidates, exact squash SHA, and rejects wrong App, newest failure, human author, stale head/base and stale redacted-bypass attestation before any merge `PUT` |
| No-fragment retirement, predecessor publication barrier, deterministic candidate push, force-with-lease retry/idempotency, release PR create/edit lifecycle | `internal/release.Reconcile` flow with Git/GitHub adapters | `TestReconcileCommandRetiresNoFragmentsAndBlocksUnpublishedPredecessor`, `TestReconcileCommandPushesCandidateAndMaintainsReleasePR`, `TestReconcileSnapshotPushesReplaceableCandidateWithLeaseAndIsIdempotent` |

The Python tests still verify the frozen oracle and unchanged workflow/Nix
consumer contract. `nix develop -c just check` also runs Go tests, vet,
formatting, exhaustive lint, the Python suite, actionlint, Renovate/Mergify
validation, and Nix contract checks.
