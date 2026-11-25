package release

import (
	"fmt"

	"github.com/blogle/sdlc/internal/changelog"
)

type SourceFragment struct {
	Entry   changelog.Entry
	BlobSHA string
}

type SnapshotInput struct {
	SourceSHA       string
	Date            string
	PreviousVersion string
	PriorBoundary   any
	Tags            []string
	Fragments       []SourceFragment
	OldChangelog    []byte
}

type SnapshotPlan struct {
	Manifest  Manifest
	Changelog []byte
	Consumed  []string
}

// PlanSnapshot contains the release decision and all identity-sensitive output
// bytes. Git and filesystem adapters only supply observed input and apply it.
func PlanSnapshot(input SnapshotInput) (*SnapshotPlan, error) {
	if len(input.Fragments) == 0 {
		return nil, nil
	}
	if input.SourceSHA == "" {
		return nil, fmt.Errorf("snapshot source SHA is required")
	}
	if input.Date == "" {
		return nil, fmt.Errorf("snapshot release date is required")
	}
	entries := make([]changelog.Entry, len(input.Fragments))
	bindings := make([]FragmentBinding, len(input.Fragments))
	consumed := make([]string, len(input.Fragments))
	for index, fragment := range input.Fragments {
		entries[index] = fragment.Entry
		bindings[index] = FragmentBinding{Path: fragment.Entry.Path, BlobSHA: fragment.BlobSHA}
		consumed[index] = fragment.Entry.Path
	}
	if err := ValidateFragmentBindings(bindings); err != nil {
		return nil, err
	}
	previous := input.PreviousVersion
	if previous == "" {
		previous = changelog.HighestVersion(input.Tags)
	}
	version, err := changelog.NextVersion(previous, changelog.Bump(entries))
	if err != nil {
		return nil, err
	}
	updatedChangelog := changelog.FinalizeBytes(input.OldChangelog, version, input.Date, entries)
	manifest := NewManifest(version, input.SourceSHA, input.PriorBoundary, bindings, updatedChangelog)
	manifest.Schema = 1
	return &SnapshotPlan{Manifest: manifest, Changelog: updatedChangelog, Consumed: consumed}, nil
}
