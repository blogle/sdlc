package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	gitrepo "github.com/blogle/sdlc/internal/git"
	"github.com/blogle/sdlc/internal/release"
)

func TestSnapshotGeneratesLegacyManifestAndConsumesFragments(t *testing.T) {
	root := t.TempDir()
	original, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(root); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(original) })
	gitTest(t, root, "init", "-q")
	gitTest(t, root, "config", "user.name", "Test")
	gitTest(t, root, "config", "user.email", "test@example.invalid")
	writeTestFile(t, root, "CHANGELOG.md", "# Changelog\n")
	writeTestFile(t, root, ".changes/fix.json", "{\"type\":\"fix\",\"semver\":\"patch\",\"summary\":\"native release\"}\n")
	gitTest(t, root, "add", ".")
	gitTest(t, root, "commit", "-qm", "source")
	source := strings.TrimSpace(gitTest(t, root, "rev-parse", "HEAD"))
	gitTest(t, root, "tag", "v1.2.3")
	sourceDate := strings.TrimSpace(gitTest(t, root, "show", "-s", "--format=%cs", source))

	var output bytes.Buffer
	command := releaseCommand()
	command.SetOut(&output)
	command.SetErr(&output)
	command.SetArgs([]string{"snapshot", "--source-sha", source, "--json"})
	if err := command.Execute(); err != nil {
		t.Fatal(err)
	}
	var result struct {
		Release  bool           `json:"release"`
		Manifest map[string]any `json:"manifest"`
	}
	if err := json.Unmarshal(output.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if !result.Release || result.Manifest["version"] != "1.2.4" || result.Manifest["source_main_sha"] != source {
		t.Fatalf("unexpected snapshot: %#v", result)
	}
	if _, err := os.Stat(filepath.Join(root, ".changes/fix.json")); !os.IsNotExist(err) {
		t.Fatal("fragment was not consumed")
	}
	changelog, err := os.ReadFile(filepath.Join(root, "CHANGELOG.md"))
	if err != nil {
		t.Fatal(err)
	}
	if got, want := string(changelog), "# Changelog\n\n## [1.2.4] - "+sourceDate+"\n\n- **fix**: native release\n"; got != want {
		t.Fatalf("changelog bytes differ\nwant: %q\n got: %q", want, got)
	}
	manifest, err := os.ReadFile(filepath.Join(root, ".sdlc/release.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(manifest), `"schema": 1`) || !strings.HasSuffix(string(manifest), "\n") {
		t.Fatalf("unexpected manifest encoding: %q", manifest)
	}
	gitTest(t, root, "add", ".")
	gitTest(t, root, "commit", "-qm", "release")
	merged := strings.TrimSpace(gitTest(t, root, "rev-parse", "HEAD"))
	identity, err := gitrepo.Open(root).ValidateRelease(merged)
	if err != nil {
		t.Fatal(err)
	}
	if identity.Version != "1.2.4" || identity.SourceMain != source || identity.Commit != merged {
		t.Fatalf("unexpected validated identity: %#v", identity)
	}
	var manifestValue release.Manifest
	manifestBytes, err := os.ReadFile(filepath.Join(root, ".sdlc/release.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(manifestBytes, &manifestValue); err != nil {
		t.Fatal(err)
	}
	if err := release.VerifyCandidate(gitrepo.Open(root), source, merged, &manifestValue); err != nil {
		t.Fatal(err)
	}
}

func writeTestFile(t *testing.T, root, name, content string) {
	t.Helper()
	path := filepath.Join(root, name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func gitTest(t *testing.T, root string, args ...string) string {
	t.Helper()
	command := exec.Command("git", args...)
	command.Dir = root
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, output)
	}
	return string(output)
}
