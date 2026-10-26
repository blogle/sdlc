package cli

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/blogle/sdlc/internal/ruleset"
	"github.com/spf13/cobra"
)

var repositoryName = regexp.MustCompile(`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`)

func rulesetCommand() *cobra.Command {
	root := &cobra.Command{Use: "ruleset", Short: "Plan, apply, and verify repository rulesets"}
	for _, action := range []string{"plan", "apply", "check"} {
		action := action
		var repo string
		command := &cobra.Command{Use: action, Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			return runRuleset(cmd, action, repo, false, "default-branch.json")
		}}
		command.Flags().StringVar(&repo, "repo", "", "GitHub OWNER/NAME")
		_ = command.MarkFlagRequired("repo")
		root.AddCommand(command)
	}
	return root
}

func enableCommand() *cobra.Command {
	var repo string
	var dryRun bool
	command := &cobra.Command{Use: "enable", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
		return runRuleset(cmd, "apply", repo, dryRun, "default-branch.json")
	}}
	command.Flags().StringVar(&repo, "repo", "", "GitHub OWNER/NAME")
	command.Flags().BoolVar(&dryRun, "dry-run", false, "show the canonical plan without applying it")
	_ = command.MarkFlagRequired("repo")
	return command
}

func runRuleset(cmd *cobra.Command, action, repo string, dryRun bool, filename string) error {
	if !repositoryName.MatchString(repo) {
		return fmt.Errorf("--repo must be OWNER/NAME")
	}
	contract := os.Getenv("SDLC_SOURCE_PATH")
	if contract == "" {
		var err error
		contract, err = os.Getwd()
		if err != nil {
			return err
		}
	}
	desiredBytes, err := os.ReadFile(filepath.Join(contract, "rulesets", filename))
	if err != nil {
		return err
	}
	var desired map[string]any
	if err := json.Unmarshal(desiredBytes, &desired); err != nil {
		return err
	}
	current, err := readRuleset(repo, desired["name"].(string), action == "check" || dryRun)
	if err != nil {
		if dryRun {
			return writeJSON(cmd, map[string]any{"repository": repo, "action": "read-unavailable", "ruleset": desired})
		}
		return err
	}
	equal := false
	if current != nil {
		if action == "check" {
			if attestedRedaction(repo, current, desired, filename) {
				copy := cloneMap(current)
				copy["bypass_actors"] = desired["bypass_actors"]
				current = copy
			}
		}
		equal = ruleset.Matches(current, desired, false)
	}
	operation := "create"
	if current != nil {
		operation = "update"
		if equal {
			operation = "no-op"
		}
	}
	if action == "plan" || dryRun {
		plan := map[string]any{"repository": repo, "action": operation, "ruleset": desired}
		if filename == "default-branch.json" {
			var automated any
			if err := json.Unmarshal(mustRead(filepath.Join(contract, "rulesets", "main-updates.json")), &automated); err != nil {
				return err
			}
			plan["automated_merges"] = automated
		}
		return writeJSON(cmd, plan)
	}
	if action == "check" {
		if !equal {
			return fmt.Errorf("canonical SDLC ruleset is %s on %s; run `sdlc ruleset apply --repo %s`", operation, repo, repo)
		}
		fmt.Fprintf(cmd.OutOrStdout(), "canonical ruleset matches for %s\n", repo)
		if filename == "default-branch.json" {
			return runRuleset(cmd, "check", repo, false, "main-updates.json")
		}
		return nil
	}
	if !equal {
		endpoint, method := "repos/"+repo+"/rulesets", "POST"
		if current != nil {
			endpoint += fmt.Sprintf("/%v", current["id"])
			method = "PUT"
		}
		if err := ghJSONInput(method, endpoint, desired); err != nil {
			return err
		}
	}
	if filename == "default-branch.json" {
		if err := runRuleset(cmd, "apply", repo, false, "main-updates.json"); err != nil {
			return err
		}
	}
	return runRuleset(cmd, "check", repo, false, filename)
}

func readRuleset(repo, name string, readOnly bool) (map[string]any, error) {
	data, err := ghJSONOutput(readOnly, "repos/"+repo+"/rulesets")
	if err != nil {
		return nil, err
	}
	var summaries []map[string]any
	if err := json.Unmarshal(data, &summaries); err != nil {
		return nil, fmt.Errorf("invalid GitHub ruleset list: %w", err)
	}
	var match map[string]any
	for _, item := range summaries {
		if item["name"] == name {
			if match != nil {
				return nil, fmt.Errorf("multiple %s rulesets exist on %s", name, repo)
			}
			match = item
		}
	}
	if match == nil {
		return nil, nil
	}
	id, ok := match["id"].(float64)
	if !ok {
		return nil, fmt.Errorf("ruleset %s has invalid id", name)
	}
	detail, err := ghJSONOutput(readOnly, fmt.Sprintf("repos/%s/rulesets/%d", repo, int64(id)))
	if err != nil {
		return nil, err
	}
	var result map[string]any
	if err := json.Unmarshal(detail, &result); err != nil {
		return nil, err
	}
	return result, nil
}

func ghJSONOutput(readOnly bool, args ...string) ([]byte, error) {
	commandArgs := append([]string{"api"}, args...)
	command := exec.Command("gh", commandArgs...)
	command.Env = os.Environ()
	if readOnly {
		if token := os.Getenv("SDLC_RULESET_TOKEN"); token != "" {
			command.Env = append(command.Env, "GH_TOKEN="+token)
		}
	}
	output, err := command.Output()
	if err != nil {
		return nil, fmt.Errorf("live GitHub API data unavailable for %s", strings.Join(args, " "))
	}
	return output, nil
}

func ghJSONInput(method, endpoint string, payload any) error {
	data, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	command := exec.Command("gh", "api", "--method", method, endpoint, "--input", "-")
	command.Stdin = bytes.NewReader(data)
	command.Stdout, command.Stderr = os.Stdout, os.Stderr
	return command.Run()
}

func attestedRedaction(repo string, current, desired map[string]any, filename string) bool {
	var attestation map[string]any
	if json.Unmarshal([]byte(os.Getenv("SDLC_RULESET_ATTESTATION")), &attestation) != nil {
		return false
	}
	if filename != "default-branch.json" {
		attestation, _ = attestation["automated_merges"].(map[string]any)
	}
	id, idOK := current["id"].(float64)
	if !idOK {
		return false
	}
	attestedID, attestedOK := attestation["ruleset_id"].(float64)
	return attestedOK && attestation["schema"] == float64(1) && attestation["repository"] == repo && int64(attestedID) == int64(id) && ruleset.SameTimestamp(attestation["updated_at"], current["updated_at"]) && equalJSON(attestation["bypass_actors"], desired["bypass_actors"])
}

func writeJSON(cmd *cobra.Command, value any) error {
	encoder := json.NewEncoder(cmd.OutOrStdout())
	encoder.SetIndent("", "  ")
	encoder.SetEscapeHTML(false)
	return encoder.Encode(value)
}
func mustRead(path string) []byte { data, _ := os.ReadFile(path); return data }
func cloneMap(source map[string]any) map[string]any {
	result := make(map[string]any, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}
func equalJSON(left, right any) bool {
	a, _ := json.Marshal(left)
	b, _ := json.Marshal(right)
	return bytes.Equal(a, b)
}
