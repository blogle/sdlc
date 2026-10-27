package ruleset

import (
	"bytes"
	"encoding/json"
)

func MatchesWithAttestation(actual, desired map[string]any, repository string, attestation map[string]any, automated bool) bool {
	if actual == nil {
		return false
	}
	if actual["bypass_actors"] != nil {
		return Matches(actual, desired, false)
	}
	if !AttestsBypassActors(actual, desired, repository, attestation, automated) {
		return false
	}
	comparable := clone(actual)
	comparable["bypass_actors"] = desired["bypass_actors"]
	return Matches(comparable, desired, false)
}

func AttestsBypassActors(actual, desired map[string]any, repository string, attestation map[string]any, automated bool) bool {
	if actual == nil || desired == nil {
		return false
	}
	binding := attestation
	if automated {
		binding = object(attestation["automated_merges"])
	}
	id, idOK := actual["id"].(float64)
	attestedID, attestedOK := binding["ruleset_id"].(float64)
	if !idOK || !attestedOK || binding["schema"] != float64(1) || binding["repository"] != repository || int64(id) != int64(attestedID) || !SameTimestamp(binding["updated_at"], actual["updated_at"]) || !equalJSON(binding["bypass_actors"], desired["bypass_actors"]) {
		return false
	}
	return true
}

func equalJSON(left, right any) bool {
	a, _ := json.Marshal(left)
	b, _ := json.Marshal(right)
	return bytes.Equal(a, b)
}
func object(value any) map[string]any {
	result, _ := value.(map[string]any)
	if result == nil {
		return map[string]any{}
	}
	return result
}
