package release

import (
	"strings"
	"testing"
)

func TestVerifyPredecessorChecksLineageBeforeAuditedRetirement(t *testing.T) {
	source := strings.Repeat("a", 40)
	tag := strings.Repeat("b", 40)
	manifest := []byte(`{"version":"1.2.3","source_main_sha":"` + source + `"}` + "\n")
	marker := []byte(`{"schema":1,"version":"1.2.3","source_main_sha":"` + source + `","merged_sha":"` + tag + `","reason":"audited incomplete release"}`)
	draft := true
	base := Predecessor{Version: "1.2.3", SourceMainSHA: source}
	valid := PublicationState{IsDraft: &draft, IsPrerelease: boolPtr(false), RetirementMarker: marker}
	facts := PredecessorFacts{TagSHA: tag, TagParent: source, TaggedManifest: manifest, OnDefaultBranch: true}
	if err := VerifyPredecessor(base, facts, manifest, valid); err != nil {
		t.Fatal(err)
	}
	for name, tampered := range map[string]PredecessorFacts{
		"tag parent":      {TagSHA: tag, TagParent: strings.Repeat("c", 40), TaggedManifest: manifest, OnDefaultBranch: true},
		"tag manifest":    {TagSHA: tag, TagParent: source, TaggedManifest: []byte("{}\n"), OnDefaultBranch: true},
		"unreachable tag": {TagSHA: tag, TagParent: source, TaggedManifest: manifest, OnDefaultBranch: false},
	} {
		t.Run(name, func(t *testing.T) {
			if err := VerifyPredecessor(base, tampered, manifest, valid); err == nil {
				t.Fatal("tampered predecessor was accepted")
			}
		})
	}
}

func TestVerifyPredecessorRequiresCompletedPublicationWithoutRetirement(t *testing.T) {
	source := strings.Repeat("a", 40)
	tag := strings.Repeat("b", 40)
	manifest := []byte(`{"version":"1.2.3","source_main_sha":"` + source + `"}` + "\n")
	draft := true
	facts := PredecessorFacts{TagSHA: tag, TagParent: source, TaggedManifest: manifest, OnDefaultBranch: true}
	if err := VerifyPredecessor(
		Predecessor{Version: "1.2.3", SourceMainSHA: source}, facts, manifest,
		PublicationState{IsDraft: &draft, IsPrerelease: boolPtr(false)},
	); err == nil {
		t.Fatal("unpublished, unretired predecessor was accepted")
	}
}

func TestVerifyPredecessorRejectsRetirementMarkerOnCompletedPublication(t *testing.T) {
	source := strings.Repeat("a", 40)
	tag := strings.Repeat("b", 40)
	manifest := []byte(`{"version":"1.2.3","source_main_sha":"` + source + `"}` + "\n")
	completed := false
	prerelease := false
	facts := PredecessorFacts{TagSHA: tag, TagParent: source, TaggedManifest: manifest, OnDefaultBranch: true}
	base := Predecessor{Version: "1.2.3", SourceMainSHA: source}

	publication := PublicationState{
		IsDraft: &completed, IsPrerelease: &prerelease,
		Assets: []PublicationAsset{{Name: "sdlc-source-1.2.3-" + tag + ".tar.gz", Digest: "sha256:" + strings.Repeat("c", 64)}},
	}
	if err := VerifyPredecessor(base, facts, manifest, publication); err != nil {
		t.Fatalf("unmarked completed publication should pass: %v", err)
	}
	for name, marker := range map[string][]byte{
		"valid":     []byte(`{"schema":1,"version":"1.2.3","source_main_sha":"` + source + `","merged_sha":"` + tag + `","reason":"audited incomplete release"}`),
		"malformed": []byte(`{"schema":1}`),
	} {
		t.Run(name, func(t *testing.T) {
			marked := publication
			marked.RetirementMarker = marker
			if err := VerifyPredecessor(base, facts, manifest, marked); err == nil || !strings.Contains(err.Error(), "has a retirement marker but is not draft") {
				t.Fatalf("marked completed publication was not rejected for its marker: %v", err)
			}
		})
	}
}

func boolPtr(value bool) *bool { return &value }
