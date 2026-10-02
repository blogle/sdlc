# Downstream contract v1

`ci.nix.json` is a JSON subset of a Nix attrset: static data only, no eval-time code. Root keys are `schemaVersion: 1`, optional `integration` (`integration:auto` or `integration:review`), and `stages`. Stage keys are `pr-fast`, `candidate`, `release`; each stage may specify `targets` (strings passed to `nix build`) and `commands` (arrays of argv strings executed in order). Unknown schema/stages, malformed targets, and missing requested stages fail closed. No language or framework assumptions are made.

The shared runner is an interpreter, not project build logic. Targets produce/cache artifacts in the project flake. A later publication command should refer to candidate-produced artifacts/identities rather than rebuild them. Commands are intentionally a narrow escape hatch.

Reusable workflows are called at a compatible major ref (`@v1`) and accept `sdlc_ref` to select the matching flake runner. The major channel may advance compatible implementation fixes. Consumers can instead pin an immutable commit or release tag. Workflows centralize Nix installation and the Hestia Cachix cache; cache credentials are optional for reads/builds.

Integration authorization has one stable check name: `sdlc-integration-authorized`. Its producer verifies policy and current pull-request head SHA and reports success against that SHA only. A changed head must not retain authorization. Comments are not an authorization transport. Missing policy, unknown policy, absent check, or stale-head attestation is a denial.

Mergify batches candidates independently of stack relationships. The configured squash method makes each PR a single logical commit on `main`; queue testing does not demand that contributor branches be rebased onto main. Candidate-required checks belong on Mergify's synthetic candidate, while PR admission stays cheap.
