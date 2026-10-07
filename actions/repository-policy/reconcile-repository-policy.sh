#!/usr/bin/env bash
set -euo pipefail

ruleset_name="SDLC default branch"
ruleset_ids_output="$(gh api --paginate "repos/${REPOSITORY}/rulesets?per_page=100" \
  --jq ".[] | select(.name == \"${ruleset_name}\") | .id")"
ruleset_ids=()
if [[ -n "$ruleset_ids_output" ]]; then
  mapfile -t ruleset_ids <<< "$ruleset_ids_output"
fi

if (( ${#ruleset_ids[@]} > 1 )); then
  printf 'Expected at most one ruleset named %s; found %s\n' "$ruleset_name" "${#ruleset_ids[@]}" >&2
  exit 1
elif (( ${#ruleset_ids[@]} == 0 )); then
  gh api --method POST "repos/${REPOSITORY}/rulesets" --input "$DESIRED_RULESET" >/dev/null
  printf 'Created ruleset: %s\n' "$ruleset_name"
else
  gh api --method PUT "repos/${REPOSITORY}/rulesets/${ruleset_ids[0]}" --input "$DESIRED_RULESET" >/dev/null
  printf 'Updated ruleset: %s (id %s)\n' "$ruleset_name" "${ruleset_ids[0]}"
fi

upsert_label() {
  local name="$1" color="$2" description="$3"
  local endpoint="repos/${REPOSITORY}/labels/${name//:/%3A}"
  if gh api "$endpoint" >/dev/null 2>&1; then
    gh api --method PATCH "$endpoint" -f color="$color" -f description="$description" >/dev/null
    printf 'Updated label: %s\n' "$name"
  else
    gh api --method POST "repos/${REPOSITORY}/labels" \
      -f name="$name" -f color="$color" -f description="$description" >/dev/null
    printf 'Created label: %s\n' "$name"
  fi
}

upsert_label 'integration:auto' '1d76db' 'Authorize automatic integration'
upsert_label 'integration:review' 'fbca04' 'Require human review before integration'
