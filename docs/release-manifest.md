# Rolling release manifest

The single source-bound manifest is `.sdlc/release.json`:

```json
{
  "version": "1.4.0",
  "source_sha": "<default-branch commit>",
  "fragments_sha256": "<digest of sorted fragment names and bytes>"
}
```

The updater computes the manifest, changelog, and consumed fragments from one
default-branch commit, then replaces the existing `sdlc/release-next` branch.
The gate repeats generation and compares the entire expected changelog and
source-to-candidate diff. The manifest is authoritative only after its squash
commit lands; that actual merged SHA is the release identity.

The release App publishes a version tag and source archive from the merged
commit. The consumer publication app publishes its own immutable version/SHA
artifacts and verifies digests on retries. A successor waits until publication
of the prior release is complete.
