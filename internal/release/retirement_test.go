package release

import (
	"strings"
	"testing"
)

func TestRetirementMarkerRequiresExactIdentityAndDraft(t *testing.T) {
	source := strings.Repeat("a", 40)
	merged := strings.Repeat("b", 40)
	marker := []byte(`{"schema":1,"version":"1.2.3","source_main_sha":"` + source + `","merged_sha":"` + merged + `","reason":"audited incomplete release"}`)
	if err := ParseRetirementMarker(marker, "1.2.3", source, merged); err != nil {
		t.Fatal(err)
	}
	if err := RejectAbandonedMarker(marker, true, "1.2.3", source, merged); err == nil || !strings.Contains(err.Error(), "abandoned") {
		t.Fatalf("marked identity was not rejected: %v", err)
	}
}

func TestRetirementMarkerFailsClosedOnTamperingAndDuplicates(t *testing.T) {
	source := strings.Repeat("a", 40)
	merged := strings.Repeat("b", 40)
	base := `{"schema":1,"version":"1.2.3","source_main_sha":"` + source + `","merged_sha":"` + merged + `","reason":"audited"}`
	for _, marker := range []string{
		strings.Replace(base, `"schema":1`, `"schema":1,"schema":1`, 1),
		strings.Replace(base, `"reason":"audited"`, `"reason":""`, 1),
		strings.Replace(base, `"reason":"audited"`, `"extra":true,"reason":"audited"`, 1),
	} {
		if err := ParseRetirementMarker([]byte(marker), "1.2.3", source, merged); err == nil {
			t.Fatalf("tampered marker accepted: %s", marker)
		}
	}
}
