# Authoring SDLC changelog fragments

Every release-worthy change should introduce a JSON file under `.changes/`.
Use a descriptive, unique filename so concurrent PRs do not collide:

```json
{
  "type": "fix",
  "semver": "patch",
  "summary": "Preserve exact changelog bytes when verifying release candidates"
}
```

The three required fields are `type`, `semver`, and `summary`.
`type` is the displayed changelog category (for example `fix`, `feature`,
`docs`, or `test`); `semver` must be `patch`, `minor`, or `major`.
Write the summary for readers of the published release.

Run `nix develop -c just changelog check` to validate fragments and
`nix develop -c just changelog plan` to preview the intended entries.
The preview does **not** publish or consume fragments.

Merge ordinary PRs through the repository's configured CI and Mergify queue.
After a source merge, the SDLC release bot aggregates **all outstanding
fragments** in sorted filename order, chooses the strongest requested semver
bump, and generates a rolling release candidate PR. The release candidate
prepends one dated section to the existing `CHANGELOG.md` and deletes exactly
the fragment files it consumed. Existing release history is retained.
The candidate is independently validated against its source commit before
the release bot squash-merges and publishes the exact resulting commit.

Do not manually edit generated release manifests or consume fragments in an
ordinary feature PR. Publication and a successful version tag must complete
before a successor can be allocated.
