package consumer

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestInstallAndCheckPreserveNativeJSON5(t *testing.T) {
	root := t.TempDir()
	revision := strings.Repeat("a", 40)
	lock := map[string]any{"nodes": map[string]any{
		"root": map[string]any{"inputs": map[string]any{"sdlc": "sdlc"}},
		"sdlc": map[string]any{"original": map[string]any{"type": "github", "owner": "blogle", "repo": "sdlc", "ref": "v2"}, "locked": map[string]any{"rev": revision}},
	}}
	encoded, err := json.Marshal(lock)
	if err != nil {
		t.Fatal(err)
	}
	writeConsumer(t, root, "flake.lock", string(encoded))
	native := "// retain comments and style\n{ extends: ['github>blogle/sdlc#v1'], customManagers: [], }\n"
	writeConsumer(t, root, "renovate.json5", native)
	contract, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	if err := Install(root, contract, false); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(filepath.Join(root, "renovate.json5"))
	if err != nil {
		t.Fatal(err)
	}
	if string(before) != native {
		t.Fatal("native Renovate JSON5 was rewritten")
	}
	if err := Install(root, contract, true); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "renovate.json")); !os.IsNotExist(err) {
		t.Fatal("generated competing renovate.json")
	}
	workflow, err := os.ReadFile(filepath.Join(root, ".github/workflows/sdlc.yml"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(workflow), "uses: blogle/sdlc/.github/workflows/ci.yml@"+revision) {
		t.Fatalf("workflow does not pin locked revision: %s", workflow)
	}
}

func writeConsumer(t *testing.T, root, name, value string) {
	t.Helper()
	path := filepath.Join(root, name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(value), 0o644); err != nil {
		t.Fatal(err)
	}
}
