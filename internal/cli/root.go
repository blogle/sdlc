package cli

import "github.com/spf13/cobra"

func New() *cobra.Command {
	root := &cobra.Command{
		Use:           "sdlc",
		Short:         "Shared SDLC tooling for consumer repositories",
		SilenceUsage:  true,
		SilenceErrors: true,
	}
	root.AddCommand(changelogCommand())
	return root
}
