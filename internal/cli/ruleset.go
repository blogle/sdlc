package cli

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/blogle/sdlc/internal/github"
	"github.com/blogle/sdlc/internal/ruleset"
	"github.com/spf13/cobra"
)

func rulesetCommand() *cobra.Command {
	root := &cobra.Command{Use: "ruleset", Short: "Plan, apply, and verify repository rulesets"}
	for _, action := range []string{"plan", "apply", "check"} {
		action := action
		var repository string
		command := &cobra.Command{Use: action, Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			return runRuleset(cmd, action, repository, false)
		}}
		command.Flags().StringVar(&repository, "repo", "", "GitHub OWNER/NAME")
		_ = command.MarkFlagRequired("repo")
		root.AddCommand(command)
	}
	return root
}

func enableCommand() *cobra.Command {
	var repository string
	var dryRun bool
	command := &cobra.Command{Use: "enable", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		return runRuleset(cmd, "apply", repository, dryRun)
	}}
	command.Flags().StringVar(&repository, "repo", "", "GitHub OWNER/NAME")
	command.Flags().BoolVar(&dryRun, "dry-run", false, "show the canonical plan without applying it")
	_ = command.MarkFlagRequired("repo")
	return command
}

func runRuleset(cmd *cobra.Command, action, repository string, dryRun bool) error {
	result, err := reconcileRuleset(action, repository, dryRun)
	for range result.Checked {
		fmt.Fprintf(cmd.OutOrStdout(), "canonical ruleset matches for %s\n", repository)
	}
	if err != nil {
		return err
	}
	if result.Plan != nil {
		plan := map[string]any{"repository": result.Plan.Repository, "action": result.Plan.Action, "ruleset": result.Plan.Ruleset}
		if result.Plan.AutomatedMerges != nil {
			plan["automated_merges"] = result.Plan.AutomatedMerges
		}
		return writeJSON(cmd, plan)
	}
	return nil
}

func reconcileRuleset(action, repository string, dryRun bool) (ruleset.Result, error) {
	contract := os.Getenv("SDLC_SOURCE_PATH")
	if contract == "" {
		var err error
		contract, err = os.Getwd()
		if err != nil {
			return ruleset.Result{}, err
		}
	}
	policies, err := ruleset.ReadPolicies(contract)
	if err != nil {
		return ruleset.Result{}, err
	}
	var attestation map[string]any
	if action == "check" {
		_ = json.Unmarshal([]byte(os.Getenv("SDLC_RULESET_ATTESTATION")), &attestation)
	}
	return ruleset.Reconcile(github.Client{RulesetToken: os.Getenv("SDLC_RULESET_TOKEN")}, ruleset.Request{
		Action: ruleset.Action(action), Repository: repository, Filename: "default-branch.json", DryRun: dryRun,
		Desired: policies.DefaultBranch, Automated: policies.AutomatedMerges, Attestation: attestation,
	})
}

func writeJSON(cmd *cobra.Command, value any) error {
	encoder := json.NewEncoder(cmd.OutOrStdout())
	encoder.SetIndent("", "  ")
	encoder.SetEscapeHTML(false)
	return encoder.Encode(value)
}
