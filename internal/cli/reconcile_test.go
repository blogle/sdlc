package cli

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/blogle/sdlc/internal/release"
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

func TestReconcileSnapshotPushesReplaceableCandidateWithLeaseAndIsIdempotent(t *testing.T) {
	base := t.TempDir()
	root, bare := filepath.Join(base, "working"), filepath.Join(base, "remote.git")
	if err := os.Mkdir(root, 0o755); err != nil {
		t.Fatal(err)
	}
	reconcileGit(t, root, "init", "-q", "-b", "main")
	reconcileGit(t, root, "config", "user.name", "Reconcile Test")
	reconcileGit(t, root, "config", "user.email", "reconcile@example.invalid")
	if err := os.Mkdir(filepath.Join(root, ".changes"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "CHANGELOG.md"), []byte("# Changelog\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, ".changes", "fix.json"), []byte(`{"type":"fix","semver":"patch","summary":"reconcile test"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	reconcileGit(t, base, "init", "--bare", "-q", bare)
	reconcileGit(t, root, "add", ".")
	reconcileGit(t, root, "commit", "-qm", "source")
	reconcileGit(t, root, "remote", "add", "origin", bare)
	reconcileGit(t, root, "push", "-u", "origin", "main")
	reconcileGit(t, root, "fetch", "origin", "main")
	baseline := strings.TrimSpace(reconcileGit(t, root, "rev-parse", "origin/main"))

	original, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(root); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(original) })
	firstCandidate := reconcileCandidate(t, base, root, bare, baseline)
	identity, err := validateCheckout(firstCandidate)
	if err != nil {
		t.Fatal(err)
	}
	if identity.SourceMain != baseline {
		t.Fatalf("candidate identity has wrong source boundary: %s", identity.SourceMain)
	}
	manifestBytes := reconcileGit(t, root, "--git-dir", bare, "show", "refs/heads/sdlc/release-next:.sdlc/release.json")
	var manifest release.Manifest
	if err := json.Unmarshal([]byte(manifestBytes), &manifest); err != nil {
		t.Fatal(err)
	}
	if err := verifyCandidate(baseline, firstCandidate, &manifest); err != nil {
		t.Fatal(err)
	}

	reconcileGit(t, root, "fetch", "origin", "main", "sdlc/release-next")
	secondCandidate := reconcileCandidate(t, base, root, bare, baseline)
	if secondCandidate != firstCandidate {
		t.Fatalf("idempotent reconcile changed candidate: %s != %s", secondCandidate, firstCandidate)
	}
	reconcileGit(t, root, "--git-dir", bare, "update-ref", "refs/heads/sdlc/release-next", baseline)
	if outcome, _ := reconcileAttempt(t, base, root, baseline); outcome != branchLeaseLost {
		t.Fatalf("stale lease replaced a concurrently changed branch: %v", outcome)
	}
	if got := strings.TrimSpace(reconcileGit(t, root, "--git-dir", bare, "rev-parse", "refs/heads/sdlc/release-next")); got != baseline {
		t.Fatalf("lease failure mutated remote branch: %s", got)
	}
	reconcileGit(t, root, "fetch", "origin", "main", "sdlc/release-next")
	if outcome, retryCandidate := reconcileAttempt(t, base, root, baseline); outcome != candidatePushed || retryCandidate != firstCandidate {
		t.Fatalf("candidate did not recover after fetching the concurrent branch: %v %s", outcome, retryCandidate)
	}
}

func reconcileCandidate(t *testing.T, base, root, bare, baseline string) string {
	t.Helper()
	outcome, candidate := reconcileAttempt(t, base, root, baseline)
	if outcome != candidatePushed {
		t.Fatalf("candidate push failed: %v", outcome)
	}
	remote := exec.Command("git", "--git-dir", bare, "rev-parse", "refs/heads/sdlc/release-next")
	output, err := remote.Output()
	if err != nil || strings.TrimSpace(string(output)) != candidate {
		t.Fatalf("remote release branch mismatch: %s %v", output, err)
	}
	return candidate
}

func reconcileAttempt(t *testing.T, base, root, baseline string) (reconcileOutcome, string) {
	t.Helper()
	worktree := filepath.Join(base, "worktree-"+strings.ReplaceAll(strings.TrimSpace(mustGit(t, root, "rev-parse", "HEAD")), "a", "x"))
	_ = os.RemoveAll(worktree)
	if _, err := git(".", "worktree", "add", "--detach", worktree, baseline); err != nil {
		t.Fatal(err)
	}
	outcome, candidate, err := reconcileSnapshot(worktree, "owner/repo", "main", "sdlc/release-next", baseline)
	removeErr := gitWorktreeRemove(worktree)
	if err != nil {
		t.Fatal(err)
	}
	if removeErr != nil {
		t.Fatal(removeErr)
	}
	return outcome, candidate
}

func mustGit(t *testing.T, directory string, args ...string) string {
	t.Helper()
	return reconcileGit(t, directory, args...)
}

func reconcileGit(t *testing.T, directory string, args ...string) string {
	t.Helper()
	command := exec.Command("git", args...)
	command.Dir = directory
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, output)
	}
	return string(output)
}
