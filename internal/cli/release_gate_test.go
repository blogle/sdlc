package cli

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCheckAPIConsumesAllPagesAndRejectsDuplicates(t *testing.T) {
	page1 := `[{"check_runs":[{"id":1,"name":"sdlc / check","head_sha":"head","status":"completed","conclusion":"success","completed_at":"2026-01-01T00:00:00Z","app":{"id":15368,"slug":"github-actions"}}]},`
	page2 := `{"check_runs":[{"id":2,"name":"sdlc / check","head_sha":"head","status":"completed","conclusion":"failure","completed_at":"2026-01-02T00:00:00Z","app":{"id":15368,"slug":"github-actions"}}]}]`
	bin := fakeGH(t, page1+page2)
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	checks, err := checksFor("owner/repo", "head")
	if err != nil {
		t.Fatal(err)
	}
	if len(checks) != 2 || checks[1].Conclusion != "failure" || checks[1].AppID != 15368 {
		t.Fatalf("pagination metadata was lost: %#v", checks)
	}
	dup := `[ {"check_runs":[{"id":1,"name":"a","head_sha":"head","app":{}}]}, {"check_runs":[{"id":1,"name":"b","head_sha":"head","app":{}}]} ]`
	bin = fakeGH(t, dup)
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	if _, err := checksFor("owner/repo", "head"); err == nil {
		t.Fatal("duplicate paginated check IDs were accepted")
	}
}

func TestAuthenticatedAppRequiresMatchingBotAndInstallationRepository(t *testing.T) {
	bin := fakeGH(t, `{"repositories":[{"full_name":"owner/repo"}]}`)
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	login, slug, err := authenticatedApp("releaser[bot]", "releaser", "owner/repo")
	if err != nil || login != "releaser[bot]" || slug != "releaser" {
		t.Fatalf("trusted installation rejected: %s %s %v", login, slug, err)
	}
	if _, _, err := authenticatedApp("human", "releaser", "owner/repo"); err == nil {
		t.Fatal("wrong bot identity was accepted")
	}
	if _, _, err := authenticatedApp("releaser[bot]", "releaser", "owner/other"); err == nil {
		t.Fatal("installation without repository access was accepted")
	}
}

func fakeGH(t *testing.T, response string) string {
	t.Helper()
	directory := t.TempDir()
	path := filepath.Join(directory, "gh")
	// The value is JSON controlled by the test, encoded through a single-quoted
	// shell argument with no single quotes in these fixtures.
	if strings.Contains(response, "'") {
		t.Fatal("fake GitHub response has unsupported shell quoting")
	}
	script := "#!/bin/sh\nprintf '%s' '" + response + "'\n"
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return directory
}
