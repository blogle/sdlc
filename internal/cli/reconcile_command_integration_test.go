package cli

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestReconcileCommandRetiresNoFragmentsAndBlocksUnpublishedPredecessor(t *testing.T) {
	t.Run("no fragments closes a stale candidate without creating a release", func(t *testing.T) {
		_, _ = makeRulesetFixtures(t, false)
		fixtures := os.Getenv("GH_FIXTURES")
		writeRepoFile(t, fixtures, "default.applied", "")
		writeRepoFile(t, fixtures, "automated.applied", "")
		t.Setenv("GH_PR_NUMBER", "42")
		mutation := filepath.Join(fixtures, "pr-mutation")
		t.Setenv("GH_PR_MUTATION", mutation)
		root, bare, baseline := makeRemoteRepository(t, false)
		inRepository(t, root)
		t.Setenv("GITHUB_REPOSITORY", "owner/repo")
		t.Setenv("SDLC_DEFAULT_BRANCH", "main")
		t.Setenv("SDLC_RELEASE_BRANCH", "sdlc/release-next")
		command := New()
		command.SetArgs([]string{"release", "reconcile", "--repo", "owner/repo"})
		if err := command.Execute(); err != nil {
			t.Fatal(err)
		}
		if _, err := os.Stat(mutation); err != nil {
			t.Fatalf("stale candidate was not retired: %v", err)
		}
		if refExists(bare, "refs/heads/sdlc/release-next") {
			t.Fatal("no-fragment reconcile published a candidate branch")
		}
		if got := strings.TrimSpace(repoGit(t, root, "rev-parse", "origin/main")); got != baseline {
			t.Fatalf("main moved during reconciliation: %s", got)
		}
	})

	t.Run("unpublished predecessor blocks candidate and PR mutation", func(t *testing.T) {
		_, _ = makeRulesetFixtures(t, false)
		fixtures := os.Getenv("GH_FIXTURES")
		writeRepoFile(t, fixtures, "default.applied", "")
		writeRepoFile(t, fixtures, "automated.applied", "")
		mutation := filepath.Join(fixtures, "pr-mutation")
		t.Setenv("GH_PR_MUTATION", mutation)
		root, bare, _ := makeRemoteRepository(t, true)
		inRepository(t, root)
		t.Setenv("GITHUB_REPOSITORY", "owner/repo")
		t.Setenv("SDLC_DEFAULT_BRANCH", "main")
		command := New()
		command.SetArgs([]string{"release", "reconcile", "--repo", "owner/repo"})
		err := command.Execute()
		if err == nil || !strings.Contains(err.Error(), "no committed tag; publication predecessor is unreconciled") {
			t.Fatalf("unpublished predecessor was not blocked: %v", err)
		}
		if _, err := os.Stat(mutation); !os.IsNotExist(err) {
			t.Fatal("predecessor failure mutated the release PR")
		}
		if refExists(bare, "refs/heads/sdlc/release-next") {
			t.Fatal("predecessor failure pushed a candidate")
		}
	})
}

func TestReconcileCommandPushesCandidateAndMaintainsReleasePR(t *testing.T) {
	_, _ = makeRulesetFixtures(t, false)
	fixtures := os.Getenv("GH_FIXTURES")
	writeRepoFile(t, fixtures, "default.applied", "")
	writeRepoFile(t, fixtures, "automated.applied", "")
	t.Setenv("GH_PR_NUMBER", "")
	mutation := filepath.Join(fixtures, "pr-mutation")
	t.Setenv("GH_PR_MUTATION", mutation)
	root, bare, _ := makeRemoteRepository(t, false)
	writeRepoFile(t, root, ".changes/fix.json", `{"type":"fix","semver":"patch","summary":"rolling candidate"}`)
	reconcileGit(t, root, "add", ".changes/fix.json")
	reconcileGit(t, root, "commit", "-qm", "fragment")
	reconcileGit(t, root, "push", "origin", "main")
	reconcileGit(t, root, "fetch", "origin", "main")
	inRepository(t, root)
	t.Setenv("GITHUB_REPOSITORY", "owner/repo")
	t.Setenv("SDLC_DEFAULT_BRANCH", "main")
	t.Setenv("SDLC_RELEASE_BRANCH", "sdlc/release-next")

	command := New()
	command.SetArgs([]string{"release", "reconcile", "--repo", "owner/repo"})
	if err := command.Execute(); err != nil {
		t.Fatal(err)
	}
	candidate := strings.TrimSpace(reconcileGit(t, root, "--git-dir", bare, "rev-parse", "refs/heads/sdlc/release-next"))
	if !strings.Contains(string(readIntegrationFile(t, mutation)), "pr create") {
		t.Fatal("release PR was not created after successful candidate push")
	}

	t.Setenv("GH_PR_NUMBER", "42")
	if err := command.Execute(); err != nil {
		t.Fatal(err)
	}
	updated := string(readIntegrationFile(t, mutation))
	if !strings.Contains(updated, "pr edit 42") {
		t.Fatalf("existing release PR was not updated: %s", updated)
	}
	if got := strings.TrimSpace(reconcileGit(t, root, "--git-dir", bare, "rev-parse", "refs/heads/sdlc/release-next")); got != candidate {
		t.Fatalf("reconcile changed its deterministic candidate: %s != %s", got, candidate)
	}
}

func makeRemoteRepository(t *testing.T, withPredecessor bool) (string, string, string) {
	t.Helper()
	base := t.TempDir()
	root, bare := filepath.Join(base, "working"), filepath.Join(base, "remote.git")
	if err := os.Mkdir(root, 0o755); err != nil {
		t.Fatal(err)
	}
	repoGit(t, root, "init", "-q", "-b", "main")
	repoGit(t, root, "config", "user.name", "Reconcile Command Test")
	repoGit(t, root, "config", "user.email", "reconcile-command@example.invalid")
	writeRepoFile(t, root, "CHANGELOG.md", "# Changelog\n")
	if withPredecessor {
		writeRepoFile(t, root, ".changes/next.json", `{"type":"fix","semver":"patch","summary":"successor"}`)
		writeRepoFile(t, root, ".sdlc/release.json", `{"version":"1.0.0","source_main_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}`)
	}
	repoGit(t, root, "add", ".")
	repoGit(t, root, "commit", "-qm", "baseline")
	repoGit(t, base, "init", "--bare", "-q", bare)
	repoGit(t, root, "remote", "add", "origin", bare)
	repoGit(t, root, "push", "-u", "origin", "main")
	repoGit(t, root, "fetch", "origin", "main")
	return root, bare, strings.TrimSpace(repoGit(t, root, "rev-parse", "origin/main"))
}

func inRepository(t *testing.T, root string) {
	t.Helper()
	original, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(root); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(original) })
}

func refExists(bare, ref string) bool {
	command := exec.Command("git", "--git-dir", bare, "show-ref", "--verify", "--quiet", ref)
	return command.Run() == nil
}
