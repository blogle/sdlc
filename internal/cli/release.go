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
			return json.NewEncoder(cmd.OutOrStdout()).Encode(identity)
		},
	}
	root.AddCommand(publish)
	return root
}

func runSnapshot(cmd *cobra.Command, source, date string, jsonOutput bool) error {
	if !fullSHA.MatchString(source) {
		return fmt.Errorf("--source-sha must be a full commit SHA")
	}
	root, err := os.Getwd()
	if err != nil {
		return err
	}
	if _, err := git(root, "rev-parse", source); err != nil {
		return fmt.Errorf("source SHA %s is not available", source)
	}
	head, err := git(root, "rev-parse", "HEAD")
	if err != nil {
		return err
	}
	if head != source {
		return fmt.Errorf("snapshot must be generated from the source SHA checkout")
	}
	entries, err := changelog.ReadFragments(root, nil)
	if err != nil {
		return err
	}
	if len(entries) == 0 {
		return printSnapshot(cmd, nil, jsonOutput)
	}
	previous, err := previousManifest(source)
	if err != nil {
		return err
	}
	priorVersion := ""
	var priorBoundary any
	if previous != nil {
		priorVersion = previous.Version
		priorBoundary = previous.SourceMainSHA
	}
	if priorVersion == "" {
		tags, _ := exec.Command("git", "tag", "--list", "v[0-9]*").Output()
		var highest string
		for _, tag := range strings.Fields(string(tags)) {
			if fullSemVersion(tag) && greaterVersion(tag, highest) {
				highest = tag
			}
		}
		priorVersion = highest
	}
	version, err := changelog.NextVersion(priorVersion, changelog.Bump(entries))
	if err != nil {
		return err
	}
	if date == "" {
		date, err = git(root, "show", "-s", "--format=%cs", source)
		if err != nil {
			return err
		}
	}
	bindings := make([]release.FragmentBinding, 0, len(entries))
	for _, entry := range entries {
		blob, err := git(root, "rev-parse", source+":"+entry.Path)
		if err != nil {
			return err
		}
		bindings = append(bindings, release.FragmentBinding{Path: entry.Path, BlobSHA: blob})
	}
	changelogBytes, err := changelog.ReleaseChangelog(root, version, date, entries)
	if err != nil {
		return err
	}
	manifest := release.NewManifest(version, source, priorBoundary, bindings, changelogBytes)
	manifestBytes, err := manifest.JSON()
	if err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(root, "CHANGELOG.md"), changelogBytes, 0o644); err != nil {
		return err
	}
	manifestPath := filepath.Join(root, ".sdlc", "release.json")
	if err := os.MkdirAll(filepath.Dir(manifestPath), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(manifestPath, manifestBytes, 0o644); err != nil {
		return err
	}
	for _, entry := range entries {
		if err := os.Remove(filepath.Join(root, entry.Path)); err != nil {
			return err
		}
	}
	return printSnapshot(cmd, &manifest, jsonOutput)
}

func previousManifest(source string) (*release.Manifest, error) {
	bytes, err := exec.Command("git", "show", source+":.sdlc/release.json").Output()
	if err != nil {
		return nil, nil
	}
	var value release.Manifest
	if err := json.Unmarshal(bytes, &value); err != nil {
		return nil, fmt.Errorf(".sdlc/release.json at %s: invalid JSON: %w", source, err)
	}
	if !fullSemVersion(value.Version) {
		return nil, fmt.Errorf(".sdlc/release.json at %s: missing valid version", source)
	}
	return &value, nil
}

func printSnapshot(cmd *cobra.Command, manifest *release.Manifest, jsonOutput bool) error {
	if jsonOutput {
		return json.NewEncoder(cmd.OutOrStdout()).Encode(map[string]any{"release": manifest != nil, "manifest": manifest})
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

func fullSemVersion(value string) bool {
	return regexp.MustCompile(`^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`).MatchString(value)
}
