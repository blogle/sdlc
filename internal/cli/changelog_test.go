package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestFragmentFlagAcceptsNargsStyleSelectionsAndEmptySelection(t *testing.T) {
	root := t.TempDir()
	original, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(root); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(original) })
	if err := os.Mkdir(filepath.Join(root, ".changes"), 0o755); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"a.json", "b.json"} {
		data := []byte(`{"type":"fix","semver":"patch","summary":"test"}`)
		if err := os.WriteFile(filepath.Join(root, ".changes", name), data, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	var output bytes.Buffer
	command := changelogCommand()
	command.SetOut(&output)
	command.SetErr(&output)
	command.SetArgs([]string{"check", "--fragment", ".changes/a.json", ".changes/b.json"})
	if err := command.Execute(); err != nil {
		t.Fatal(err)
	}
	if output.String() != "validated 2 changelog fragment(s)\n" {
		t.Fatalf("nargs-style selection changed: %q", output.String())
	}
	output.Reset()
	command = changelogCommand()
	command.SetOut(&output)
	command.SetErr(&output)
	command.SetArgs([]string{"check", "--fragment"})
	if err := command.Execute(); err != nil {
		t.Fatal(err)
	}
	if output.String() != "validated 0 changelog fragment(s)\n" {
		t.Fatalf("empty --fragment selection changed: %q", output.String())
	}
}
