package cli

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strings"

	"github.com/blogle/sdlc/internal/changelog"
	"github.com/spf13/cobra"
)

func reconcileCommand() *cobra.Command {
	var repo string
	command := &cobra.Command{Use: "reconcile", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error { return reconcile(cmd, repo) }}
	command.Flags().StringVar(&repo, "repo", "", "GitHub OWNER/NAME (defaults to GITHUB_REPOSITORY)")
	return command
}

func reconcile(cmd *cobra.Command, repo string) error {
	if repo == "" {
		repo = os.Getenv("GITHUB_REPOSITORY")
	}
	defaultBranch := os.Getenv("SDLC_DEFAULT_BRANCH")
	if defaultBranch == "" {
		return fmt.Errorf("release reconcile stopped: SDLC_DEFAULT_BRANCH is required")
	}
	branch := os.Getenv("SDLC_RELEASE_BRANCH")
	if branch == "" {
		branch = "sdlc/release-next"
	}
	if repo == "" {
		return fmt.Errorf("release reconcile stopped: GITHUB_REPOSITORY is required")
	}
	if err := runRuleset(cmd, "check", repo, false, "default-branch.json"); err != nil {
		return err
	}
	if err := runCommand("gh", "auth", "setup-git"); err != nil {
		return err
	}
	for attempt := 1; attempt <= 3; attempt++ {
		fetch := exec.Command("git", "fetch", "--prune", "--tags", "origin", defaultBranch, branch)
		fetch.Stdout, fetch.Stderr = os.Stdout, os.Stderr
		_ = fetch.Run()
		baseline, err := git(".", "rev-parse", "origin/"+defaultBranch)
		if err != nil {
			return err
		}
		worktree, err := os.MkdirTemp("", "sdlc-release-")
		if err != nil {
			return err
		}
		_ = os.Remove(worktree)
		if _, err := git(".", "worktree", "add", "--detach", worktree, baseline); err != nil {
			return err
		}
		outcome, candidate, err := reconcileSnapshot(worktree, repo, defaultBranch, branch, baseline)
		removeErr := gitWorktreeRemove(worktree)
		if err != nil {
			return err
		}
		if removeErr != nil {
			return removeErr
		}
		switch outcome {
		case noReleaseCandidate:
			return nil
		case branchLeaseLost:
			if attempt == 3 {
				return fmt.Errorf("release reconcile stopped: release branch changed concurrently after three lease retries")
			}
			continue
		case candidatePushed:
			return updateReleasePR(repo, defaultBranch, branch, baseline, candidate)
		}
	}
	return fmt.Errorf("release reconcile stopped: release branch changed concurrently after three lease retries")
}

type reconcileOutcome uint8

const (
	noReleaseCandidate reconcileOutcome = iota
	candidatePushed
	branchLeaseLost
)

func reconcileSnapshot(worktree, repo, defaultBranch, branch, baseline string) (reconcileOutcome, string, error) {
	entries, err := changelog.ReadFragments(worktree, nil)
	if err != nil {
		return noReleaseCandidate, "", err
	}
	if len(entries) == 0 {
		if err := retireReleasePR(repo, branch, defaultBranch); err != nil {
			return noReleaseCandidate, "", err
		}
		return noReleaseCandidate, "", nil
	}
	if err := verifyPredecessor(repo, defaultBranch, baseline); err != nil {
		return noReleaseCandidate, "", err
	}
	program, err := os.Executable()
	if err != nil {
		return noReleaseCandidate, "", err
	}
	snapshot := exec.Command(program, "release", "snapshot", "--source-sha", baseline, "--json")
	snapshot.Dir = worktree
	output, err := snapshot.Output()
	if err != nil {
		return noReleaseCandidate, "", fmt.Errorf("release reconcile stopped: deterministic snapshot generation failed")
	}
	var result struct {
		Release  bool            `json:"release"`
		Manifest json.RawMessage `json:"manifest"`
	}
	var manifest map[string]json.RawMessage
	if err := json.Unmarshal(output, &result); err != nil || !result.Release || len(result.Manifest) == 0 || string(result.Manifest) == "null" || json.Unmarshal(result.Manifest, &manifest) != nil || manifest == nil {
		return noReleaseCandidate, "", fmt.Errorf("release reconcile stopped: deterministic snapshot generation produced no manifest")
	}
	if _, err := gitAt(worktree, "config", "user.name", "sdlc-release[bot]"); err != nil {
		return noReleaseCandidate, "", err
	}
	if _, err := gitAt(worktree, "config", "user.email", "sdlc-release[bot]@users.noreply.github.com"); err != nil {
		return noReleaseCandidate, "", err
	}
	if _, err := gitAt(worktree, "add", "CHANGELOG.md", ".sdlc/release.json", ".changes"); err != nil {
		return noReleaseCandidate, "", err
	}
	date, err := gitAt(worktree, "show", "-s", "--format=%cI", baseline)
	if err != nil {
		return noReleaseCandidate, "", err
	}
	commit := exec.Command("git", "commit", "-m", "docs: prepare rolling release candidate")
	commit.Dir = worktree
	commit.Env = append(os.Environ(), "GIT_AUTHOR_DATE="+date, "GIT_COMMITTER_DATE="+date)
	if output, err := commit.CombinedOutput(); err != nil {
		return noReleaseCandidate, "", fmt.Errorf("create deterministic release candidate: %w: %s", err, strings.TrimSpace(string(output)))
	}
	candidate, err := gitAt(worktree, "rev-parse", "HEAD")
	if err != nil {
		return noReleaseCandidate, "", err
	}
	lease := "--force-with-lease=refs/heads/" + branch + ":"
	old, err := git(".", "rev-parse", "--verify", "refs/remotes/origin/"+branch)
	if err == nil {
		lease += old
	}
	push := exec.Command("git", "-C", worktree, "push", lease, "origin", "HEAD:refs/heads/"+branch)
	if err := push.Run(); err != nil {
		return branchLeaseLost, candidate, nil
	}
	return candidatePushed, candidate, nil
}

func verifyPredecessor(repo, defaultBranch, baseline string) error {
	manifestRaw, err := gitBytes("show", "origin/"+defaultBranch+":.sdlc/release.json")
	if err != nil {
		return nil
	}
	var manifest struct {
		Version       string `json:"version"`
		SourceMainSHA string `json:"source_main_sha"`
	}
	if err := json.Unmarshal(manifestRaw, &manifest); err != nil {
		return fmt.Errorf("release reconcile stopped: committed release manifest has no valid JSON")
	}
	if !fullCoordinatorSemVersion(manifest.Version) {
		return fmt.Errorf("release reconcile stopped: committed release manifest has no valid version")
	}
	if !fullSHA.MatchString(manifest.SourceMainSHA) {
		return fmt.Errorf("release reconcile stopped: committed release manifest has no valid source SHA")
	}
	tag := "v" + manifest.Version
	tagSHA, err := git(".", "rev-list", "-n1", "refs/tags/"+tag)
	if err != nil || tagSHA == "" {
		return fmt.Errorf("release reconcile stopped: release %s has no committed tag; publication predecessor is unreconciled", tag)
	}
	parent, err := git(".", "rev-parse", tagSHA+"^")
	if err != nil || parent != manifest.SourceMainSHA {
		return fmt.Errorf("release reconcile stopped: release %s tag does not identify the manifest's merged commit", tag)
	}
	tagged, err := gitBytes("show", tagSHA+":.sdlc/release.json")
	if err != nil || !bytesEqual(tagged, append([]byte(strings.TrimSpace(string(manifestRaw))), '\n')) {
		return fmt.Errorf("release reconcile stopped: release %s tag manifest differs from main", tag)
	}
	if _, err := git(".", "merge-base", "--is-ancestor", tagSHA, baseline); err != nil {
		return fmt.Errorf("release reconcile stopped: release %s tag is not on %s", tag, defaultBranch)
	}
	stateRaw, err := commandOutput("gh", "release", "view", tag, "--repo", repo, "--json", "isDraft,isPrerelease,assets")
	if err != nil {
		return fmt.Errorf("release reconcile stopped: release %s has no completed publication ledger", tag)
	}
	var state map[string]any
	if err := json.Unmarshal([]byte(stateRaw), &state); err != nil || state["isDraft"] != false || state["isPrerelease"] != false {
		return fmt.Errorf("release reconcile stopped: release %s is still draft or prerelease", tag)
	}
	assetName := fmt.Sprintf("sdlc-source-%s-%s.tar.gz", manifest.Version, tagSHA)
	assets, ok := state["assets"].([]any)
	if !ok {
		return fmt.Errorf("release reconcile stopped: release %s lacks its immutable published asset identity", tag)
	}
	for _, item := range assets {
		asset := object(item)
		if stringValue(asset["name"]) == assetName && regexp.MustCompile(`^sha256:[0-9a-f]{64}$`).MatchString(stringValue(asset["digest"])) {
			return nil
		}
	}
	return fmt.Errorf("release reconcile stopped: release %s lacks its immutable published asset identity", tag)
}

func retireReleasePR(repo, branch, defaultBranch string) error {
	number, err := commandOutput("gh", "pr", "list", "--repo", repo, "--state", "open", "--base", defaultBranch, "--head", branch, "--json", "number", "--jq", ".[0].number")
	if err != nil {
		return err
	}
	if strings.TrimSpace(number) == "" {
		return nil
	}
	return runCommand("gh", "pr", "close", strings.TrimSpace(number), "--repo", repo, "--comment", "No unreleased fragments remain; candidate retired.")
}

func updateReleasePR(repo, defaultBranch, branch, baseline, candidate string) error {
	number, err := commandOutput("gh", "pr", "list", "--repo", repo, "--state", "open", "--base", defaultBranch, "--head", branch, "--json", "number", "--jq", ".[0].number")
	if err != nil {
		return err
	}
	title := "chore: rolling release candidate"
	body := fmt.Sprintf("Generated by the release App from %s snapshot %s. This PR is bot-owned, excluded from Mergify, and replaceable until merged.\n\nCandidate commit: %s\n\nThe manifest binds the version, source SHA, ordered fragments, changelog digest, generated tree, and publication identity.", defaultBranch, baseline, candidate)
	if strings.TrimSpace(number) != "" {
		return runCommand("gh", "pr", "edit", strings.TrimSpace(number), "--repo", repo, "--title", title, "--body", body, "--add-label", "release")
	}
	return runCommand("gh", "pr", "create", "--repo", repo, "--base", defaultBranch, "--head", branch, "--title", title, "--body", body, "--label", "release")
}

func gitAt(directory string, args ...string) (string, error) {
	command := exec.Command("git", args...)
	command.Dir = directory
	output, err := command.Output()
	if err != nil {
		return "", fmt.Errorf("git cannot prove release invariant: git %s", strings.Join(args, " "))
	}
	return strings.TrimSpace(string(output)), nil
}

func gitWorktreeRemove(worktree string) error {
	_, err := git(".", "worktree", "remove", "--force", worktree)
	return err
}
func bytesEqual(a, b []byte) bool { return bytes.Equal(a, b) }
