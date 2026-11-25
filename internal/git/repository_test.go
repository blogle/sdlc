package git

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestPredecessorManifestDistinguishesFirstReleaseFromUnreadableManifest(t *testing.T) {
	root := t.TempDir()
	gitTest(t, root, "init", "-q")
	gitTest(t, root, "config", "user.name", "Test")
	gitTest(t, root, "config", "user.email", "test@example.invalid")
	if err := os.WriteFile(filepath.Join(root, "README"), []byte("first\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	gitTest(t, root, "add", ".")
	gitTest(t, root, "commit", "-qm", "first")
	first := strings.TrimSpace(gitTest(t, root, "rev-parse", "HEAD"))
	if manifest, found, err := Open(root).PredecessorManifest(first); err != nil || found || manifest != nil {
		t.Fatalf("missing predecessor was not treated as first release: %#v %v %v", manifest, found, err)
	}
	if err := os.MkdirAll(filepath.Join(root, ".sdlc"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, ".sdlc/release.json"), []byte("{"), 0o644); err != nil {
		t.Fatal(err)
	}
	gitTest(t, root, "add", ".")
	gitTest(t, root, "commit", "-qm", "unreadable manifest")
	second := strings.TrimSpace(gitTest(t, root, "rev-parse", "HEAD"))
	if _, found, err := Open(root).PredecessorManifest(second); err == nil || !found || !strings.Contains(err.Error(), "unreadable") {
		t.Fatalf("unreadable predecessor was not rejected: found=%v err=%v", found, err)
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
