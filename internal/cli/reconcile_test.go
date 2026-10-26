package cli

import (
	"os"
	"path/filepath"
	"testing"
)

func TestReconcileWithNoFragmentsRetiresInsteadOfCreatingCandidate(t *testing.T) {
	root := t.TempDir()
	bin := filepath.Join(t.TempDir(), "bin")
	if err := os.MkdirAll(bin, 0o755); err != nil {
		t.Fatal(err)
	}
	gh := filepath.Join(bin, "gh")
	if err := os.WriteFile(gh, []byte("#!/bin/sh\nexit 0\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	outcome, candidate, err := reconcileSnapshot(root, "owner/repo", "main", "sdlc/release-next", "baseline")
	if err != nil {
		t.Fatal(err)
	}
	if outcome != noReleaseCandidate || candidate != "" {
		t.Fatalf("unexpected no-fragment result: %v %q", outcome, candidate)
	}
}
