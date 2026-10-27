package cli

import (
	"os"

	gitrepo "github.com/blogle/sdlc/internal/git"
	"github.com/blogle/sdlc/internal/github"
	"github.com/blogle/sdlc/internal/release"
	"github.com/blogle/sdlc/internal/ruleset"
	"github.com/spf13/cobra"
)

func gateCommand(action string) *cobra.Command {
	var request release.GateRequest
	var expectedHead string
	command := &cobra.Command{Use: action, Args: cobra.NoArgs, RunE: func(_ *cobra.Command, _ []string) error {
		contract := os.Getenv("SDLC_SOURCE_PATH")
		if contract == "" {
			var err error
			contract, err = os.Getwd()
			if err != nil {
				return err
			}
		}
		policies, err := ruleset.ReadPolicies(contract)
		if err != nil {
			return err
		}
		service := release.Service{
			API: github.Client{}, Repository: gitrepo.Open("."),
			RulesetAttestation: os.Getenv("SDLC_RULESET_ATTESTATION"),
			RulesetToken:       os.Getenv("SDLC_RULESET_TOKEN"),
			CanonicalPolicy:    policies.DefaultBranch,
			AutomatedPolicy:    policies.AutomatedMerges,
		}
		if action == "gate" {
			return service.Gate(request)
		}
		return service.Merge(release.MergeRequest{
			Repository: request.Repository, PRNumber: request.PRNumber,
			ReleaseBotLogin: request.ReleaseBotLogin, AppSlug: request.AppSlug,
			ExpectedHeadSHA: expectedHead,
		})
	}}
	command.Flags().StringVar(&request.Repository, "repo", "", "GitHub OWNER/NAME")
	command.Flags().IntVar(&request.PRNumber, "pr", 0, "pull request number")
	command.Flags().StringVar(&request.ReleaseBotLogin, "release-bot", "", "release bot login")
	command.Flags().StringVar(&request.AppSlug, "app-slug", "", "authenticated release App slug")
	_ = command.MarkFlagRequired("repo")
	_ = command.MarkFlagRequired("pr")
	_ = command.MarkFlagRequired("release-bot")
	if action == "gate" {
		command.Flags().StringVar(&request.HistoricalMergedSHA, "historical-merged-sha", "", "historical squash merge SHA")
	} else {
		command.Flags().StringVar(&expectedHead, "expected-head", "", "expected candidate head SHA")
	}
	return command
}
