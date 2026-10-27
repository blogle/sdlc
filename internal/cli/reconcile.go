package cli

import (
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/blogle/sdlc/internal/changelog"
	gitrepo "github.com/blogle/sdlc/internal/git"
	"github.com/blogle/sdlc/internal/github"
	"github.com/blogle/sdlc/internal/release"
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
	return release.Reconcile(repo, defaultBranch, branch, reconcilePlatform{output: cmd.OutOrStdout()})
}

type reconcilePlatform struct{ output io.Writer }

func (platform reconcilePlatform) CheckRuleset(repo string) error {
	result, err := reconcileRuleset("check", repo, false)
	for range result.Checked {
		fmt.Fprintf(platform.output, "canonical ruleset matches for %s\n", repo)
	}
	return err
}

func (reconcilePlatform) SetupGitAuthentication() error {
	return (github.Client{}).SetupGitAuthentication()
}
func (reconcilePlatform) FetchBranches(defaultBranch, releaseBranch string) {
	_ = gitrepo.Open(".").FetchReleaseBranches(defaultBranch, releaseBranch)
}
func (reconcilePlatform) MainSHA(defaultBranch string) (string, error) {
	return gitrepo.Open(".").Text("rev-parse", "origin/"+defaultBranch)
}

func (platform reconcilePlatform) Candidate(repo, defaultBranch, branch, baseline string) (release.ReconcileOutcome, string, error) {
	worktree, err := os.MkdirTemp("", "sdlc-release-")
	if err != nil {
		return release.NoReleaseCandidate, "", err
	}
	if err := os.Remove(worktree); err != nil {
		return release.NoReleaseCandidate, "", err
	}
	repository := gitrepo.Open(".")
	if err := repository.AddWorktree(worktree, baseline); err != nil {
		return release.NoReleaseCandidate, "", err
	}
	outcome, candidate, err := reconcileSnapshot(worktree, repo, defaultBranch, branch, baseline)
	removeErr := repository.RemoveWorktree(worktree)
	if err != nil {
		return release.NoReleaseCandidate, "", err
	}
	if removeErr != nil {
		return release.NoReleaseCandidate, "", removeErr
	}
	return outcome, candidate, nil
}

func (reconcilePlatform) UpdateReleasePR(repo, defaultBranch, branch, baseline, candidate string) error {
	return updateReleasePR(repo, defaultBranch, branch, baseline, candidate)
}

func reconcileSnapshot(worktree, repo, defaultBranch, branch, baseline string) (release.ReconcileOutcome, string, error) {
	entries, err := changelog.ReadFragments(worktree, nil)
	if err != nil {
		return release.NoReleaseCandidate, "", err
	}
	if len(entries) == 0 {
		if err := retireReleasePR(repo, branch, defaultBranch); err != nil {
			return release.NoReleaseCandidate, "", err
		}
		return release.NoReleaseCandidate, "", nil
	}
	if err := verifyPredecessor(repo, defaultBranch, baseline); err != nil {
		return release.NoReleaseCandidate, "", err
	}
	manifest, err := gitrepo.Open(worktree).Snapshot(baseline, "")
	if err != nil {
		return release.NoReleaseCandidate, "", fmt.Errorf("release reconcile stopped: deterministic snapshot generation failed: %w", err)
	}
	if manifest == nil {
		return release.NoReleaseCandidate, "", fmt.Errorf("release reconcile stopped: deterministic snapshot generation produced no manifest")
	}
	date, err := gitrepo.Open(worktree).Text("show", "-s", "--format=%cI", baseline)
	if err != nil {
		return release.NoReleaseCandidate, "", err
	}
	candidate, pushed, err := gitrepo.Open(worktree).CommitAndPushReleaseCandidate(branch, date)
	if err != nil {
		return release.NoReleaseCandidate, "", err
	}
	if !pushed {
		return release.ReleaseBranchLeaseLost, candidate, nil
	}
	return release.ReleaseCandidatePushed, candidate, nil
}

func verifyPredecessor(repo, defaultBranch, baseline string) error {
	repository := gitrepo.Open(".")
	manifestRaw, err := repository.Bytes("show", "origin/"+defaultBranch+":.sdlc/release.json")
	if err != nil {
		return nil
	}
	manifest, err := release.ParsePredecessorManifest(manifestRaw)
	if err != nil {
		return err
	}
	tag := "v" + manifest.Version
	tagSHA, err := repository.Text("rev-list", "-n1", "refs/tags/"+tag)
	if err != nil {
		tagSHA = ""
	}
	if tagSHA == "" {
		return release.VerifyPredecessor(manifest, "", "", nil, manifestRaw, false, release.PublicationState{})
	}
	parent, parentErr := repository.Text("rev-parse", tagSHA+"^")
	if parentErr != nil {
		parent = ""
	}
	tagged, taggedErr := repository.Bytes("show", tagSHA+":.sdlc/release.json")
	if taggedErr != nil {
		tagged = nil
	}
	_, ancestorErr := repository.Text("merge-base", "--is-ancestor", tagSHA, baseline)
	state, err := (github.Client{}).PublishedRelease(tag, repo)
	if err != nil {
		return fmt.Errorf("release reconcile stopped: release %s has no completed publication ledger", tag)
	}
	return release.VerifyPredecessor(manifest, tagSHA, parent, tagged, manifestRaw, ancestorErr == nil, state)
}

func retireReleasePR(repo, branch, defaultBranch string) error {
	api := github.Client{}
	number, err := api.OpenReleasePR(repo, defaultBranch, branch)
	if err != nil {
		return err
	}
	if strings.TrimSpace(number) == "" {
		return nil
	}
	return api.CloseReleasePR(repo, strings.TrimSpace(number))
}

func updateReleasePR(repo, defaultBranch, branch, baseline, candidate string) error {
	api := github.Client{}
	number, err := api.OpenReleasePR(repo, defaultBranch, branch)
	if err != nil {
		return err
	}
	title := "chore: rolling release candidate"
	body := fmt.Sprintf("Generated by the release App from %s snapshot %s. This PR is bot-owned, excluded from Mergify, and replaceable until merged.\n\nCandidate commit: %s\n\nThe manifest binds the version, source SHA, ordered fragments, changelog digest, generated tree, and publication identity.", defaultBranch, baseline, candidate)
	if strings.TrimSpace(number) != "" {
		return api.EditReleasePR(repo, strings.TrimSpace(number), title, body)
	}
	return api.CreateReleasePR(repo, defaultBranch, branch, title, body)
}
