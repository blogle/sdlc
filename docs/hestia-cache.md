# Hestia cache behavior in SDLC

## Why identical derivations rebuilt across pull requests

Nix deduplicates by derivation and store-output identity. GitHub-hosted runners
start with an empty local Nix store, so the outputs must be available from a
substituter. Hestia uses the **repository's GitHub Actions cache**; it is not a
persistent, cross-repository Nix cache.

GitHub cache access scopes are important:

- A PR can read cache entries created on the repository's default branch.
- A PR can write only to its own PR cache scope, not the default-branch scope.
- Another PR (including Mergify's separate merge-queue PR) cannot read that
  first PR's cache entries.

Consequently, repeatedly building the same .drv from unrelated PRs is
expected **until default-branch CI has published the output**. This is not
derivation invalidation. The shared cache must be warmed from the default
branch after merge. GitHub's cache service is still lossy and quota-limited.

## Shared SDLC configuration

The stage workflow enables Hestia upstream-cache filtering in both evaluation
and build jobs, recognizing outputs signed by cache.nixos.org and
nix-community.cachix.org. The evaluator additionally sets
filter-drv-closures: true; the existing hestia prefetch step in each build job
is mandatory when that filter is enabled. This avoids re-uploading signed
third-party closure members wherever possible. Source derivations and
locally built outputs are still eligible for Hestia. The checks remain ordinary
Nix derivations; this change does not introduce custom file/target dependency
tracking or disable inexpensive Nix-backed checks.

No Hestia setting can make PR writes visible to other PRs. SDLC itself warms
its default-branch cache on main/master pushes. **Consumers must wire their
own default-branch push trigger**, because a reusable workflow cannot add
triggers to its caller.

For a Mergify-based consumer, add the following to the caller workflow (using
the same pinned SDLC version as its other workflow calls):

~~~yaml
on:
  pull_request:
  push:
    branches: [main, master]  # include your actual default branch

jobs:
  # Existing PR fast, candidate, and required-gate jobs remain intact.
  cache-warm:
    if: github.event_name == 'push' && github.ref == format('refs/heads/{0}', github.event.repository.default_branch)
    uses: blogle/sdlc/.github/workflows/candidate.yml@<PINNED_SDLC_RELEASE>
~~~

The push job should not be used as a required PR status context. It rebuilds
the candidate stage on the **merged** default-branch commit, thereby
publishing entries that later PRs can substitute. Since GitHub forbids
promoting PR-scoped cache entries directly into the default scope, the first
post-merge run may rebuild previously tested outputs. Later PRs benefit.
There is no need to rerun all other CI jobs on default-branch pushes just to
warm the cache; only the candidate stage must run.

SDLC's minimal and clean-room examples demonstrate the push job. Their
example workflow release pins are independent examples; consuming repos
must update to the SDLC release containing this change.

## Garbage collection

Hestia's repository-scoped cache has a GitHub storage quota and can evict
entries. It recommends scheduled default-branch GC. SDLC provides a reusable
hestia-gc.yml workflow with a read-only dry run, default-branch guard,
serialized GC runs, and the actions:write permission needed to delete cache
entries. SDLC itself runs GC daily.

Each consuming repository can opt in with its own scheduled caller:

~~~yaml
name: Hestia GC
on:
  schedule:
    - cron: "23 3 * * *"
  workflow_dispatch:
    inputs:
      dry_run:
        type: boolean
        default: false
permissions:
  contents: read
  actions: write
jobs:
  gc:
    uses: blogle/sdlc/.github/workflows/hestia-gc.yml@<PINNED_SDLC_RELEASE>
    with:
      dry_run: ${{ inputs.dry_run || false }}
~~~

Schedules run against the repository's default branch. Do not invoke GC from
untrusted PR events. The reusable workflow separately checks the default
branch before doing anything.

## Verification

1. Run the normal library validation via nix develop -c just check.
2. In a consumer that has adopted the new pinned release, merge a change and
   confirm its post-merge cache-warm job finishes on the default branch.
3. Inspect the Hestia drain logs: upstream-signed packages should no longer
   dominate uploads, and default-branch manifests should be present.
4. Open a separate PR that uses unchanged derivations. The Hestia matrix
   should omit cached targets; inspect build logs for any remaining misses.
5. If misses persist, compare exact .drv paths, store outputs, Hestia cache
   scope/manifest lookups, and evictions. Same path but rebuilding after a
   completed default-branch warm is a cache-availability failure.

These changes do **not** share artifacts between repositories, or make the
GitHub Actions cache durable. A cross-repository or developer-machine cache
still requires a separate substituter such as Attic/Cachix.
