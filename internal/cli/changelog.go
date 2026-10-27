package cli

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/blogle/sdlc/internal/changelog"
	gitrepo "github.com/blogle/sdlc/internal/git"
	"github.com/spf13/cobra"
)

const noFragmentValue = "\x00"

func changelogCommand() *cobra.Command {
	command := &cobra.Command{Use: "changelog", Short: "Validate and generate changelog releases"}
	for _, action := range []string{"check", "plan", "finalize", "generate"} {
		action := action
		var version, date string
		var jsonOutput bool
		var selected []string
		child := &cobra.Command{
			Use: action,
			Args: func(cmd *cobra.Command, args []string) error {
				if len(args) > 0 && !cmd.Flags().Changed("fragment") {
					return fmt.Errorf("unknown argument %q", args[0])
				}
				return nil
			},
			RunE: func(cmd *cobra.Command, args []string) error {
				if cmd.Flags().Changed("fragment") {
					values := make([]string, 0, len(selected)+len(args))
					for _, item := range selected {
						if item != noFragmentValue {
							values = append(values, item)
						}
					}
					selected = append(values, args...)
				}
				return runChangelog(cmd, action, version, date, jsonOutput, selected)
			},
		}
		child.Flags().StringVar(&version, "version", "", "release version")
		child.Flags().StringVar(&date, "date", "", "release date (YYYY-MM-DD)")
		child.Flags().BoolVar(&jsonOutput, "json", false, "emit JSON")
		child.Flags().StringArrayVar(&selected, "fragment", nil, "limit to selected .changes fragments")
		child.Flags().Lookup("fragment").NoOptDefVal = noFragmentValue
		command.AddCommand(child)
	}
	return command
}

func runChangelog(cmd *cobra.Command, action, version, date string, jsonOutput bool, selected []string) error {
	root, err := os.Getwd()
	if err != nil {
		return err
	}
	if action == "generate" {
		head, err := gitrepo.Open(root).Text("rev-parse", "HEAD")
		if err != nil {
			return err
		}
		return runSnapshot(cmd, head, date, jsonOutput)
	}
	entries, err := changelog.ReadFragments(root, selected)
	if err != nil {
		return err
	}
	bump := changelog.Bump(entries)
	switch action {
	case "check":
		_, err = fmt.Fprintf(cmd.OutOrStdout(), "validated %d changelog fragment(s)\n", len(entries))
		return err
	case "plan":
		planned := ""
		if bump != "" {
			planned, err = planVersion(root, bump)
			if err != nil {
				return err
			}
		}
		if jsonOutput {
			return writeCompactJSON(cmd.OutOrStdout(), map[string]any{"release": len(entries) > 0, "bump": nullable(bump), "version": nullable(planned)})
		}
		if bump == "" {
			_, err = fmt.Fprintln(cmd.OutOrStdout(), "no changelog fragments; no release")
		} else {
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "release %s (%s)\n\n%s\n", planned, bump, changelog.Render(entries))
		}
		return err
	case "finalize":
		if len(entries) == 0 {
			_, err = fmt.Fprintln(cmd.OutOrStdout(), "no changelog fragments; no release")
			return err
		}
		if err := changelog.ValidateVersion(version); err != nil {
			return err
		}
		if date == "" {
			date = time.Now().UTC().Format("2006-01-02")
		}
		return finalize(root, version, date, entries, cmd)
	case "generate":
		return nil
	default:
		return fmt.Errorf("unknown changelog action %s", action)
	}
}

func finalize(root, version, date string, entries []changelog.Entry, cmd *cobra.Command) error {
	path := filepath.Join(root, "CHANGELOG.md")
	old, err := os.ReadFile(path)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	if err := os.WriteFile(path, changelog.FinalizeBytes(old, version, date, entries), 0o644); err != nil {
		return err
	}
	for _, entry := range entries {
		if err := os.Remove(filepath.Join(root, entry.Path)); err != nil {
			return err
		}
	}
	_, err = fmt.Fprintf(cmd.OutOrStdout(), "finalized %d fragment(s) as %s\n", len(entries), version)
	return err
}

func nullable(value string) any {
	if value == "" {
		return nil
	}
	return value
}

func planVersion(root, bump string) (string, error) {
	output, err := gitrepo.Open(root).Text("tag", "--list", "v[0-9]*")
	if err != nil {
		output = ""
	}
	return changelog.NextVersion(changelog.HighestVersion(strings.Fields(output)), bump)
}
