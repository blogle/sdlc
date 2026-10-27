package cli

import (
	"encoding/json"
	"fmt"
	"os"

	gitrepo "github.com/blogle/sdlc/internal/git"
	nixcli "github.com/blogle/sdlc/internal/nix"
	"github.com/blogle/sdlc/internal/release"
	"github.com/spf13/cobra"
)

func releaseCommand() *cobra.Command {
	root := &cobra.Command{Use: "release", Short: "Coordinate and validate releases"}
	var source, date string
	var jsonOutput bool
	snapshot := &cobra.Command{
		Use: "snapshot", Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error { return runSnapshot(cmd, source, date, jsonOutput) },
	}
	snapshot.Flags().StringVar(&source, "source-sha", "", "exact source commit SHA")
	_ = snapshot.MarkFlagRequired("source-sha")
	snapshot.Flags().StringVar(&date, "date", "", "release date (YYYY-MM-DD)")
	snapshot.Flags().BoolVar(&jsonOutput, "json", false, "emit JSON")
	root.AddCommand(snapshot)
	publish := &cobra.Command{
		Use: "publish <merged-sha>", Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			identity, err := gitrepo.Open(".").ValidateRelease(args[0])
			if err != nil {
				return err
			}
			return writeCompactJSON(cmd.OutOrStdout(), identity)
		},
	}
	root.AddCommand(publish)
	root.AddCommand(ledgerCommand())
	root.AddCommand(receiptsCommand())
	root.AddCommand(invokeCommand())
	root.AddCommand(gateCommand("gate"), gateCommand("merge"))
	root.AddCommand(reconcileCommand())
	return root
}

func ledgerCommand() *cobra.Command {
	root := &cobra.Command{Use: "ledger"}
	var ledgerPath, commit, name, digest string
	artifact := &cobra.Command{Use: "artifact", Args: cobra.NoArgs, RunE: func(_ *cobra.Command, _ []string) error {
		identity, err := gitrepo.Open(".").ValidateRelease(commit)
		if err != nil {
			return err
		}
		ledger, err := release.LoadLedger(ledgerPath, identity)
		if err != nil {
			return err
		}
		if err := ledger.RecordArtifact(name, digest); err != nil {
			return err
		}
		ledger.MarkTagged()
		return ledger.Save(ledgerPath)
	}}
	artifact.Flags().StringVar(&ledgerPath, "ledger", "", "ledger file path")
	artifact.Flags().StringVar(&commit, "commit", "", "merged release commit")
	artifact.Flags().StringVar(&name, "name", "", "artifact name")
	artifact.Flags().StringVar(&digest, "digest", "", "64-character SHA256")
	_ = artifact.MarkFlagRequired("ledger")
	_ = artifact.MarkFlagRequired("commit")
	_ = artifact.MarkFlagRequired("name")
	_ = artifact.MarkFlagRequired("digest")
	var publishedPath string
	published := &cobra.Command{Use: "published", Args: cobra.NoArgs, RunE: func(_ *cobra.Command, _ []string) error {
		data, err := os.ReadFile(publishedPath)
		if err != nil {
			return err
		}
		var stored struct {
			MergedSHA string `json:"merged_sha"`
		}
		if err := json.Unmarshal(data, &stored); err != nil || stored.MergedSHA == "" {
			return fmt.Errorf("artifact ledger is malformed")
		}
		identity, err := gitrepo.Open(".").ValidateRelease(stored.MergedSHA)
		if err != nil {
			return err
		}
		ledger, err := release.LoadLedger(publishedPath, identity)
		if err != nil {
			return err
		}
		if err := ledger.MarkPublished(); err != nil {
			return err
		}
		return ledger.Save(publishedPath)
	}}
	published.Flags().StringVar(&publishedPath, "ledger", "", "ledger file path")
	_ = published.MarkFlagRequired("ledger")
	root.AddCommand(artifact, published)
	return root
}

func receiptsCommand() *cobra.Command {
	var ledgerPath, receiptsPath, version, merged, source string
	command := &cobra.Command{Use: "receipts", Args: cobra.NoArgs, RunE: func(_ *cobra.Command, _ []string) error {
		receiptBytes, err := os.ReadFile(receiptsPath)
		if err != nil {
			return err
		}
		var receipts release.Receipts
		if err := json.Unmarshal(receiptBytes, &receipts); err != nil {
			return fmt.Errorf("publication receipts are invalid JSON: %w", err)
		}
		identity, err := gitrepo.Open(".").ValidateRelease(merged)
		if err != nil {
			return err
		}
		if identity.Version != version || identity.SourceMain != source {
			return fmt.Errorf("publication receipt inputs do not match the merged release manifest")
		}
		ledgerBytes, err := os.ReadFile(ledgerPath)
		if err != nil {
			return err
		}
		var stored release.Ledger
		if err := json.Unmarshal(ledgerBytes, &stored); err != nil || stored.MergedSHA == "" {
			return fmt.Errorf("artifact ledger is malformed")
		}
		ledger, err := release.LoadLedger(ledgerPath, identity)
		if err != nil {
			return err
		}
		if stored.Version != identity.Version || stored.MergedSHA != identity.Commit || stored.SourceMainSHA != identity.SourceMain || stored.TreeSHA != identity.Tree || stored.ChangelogSHA256 != identity.ChangelogDigest {
			return fmt.Errorf("publication receipts do not match the release identity")
		}
		if err := release.ApplyReceipts(ledger, receipts, identity); err != nil {
			return err
		}
		return ledger.Save(ledgerPath)
	}}
	command.Flags().StringVar(&ledgerPath, "ledger", "", "ledger file path")
	command.Flags().StringVar(&receiptsPath, "receipts", "", "receipts JSON path")
	command.Flags().StringVar(&version, "version", "", "release version")
	command.Flags().StringVar(&merged, "merged-sha", "", "merged release SHA")
	command.Flags().StringVar(&source, "source-sha", "", "source SHA")
	for _, flag := range []string{"ledger", "receipts", "version", "merged-sha", "source-sha"} {
		_ = command.MarkFlagRequired(flag)
	}
	return command
}

func invokeCommand() *cobra.Command {
	var flake, system string
	command := &cobra.Command{Use: "invoke", Args: cobra.NoArgs, RunE: func(_ *cobra.Command, _ []string) error { return invokeConsumerPublish(flake, system) }}
	command.Flags().StringVar(&flake, "consumer-flake", ".", "consumer flake path")
	command.Flags().StringVar(&system, "system", "x86_64-linux", "consumer Nix system")
	return command
}

func invokeConsumerPublish(consumerFlake, system string) error {
	version := os.Getenv("SDLC_RELEASE_VERSION")
	merged, source := os.Getenv("SDLC_RELEASE_MERGED_SHA"), os.Getenv("SDLC_RELEASE_SOURCE_SHA")
	receipts := os.Getenv("SDLC_RELEASE_RECEIPTS")
	if !release.ValidReleaseVersion(version) {
		return fmt.Errorf("SDLC_RELEASE_VERSION must be a semantic version")
	}
	if !release.ValidSHA(merged) {
		return fmt.Errorf("SDLC_RELEASE_MERGED_SHA must be a 40-character lowercase SHA")
	}
	if !release.ValidSHA(source) {
		return fmt.Errorf("SDLC_RELEASE_SOURCE_SHA must be a 40-character lowercase SHA")
	}
	if receipts == "" {
		return fmt.Errorf("SDLC_RELEASE_RECEIPTS is required")
	}
	identity, err := gitrepo.Open(".").ValidateRelease(merged)
	if err != nil {
		return err
	}
	if err := release.ValidatePublisherIdentity(identity, version, merged, source); err != nil {
		return err
	}
	return nixcli.InvokeConsumerPublish(consumerFlake, system)
}

func runSnapshot(cmd *cobra.Command, source, date string, jsonOutput bool) error {
	root, err := os.Getwd()
	if err != nil {
		return err
	}
	manifest, err := gitrepo.Open(root).Snapshot(source, date)
	if err != nil {
		return err
	}
	return printSnapshot(cmd, manifest, jsonOutput)
}

func printSnapshot(cmd *cobra.Command, manifest *release.Manifest, jsonOutput bool) error {
	if jsonOutput {
		return writeCompactJSON(cmd.OutOrStdout(), map[string]any{"release": manifest != nil, "manifest": manifest})
	}
	if manifest == nil {
		_, err := fmt.Fprintln(cmd.OutOrStdout(), "no changelog fragments; no release")
		return err
	}
	_, err := fmt.Fprintf(cmd.OutOrStdout(), "generated release candidate %s from %s\n", manifest.Version, manifest.SourceMainSHA)
	return err
}
