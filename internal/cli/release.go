package cli

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/blogle/sdlc/internal/changelog"
	"github.com/blogle/sdlc/internal/release"
	"github.com/spf13/cobra"
)

var fullSHA = regexp.MustCompile(`^[0-9a-f]{40}$`)
var releaseVersion = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)
var coordinatorVersion = regexp.MustCompile(`^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)

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
			identity, err := validateCheckout(args[0])
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
		identity, err := validateCheckout(commit)
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
		identity, err := validateCheckout(stored.MergedSHA)
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
		identity, err := validateCheckout(merged)
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
	if !releaseVersion.MatchString(version) {
		return fmt.Errorf("SDLC_RELEASE_VERSION must be a semantic version")
	}
	if !fullSHA.MatchString(merged) {
		return fmt.Errorf("SDLC_RELEASE_MERGED_SHA must be a 40-character lowercase SHA")
	}
	if !fullSHA.MatchString(source) {
		return fmt.Errorf("SDLC_RELEASE_SOURCE_SHA must be a 40-character lowercase SHA")
	}
	if receipts == "" {
		return fmt.Errorf("SDLC_RELEASE_RECEIPTS is required")
	}
	if !regexp.MustCompile(`^[A-Za-z0-9_-]+$`).MatchString(system) {
		return fmt.Errorf("consumer system must be a Nix system identifier")
	}
	identity, err := validateCheckout(merged)
	if err != nil {
		return err
	}
	if identity.Version != version || identity.SourceMain != source {
		return fmt.Errorf("publisher environment does not match the actual merged release identity")
	}
	installable := consumerFlake + "#ci." + system + ".publish.program"
	program, err := commandOutput("nix", "eval", "--no-write-lock-file", "--raw", installable)
	if err != nil {
		return err
	}
	program = strings.TrimSpace(program)
	derivation, err := commandOutput("nix", "eval", "--no-write-lock-file", "--raw", installable, "--apply", "program: builtins.head (builtins.attrNames (builtins.getContext program))")
	if err != nil {
		return err
	}
	derivation = strings.TrimSpace(derivation)
	storeDir, err := commandOutput("nix", "eval", "--no-write-lock-file", "--raw", "--expr", "builtins.storeDir")
	if err != nil {
		return err
	}
	storeDir = strings.TrimSpace(storeDir)
	if !strings.HasPrefix(program, storeDir+"/") || !strings.HasSuffix(derivation, ".drv") {
		return fmt.Errorf("ci.publish.program is not a store-backed executable Nix app")
	}
	if err := runCommand("nix-store", "--realise", derivation); err != nil {
		return err
	}
	info, err := os.Stat(program)
	if err != nil || info.Mode()&0o111 == 0 {
		return fmt.Errorf("ci.publish.program did not build an executable")
	}
	return runCommand(program)
}

func commandOutput(name string, args ...string) (string, error) {
	output, err := exec.Command(name, args...).Output()
	if err != nil {
		return "", err
	}
	return string(output), nil
}

func runSnapshot(cmd *cobra.Command, source, date string, jsonOutput bool) error {
	root, err := os.Getwd()
	if err != nil {
		return err
	}
	manifest, err := generateSnapshot(root, source, date)
	if err != nil {
		return err
	}
	return printSnapshot(cmd, manifest, jsonOutput)
}

func generateSnapshot(root, source, date string) (*release.Manifest, error) {
	if !fullSHA.MatchString(source) {
		return nil, fmt.Errorf("--source-sha must be a full commit SHA")
	}
	if _, err := git(root, "rev-parse", source); err != nil {
		return nil, fmt.Errorf("source SHA %s is not available", source)
	}
	head, err := git(root, "rev-parse", "HEAD")
	if err != nil {
		return nil, err
	}
	if head != source {
		return nil, fmt.Errorf("snapshot must be generated from the source SHA checkout")
	}
	entries, err := changelog.ReadFragments(root, nil)
	if err != nil {
		return nil, err
	}
	if len(entries) == 0 {
		return nil, nil
	}
	previous, err := previousManifest(root, source)
	if err != nil {
		return nil, err
	}
	priorVersion := ""
	var priorBoundary any
	if previous != nil {
		priorVersion = previous.Version
		if previous.SourceMainSHA != "" {
			priorBoundary = previous.SourceMainSHA
		}
	}
	if priorVersion == "" {
		command := exec.Command("git", "tag", "--list", "v[0-9]*")
		command.Dir = root
		tags, _ := command.Output()
		var highest string
		for _, tag := range strings.Fields(string(tags)) {
			if fullCoordinatorSemVersion(tag) && greaterVersion(tag, highest) {
				highest = tag
			}
		}
		priorVersion = highest
	}
	version, err := changelog.NextVersion(priorVersion, changelog.Bump(entries))
	if err != nil {
		return nil, err
	}
	if date == "" {
		date, err = git(root, "show", "-s", "--format=%cs", source)
		if err != nil {
			return nil, err
		}
	}
	bindings := make([]release.FragmentBinding, 0, len(entries))
	for _, entry := range entries {
		blob, err := git(root, "rev-parse", source+":"+entry.Path)
		if err != nil {
			return nil, err
		}
		bindings = append(bindings, release.FragmentBinding{Path: entry.Path, BlobSHA: blob})
	}
	changelogBytes, err := changelog.ReleaseChangelog(root, version, date, entries)
	if err != nil {
		return nil, err
	}
	manifest := release.NewManifest(version, source, priorBoundary, bindings, changelogBytes)
	manifestBytes, err := manifest.JSON()
	if err != nil {
		return nil, err
	}
	if err := os.WriteFile(filepath.Join(root, "CHANGELOG.md"), changelogBytes, 0o644); err != nil {
		return nil, err
	}
	manifestPath := filepath.Join(root, ".sdlc", "release.json")
	if err := os.MkdirAll(filepath.Dir(manifestPath), 0o755); err != nil {
		return nil, err
	}
	if err := os.WriteFile(manifestPath, manifestBytes, 0o644); err != nil {
		return nil, err
	}
	for _, entry := range entries {
		if err := os.Remove(filepath.Join(root, entry.Path)); err != nil {
			return nil, err
		}
	}
	return &manifest, nil
}

func previousManifest(root, source string) (*previousRelease, error) {
	command := exec.Command("git", "show", source+":.sdlc/release.json")
	command.Dir = root
	bytes, err := command.Output()
	if err != nil {
		return nil, nil
	}
	var value previousRelease
	if err := json.Unmarshal(bytes, &value); err != nil {
		return nil, fmt.Errorf(".sdlc/release.json at %s: invalid JSON: %w", source, err)
	}
	if !fullCoordinatorSemVersion(value.Version) {
		return nil, fmt.Errorf(".sdlc/release.json at %s: missing valid version", source)
	}
	return &value, nil
}

type previousRelease struct {
	Version       string `json:"version"`
	SourceMainSHA string `json:"source_main_sha"`
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

func git(root string, args ...string) (string, error) {
	command := exec.Command("git", args...)
	command.Dir = root
	output, err := command.Output()
	if err != nil {
		return "", fmt.Errorf("git cannot prove release invariant: git %s", strings.Join(args, " "))
	}
	return strings.TrimSpace(string(output)), nil
}

func gitBytes(args ...string) ([]byte, error) {
	output, err := exec.Command("git", args...).Output()
	if err != nil {
		return nil, fmt.Errorf("git cannot prove release invariant: git %s", strings.Join(args, " "))
	}
	return output, nil
}

func validateCheckout(commit string) (release.Identity, error) {
	if !fullSHA.MatchString(commit) {
		return release.Identity{}, fmt.Errorf("merged commit must be a 40-character lowercase SHA")
	}
	parents, err := git(".", "rev-list", "--parents", "-n1", commit)
	if err != nil {
		return release.Identity{}, err
	}
	fields := strings.Fields(parents)
	if len(fields) != 2 {
		return release.Identity{}, fmt.Errorf("merged release commit must have exactly one parent")
	}
	parent := fields[1]
	sourceBlobs, err := gitTree(parent)
	if err != nil {
		return release.Identity{}, err
	}
	mergedBlobs, err := gitTree(commit)
	if err != nil {
		return release.Identity{}, err
	}
	changedRaw, err := gitBytes("diff", "--name-only", "-z", parent, commit)
	if err != nil {
		return release.Identity{}, err
	}
	changed := map[string]bool{}
	for _, path := range strings.Split(string(changedRaw), "\x00") {
		if path != "" {
			changed[path] = true
		}
	}
	treeSHA, err := git(".", "rev-parse", commit+"^{tree}")
	if err != nil {
		return release.Identity{}, err
	}
	manifestBytes, err := gitBytes("show", commit+":.sdlc/release.json")
	if err != nil {
		return release.Identity{}, err
	}
	var manifest release.Manifest
	if err := json.Unmarshal(manifestBytes, &manifest); err != nil {
		return release.Identity{}, fmt.Errorf("release manifest is invalid JSON: %w", err)
	}
	changelog, err := gitBytes("show", commit+":CHANGELOG.md")
	if err != nil {
		return release.Identity{}, err
	}
	return release.ValidateIdentity(manifest, commit, release.Tree{
		Parent: parent, TreeSHA: treeSHA, SourceBlobs: sourceBlobs, MergedBlobs: mergedBlobs,
		ChangedPaths: changed, Changelog: changelog,
	})
}

func gitTree(commit string) (map[string]string, error) {
	output, err := gitBytes("ls-tree", "-r", "-z", commit)
	if err != nil {
		return nil, err
	}
	blobs := map[string]string{}
	for _, record := range strings.Split(string(output), "\x00") {
		if record == "" {
			continue
		}
		metadata, path, ok := strings.Cut(record, "\t")
		if !ok {
			return nil, fmt.Errorf("ambiguous git tree record")
		}
		fields := strings.Fields(metadata)
		if len(fields) != 3 {
			return nil, fmt.Errorf("ambiguous git tree metadata")
		}
		if fields[1] == "blob" {
			blobs[path] = fields[2]
		}
	}
	return blobs, nil
}

func fullSemVersion(value string) bool            { return releaseVersion.MatchString(value) }
func fullCoordinatorSemVersion(value string) bool { return coordinatorVersion.MatchString(value) }
