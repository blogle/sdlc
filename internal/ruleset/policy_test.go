package ruleset

import "testing"

func TestSameTimestampComparesInstants(t *testing.T) {
	if !SameTimestamp("2026-10-26T10:00:00Z", "2026-10-26T12:00:00+02:00") {
		t.Fatal("equivalent timestamp offsets should match")
	}
	if SameTimestamp("", "2026-10-26T10:00:00Z") {
		t.Fatal("empty timestamp must not match")
	}
}

func TestMatchesIgnoresGitHubMetadataButRequiresExactPolicy(t *testing.T) {
	desired := map[string]any{"name": "rules", "enforcement": "active", "bypass_actors": []any{}, "rules": []any{map[string]any{"type": "required_status_checks", "parameters": map[string]any{"contexts": []any{"a", "b"}}}}}
	actual := map[string]any{"id": float64(21), "name": "rules", "enforcement": "active", "updated_at": "today", "bypass_actors": []any{}, "rules": []any{map[string]any{"type": "required_status_checks", "parameters": map[string]any{"contexts": []any{"b", "a"}, "extra": true}}}}
	if !Matches(actual, desired, false) {
		t.Fatal("matching policy with unrelated metadata or array ordering was rejected")
	}
	actual["enforcement"] = "disabled"
	if Matches(actual, desired, false) {
		t.Fatal("unexpected policy field was ignored")
	}
}

func TestMatchesOnlyAcceptsRedactedBypassWhenExplicit(t *testing.T) {
	desired := map[string]any{"name": "rules", "bypass_actors": []any{}, "rules": []any{}}
	actual := map[string]any{"name": "rules", "bypass_actors": nil, "rules": []any{}}
	if Matches(actual, desired, false) {
		t.Fatal("unattested redacted actors were accepted")
	}
	if !Matches(actual, desired, true) {
		t.Fatal("attested redacted actors were rejected")
	}
}
