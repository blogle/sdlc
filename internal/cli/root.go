package cli

import (
	"os"
	"os/exec"
	"path/filepath"

	"github.com/blogle/sdlc/internal/consumer"
	"github.com/spf13/cobra"
)

func New() *cobra.Command {
	root := &cobra.Command{
		Use:           "sdlc",
		Short:         "Shared SDLC tooling for consumer repositories",
		SilenceUsage:  true,
		SilenceErrors: true,
	}
	root.CompletionOptions.DisableDefaultCmd = true
	root.AddCommand(changelogCommand())
	root.AddCommand(releaseCommand())
	root.AddCommand(installCommand())
	root.AddCommand(updateCommand())
	root.AddCommand(rulesetCommand())
	root.AddCommand(enableCommand())
	return root
}

func installCommand() *cobra.Command {
	var check bool
	command := &cobra.Command{Use: "install", Args: cobra.NoArgs, RunE: func(_ *cobra.Command, _ []string) error {
		root, err := os.Getwd()
		if err != nil {
			return err
		}
		contract := os.Getenv("SDLC_SOURCE_PATH")
		if contract == "" {
			contract, err = filepath.Abs(".")
			if err != nil {
				return err
			}
		}
		return consumer.Install(root, contract, check)
	}}
	command.Flags().BoolVar(&check, "check", false, "verify generated adapters without changing files")
	return command
}

func updateCommand() *cobra.Command {
	return &cobra.Command{Use: "update", Args: cobra.NoArgs, Short: "advance the SDLC v2 lock and regenerate pinned callers", RunE: func(_ *cobra.Command, _ []string) error {
		if err := runCommand("nix", "flake", "update", "sdlc"); err != nil {
			return err
		}
		if err := runCommand("nix", "run", ".#sdlc", "--", "install"); err != nil {
			return err
		}
		return runCommand("nix", "run", ".#sdlc", "--", "install", "--check")
	}}
}

func runCommand(name string, args ...string) error {
	command := exec.Command(name, args...)
	command.Stdin, command.Stdout, command.Stderr = os.Stdin, os.Stdout, os.Stderr
	return command.Run()
}
