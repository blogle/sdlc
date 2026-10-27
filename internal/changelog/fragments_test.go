package changelog

import (
	"os"
	"path/filepath"
	"testing"
)

func TestFinalizeBytesMatchesLegacyOutput(t *testing.T) {
	entries := []Entry{{Fragment: Fragment{Type: "fix", Summary: "preserve bytes"}}}
	want := "# Changelog\n\n## [1.2.4] - 2026-10-26\n\n- **fix**: preserve bytes\n\n## [1.2.3] - 2026-10-01\n\n- **fix**: previous\n"
	got := string(FinalizeBytes([]byte("# Changelog\n\n## [1.2.3] - 2026-10-01\n\n- **fix**: previous\n"), "v1.2.4", "2026-10-26", entries))
	if got != want {
		t.Fatalf("finalized changelog differs\nwant: %q\n got: %q", want, got)
	}
}

func TestFinalizeHeaderWithoutTrailingNewlineIsNotDuplicated(t *testing.T) {
	entries := []Entry{{Fragment: Fragment{Type: "fix", Summary: "only one title"}}}
	got := string(FinalizeBytes([]byte("# Changelog"), "1.0.1", "2026-10-26", entries))
	want := "# Changelog\n\n## [1.0.1] - 2026-10-26\n\n- **fix**: only one title\n"
	if got != want {
		t.Fatalf("changelog header duplicated or changed\nwant: %q\n got: %q", want, got)
	}
}

func TestReadFragmentsSortsAndRejectsInvalidInput(t *testing.T) {
	root := t.TempDir()
	directory := filepath.Join(root, ".changes")
	if err := os.Mkdir(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	for name, content := range map[string]string{
		"z.json": `{"type":"docs","semver":"patch","summary":"last"}`,
		"a.json": `{"type":"fix","semver":"minor","summary":"first"}`,
	} {
		if err := os.WriteFile(filepath.Join(directory, name), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	entries, err := ReadFragments(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 2 || filepath.Base(entries[0].Path) != "a.json" || Bump(entries) != "minor" {
		t.Fatalf("unexpected ordered entries: %#v", entries)
	}
	if _, err := ReadFragments(root, []string{"../escape.json"}); err == nil {
		t.Fatal("expected path traversal rejection")
	}
}

func TestNextVersionPreservesConsumerBumpSemantics(t *testing.T) {
	cases := map[string]string{"patch": "1.2.4", "minor": "1.3.0", "major": "2.0.0"}
	for bump, want := range cases {
		got, err := NextVersion("1.2.3", bump)
		if err != nil || got != want {
			t.Errorf("NextVersion(%q): got %q, %v; want %q", bump, got, err, want)
		}
	}
}

func TestNextVersionDoesNotOverflowValidSemVerComponents(t *testing.T) {
	previous := "999999999999999999999999999999999999999.0.0"
	got, err := NextVersion(previous, "patch")
	if err != nil {
		t.Fatal(err)
	}
	want := "999999999999999999999999999999999999999.0.1"
	if got != want {
		t.Fatalf("got %s, want %s", got, want)
	}
}
