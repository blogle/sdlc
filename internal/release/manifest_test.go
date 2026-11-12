package release

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestValidateIdentityBindsActualSquashTree(t *testing.T) {
	source, merged, blob := strings.Repeat("a", 40), strings.Repeat("b", 40), strings.Repeat("c", 40)
	changelog := []byte("# Changelog\n")
	bindings := []FragmentBinding{{Path: ".changes/fix.json", BlobSHA: blob}}
	manifest := NewManifest("1.2.3", source, nil, bindings, changelog)
	manifest.Schema = 1
	identity, err := ValidateIdentity(manifest, merged, Tree{Parent: source, TreeSHA: strings.Repeat("d", 40), SourceBlobs: map[string]string{bindings[0].Path: blob}, MergedBlobs: map[string]string{"CHANGELOG.md": "blob", ".sdlc/release.json": "manifest"}, ChangedPaths: map[string]bool{"CHANGELOG.md": true, ".sdlc/release.json": true, bindings[0].Path: true}, Changelog: changelog})
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(identity)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(encoded), `"fragments":[[".changes/fix.json","`+blob+`"]]`) {
		t.Fatalf("legacy release identity format changed: %s", encoded)
	}
	manifest.GeneratedTree = strings.Repeat("0", 64)
	if _, err := ValidateIdentity(manifest, merged, Tree{Parent: source, TreeSHA: strings.Repeat("d", 40), SourceBlobs: map[string]string{bindings[0].Path: blob}, MergedBlobs: map[string]string{"CHANGELOG.md": "blob", ".sdlc/release.json": "manifest"}, ChangedPaths: map[string]bool{"CHANGELOG.md": true, ".sdlc/release.json": true, bindings[0].Path: true}, Changelog: changelog}); err == nil {
		t.Fatal("tampered generated tree digest was accepted")
	}
}

func TestManifestDecodeRetainsUnrecognizedFieldsForReplayRejection(t *testing.T) {
	var manifest Manifest
	if err := json.Unmarshal([]byte(`{"schema":1,"version":"1.2.3","extra":true}`), &manifest); err != nil {
		t.Fatal(err)
	}
	if !manifest.UnknownFields["extra"] {
		t.Fatal("manifest replay cannot see extra unrecognized fields")
	}
}
