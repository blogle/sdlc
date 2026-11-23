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

	t.Run("mismatched tag parent and divergent history block candidate and PR mutation", func(t *testing.T) {
		_, _ = makeRulesetFixtures(t, false)
		fixtures := os.Getenv("GH_FIXTURES")
		writeRepoFile(t, fixtures, "default.applied", "")
		writeRepoFile(t, fixtures, "automated.applied", "")
		mutation := filepath.Join(fixtures, "pr-mutation")
		t.Setenv("GH_PR_MUTATION", mutation)
		root, bare, tagSHA, tagParent, sourceSHA, baseline := makeMismatchedPredecessorRepository(t)
		writeRepoFile(t, fixtures, "release.json", `{"isDraft":false,"isPrerelease":false,"assets":[]}`)
		inRepository(t, root)
		t.Setenv("GITHUB_REPOSITORY", "owner/repo")
		t.Setenv("SDLC_DEFAULT_BRANCH", "main")
		t.Setenv("SDLC_RELEASE_BRANCH", "sdlc/release-next")

		command := New()
		command.SetArgs([]string{"release", "reconcile", "--repo", "owner/repo"})
		err := command.Execute()
		if err == nil ||
			!strings.Contains(err.Error(), "tag does not identify the manifest's merged commit") ||
			!strings.Contains(err.Error(), tagSHA) ||
			!strings.Contains(err.Error(), tagParent) ||
			!strings.Contains(err.Error(), sourceSHA) ||
			!strings.Contains(err.Error(), "no common ancestor") {
			t.Fatalf("mismatched predecessor was not diagnosed with observed identities: %v", err)
		}
		if _, err := os.Stat(mutation); !os.IsNotExist(err) {
			t.Fatal("mismatched predecessor mutated the release PR")
		}
		if refExists(bare, "refs/heads/sdlc/release-next") {
			t.Fatal("mismatched predecessor pushed a candidate")
		}
		if got := strings.TrimSpace(repoGit(t, root, "rev-parse", "origin/main")); got != baseline {
			t.Fatalf("main moved during failed reconciliation: %s", got)
		}
		if tagParent == sourceSHA {
			t.Fatal("fixture did not mismatch the tag parent and manifest source")
		}
		if command := exec.Command("git", "-C", root, "merge-base", tagSHA, baseline); command.Run() == nil {
			t.Fatal("fixture unexpectedly gave the divergent tag and main a common ancestor")
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

func makeMismatchedPredecessorRepository(t *testing.T) (root, bare, tagSHA, tagParent, sourceSHA, baseline string) {
	t.Helper()
	base := t.TempDir()
	root, bare = filepath.Join(base, "working"), filepath.Join(base, "remote.git")
	if err := os.Mkdir(root, 0o755); err != nil {
		t.Fatal(err)
	}
	repoGit(t, root, "init", "-q", "-b", "main")
	repoGit(t, root, "config", "user.name", "Reconcile Lineage Test")
	repoGit(t, root, "config", "user.email", "reconcile-lineage@example.invalid")
	writeRepoFile(t, root, "CHANGELOG.md", "# Changelog\n")
	repoGit(t, root, "add", ".")
	repoGit(t, root, "commit", "-qm", "source")
	sourceSHA = strings.TrimSpace(repoGit(t, root, "rev-parse", "HEAD"))
	writeRepoFile(t, root, ".sdlc/release.json", `{"version":"1.0.0","source_main_sha":"`+sourceSHA+`"}`)
	writeRepoFile(t, root, ".changes/next.json", `{"type":"fix","semver":"patch","summary":"successor"}`)
	repoGit(t, root, "add", ".")
	repoGit(t, root, "commit", "-qm", "main successor")
	baseline = strings.TrimSpace(repoGit(t, root, "rev-parse", "HEAD"))

	repoGit(t, root, "checkout", "--orphan", "divergent")
	repoGit(t, root, "rm", "-rf", ".")
	writeRepoFile(t, root, "CHANGELOG.md", "# Changelog\n")
	repoGit(t, root, "add", ".")
	repoGit(t, root, "commit", "-qm", "divergent source")
	writeRepoFile(t, root, ".sdlc/release.json", `{"version":"1.0.0","source_main_sha":"`+sourceSHA+`"}`)
	repoGit(t, root, "add", ".")
	repoGit(t, root, "commit", "-qm", "divergent release")
	repoGit(t, root, "tag", "v1.0.0")
	tagSHA = strings.TrimSpace(repoGit(t, root, "rev-parse", "v1.0.0"))
	tagParent = strings.TrimSpace(repoGit(t, root, "rev-parse", tagSHA+"^"))
	repoGit(t, root, "checkout", "main")

	repoGit(t, base, "init", "--bare", "-q", bare)
	repoGit(t, root, "remote", "add", "origin", bare)
	repoGit(t, root, "push", "-u", "origin", "main")
	repoGit(t, root, "push", "origin", "v1.0.0")
	repoGit(t, root, "fetch", "origin", "main")
	return root, bare, tagSHA, tagParent, sourceSHA, baseline
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
