#!/usr/bin/env bash
set -euo pipefail

: "${SDLC_RELEASE_VERSION:?release workflow must set SDLC_RELEASE_VERSION}"
: "${SDLC_RELEASE_MERGED_SHA:?release workflow must set SDLC_RELEASE_MERGED_SHA}"
: "${SDLC_RELEASE_SOURCE_SHA:?release workflow must set SDLC_RELEASE_SOURCE_SHA}"
: "${SDLC_RELEASE_RECEIPTS:?release workflow must set SDLC_RELEASE_RECEIPTS}"

test "$(git rev-parse HEAD)" = "$SDLC_RELEASE_MERGED_SHA"
source_sha="$(jq -r .source_main_sha .sdlc/release.json)"
test "$source_sha" = "$SDLC_RELEASE_SOURCE_SHA"

asset="sdlc-source-${SDLC_RELEASE_VERSION}-${SDLC_RELEASE_MERGED_SHA}.tar.gz"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
git archive --format=tar.gz --output "$temporary/$asset.expected" "$SDLC_RELEASE_MERGED_SHA"
gh release download "v$SDLC_RELEASE_VERSION" --pattern "$asset" --dir "$temporary"
cmp -- "$temporary/$asset.expected" "$temporary/$asset"
digest="$(sha256sum "$temporary/$asset" | cut -d' ' -f1)"

mkdir -p "$(dirname "$SDLC_RELEASE_RECEIPTS")"
jq -n \
  --arg name "$asset" \
  --arg digest "sha256:$digest" \
  --arg version "$SDLC_RELEASE_VERSION" \
  --arg merged "$SDLC_RELEASE_MERGED_SHA" \
  --arg source "$source_sha" \
  '{schema:1,artifacts:[{name:$name,digest:$digest,version:$version,merged_sha:$merged,source_main_sha:$source}]}' \
  > "$SDLC_RELEASE_RECEIPTS"
