package release

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
	"strings"
)

type FragmentBinding struct {
	Path          string          `json:"path"`
	BlobSHA       string          `json:"blob_sha"`
	UnknownFields map[string]bool `json:"-"`
}

func (binding *FragmentBinding) UnmarshalJSON(data []byte) error {
	type fragmentJSON FragmentBinding
	var decoded fragmentJSON
	if err := json.Unmarshal(data, &decoded); err != nil {
		return err
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return err
	}
	unknown := map[string]bool{}
	for key := range fields {
		if key != "path" && key != "blob_sha" {
			unknown[key] = true
		}
	}
	*binding = FragmentBinding(decoded)
	binding.UnknownFields = unknown
	return nil
}

type Manifest struct {
	Schema             int               `json:"schema"`
	Version            string            `json:"version"`
	PriorReleasedBound any               `json:"prior_released_boundary"`
	SourceMainSHA      string            `json:"source_main_sha"`
	Fragments          []FragmentBinding `json:"fragments"`
	ChangelogSHA256    string            `json:"changelog_sha256"`
	GeneratedTree      string            `json:"generated_tree"`
	Publication        map[string]string `json:"publication"`
	UnknownFields      map[string]bool   `json:"-"`
}

func (manifest *Manifest) UnmarshalJSON(data []byte) error {
	type manifestJSON Manifest
	var decoded manifestJSON
	if err := json.Unmarshal(data, &decoded); err != nil {
		return err
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return err
	}
	known := map[string]bool{"schema": true, "version": true, "prior_released_boundary": true, "source_main_sha": true, "fragments": true, "changelog_sha256": true, "generated_tree": true, "publication": true}
	unknown := map[string]bool{}
	for key := range fields {
		if !known[key] {
			unknown[key] = true
		}
	}
	*manifest = Manifest(decoded)
	manifest.UnknownFields = unknown
	return nil
}

type Identity struct {
	Version         string            `json:"version"`
	Commit          string            `json:"commit"`
	SourceMain      string            `json:"source_main"`
	Tree            string            `json:"tree"`
	ChangelogDigest string            `json:"changelog_digest"`
	Fragments       []FragmentBinding `json:"-"`
}

func (identity Identity) MarshalJSON() ([]byte, error) {
	fragments := make([][2]string, len(identity.Fragments))
	for index, fragment := range identity.Fragments {
		fragments[index] = [2]string{fragment.Path, fragment.BlobSHA}
	}
	value := map[string]any{
		"version": identity.Version, "commit": identity.Commit, "source_main": identity.SourceMain,
		"tree": identity.Tree, "changelog_digest": identity.ChangelogDigest, "fragments": fragments,
	}
	var output bytes.Buffer
	encoder := json.NewEncoder(&output)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(output.Bytes(), []byte("\n")), nil
}

var shaPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)
var digestPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)
var semverPattern = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)

func ValidSHA(value string) bool            { return shaPattern.MatchString(value) }
func ValidReleaseVersion(value string) bool { return semverPattern.MatchString(value) }

type Tree struct {
	Parent       string
	TreeSHA      string
	SourceBlobs  map[string]string
	MergedBlobs  map[string]string
	ChangedPaths map[string]bool
	Changelog    []byte
}

type digestRecord struct {
	path, kind string
	content    []byte
}

func GeneratedTreeDigest(changelog []byte, fragments []FragmentBinding) string {
	records := []digestRecord{{path: "CHANGELOG.md", kind: "file", content: changelog}}
	for _, fragment := range fragments {
		records = append(records, digestRecord{path: fragment.Path, kind: "deleted", content: []byte(fragment.BlobSHA)})
	}
	sort.Slice(records, func(i, j int) bool { return records[i].path < records[j].path })
	var payload []byte
	for _, record := range records {
		payload = append(payload, []byte(record.path+"\x00"+record.kind+"\x00")...)
		payload = append(payload, record.content...)
		payload = append(payload, 0)
	}
	digest := sha256.Sum256(payload)
	return hex.EncodeToString(digest[:])
}

func NewManifest(version, source string, prior any, bindings []FragmentBinding, changelog []byte) Manifest {
	digest := sha256.Sum256(changelog)
	return Manifest{
		Schema: 1, Version: version, PriorReleasedBound: prior, SourceMainSHA: source,
		Fragments: bindings, ChangelogSHA256: hex.EncodeToString(digest[:]),
		GeneratedTree: GeneratedTreeDigest(changelog, bindings),
		Publication:   map[string]string{"version": version, "source_main_sha": source},
	}
}

func (m Manifest) JSON() ([]byte, error) {
	encoded, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return nil, fmt.Errorf("encode release manifest: %w", err)
	}
	// The legacy format sorts object keys. MarshalIndent preserves struct order,
	// so encode through maps at the schema boundary to retain its canonical bytes.
	var value map[string]any
	if err := json.Unmarshal(encoded, &value); err != nil {
		return nil, err
	}
	encoded, err = json.MarshalIndent(value, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(encoded, '\n'), nil
}

func ValidateFragmentBindings(bindings []FragmentBinding) error {
	previous := ""
	for _, binding := range bindings {
		if !strings.HasPrefix(binding.Path, ".changes/") || !strings.HasSuffix(binding.Path, ".json") || strings.Contains(strings.TrimPrefix(binding.Path, ".changes/"), "/") {
			return fmt.Errorf("invalid release fragment path %q", binding.Path)
		}
		if binding.Path <= previous || !shaPattern.MatchString(binding.BlobSHA) {
			return fmt.Errorf("release fragment bindings must be unique, sorted, and use full blob SHAs")
		}
		previous = binding.Path
	}
	return nil
}

func ValidateManifestStructure(manifest Manifest) error {
	if manifest.Schema != 1 {
		return fmt.Errorf("release manifest schema must be 1")
	}
	if !semverPattern.MatchString(manifest.Version) {
		return fmt.Errorf("release manifest has an invalid semantic version")
	}
	if len(manifest.Fragments) == 0 {
		return fmt.Errorf("release manifest must contain fragments")
	}
	return ValidateFragmentBindings(manifest.Fragments)
}

func ValidateIdentity(manifest Manifest, mergedSHA string, tree Tree) (Identity, error) {
	if err := ValidateManifestStructure(manifest); err != nil {
		return Identity{}, err
	}
	if !shaPattern.MatchString(mergedSHA) {
		return Identity{}, fmt.Errorf("merged commit must be a 40-character lowercase SHA")
	}
	if !shaPattern.MatchString(manifest.SourceMainSHA) {
		return Identity{}, fmt.Errorf("source_main_sha must be a 40-character lowercase SHA")
	}
	if tree.Parent != manifest.SourceMainSHA {
		return Identity{}, fmt.Errorf("release commit parent does not equal manifest source_main_sha")
	}
	if !shaPattern.MatchString(tree.TreeSHA) {
		return Identity{}, fmt.Errorf("actual merged tree must be a 40-character lowercase SHA")
	}
	for _, fragment := range manifest.Fragments {
		if tree.SourceBlobs[fragment.Path] != fragment.BlobSHA {
			return Identity{}, fmt.Errorf("fragment blob does not match source snapshot: %s", fragment.Path)
		}
		if _, exists := tree.MergedBlobs[fragment.Path]; exists {
			return Identity{}, fmt.Errorf("consumed fragment remains in merged tree: %s", fragment.Path)
		}
	}
	expected := map[string]bool{"CHANGELOG.md": true, ".sdlc/release.json": true}
	for _, fragment := range manifest.Fragments {
		expected[fragment.Path] = true
	}
	if !samePaths(tree.ChangedPaths, expected) {
		return Identity{}, fmt.Errorf("merged release tree differs outside manifest paths")
	}
	if _, exists := tree.MergedBlobs["CHANGELOG.md"]; !exists {
		return Identity{}, fmt.Errorf("merged release tree lacks CHANGELOG.md")
	}
	digest := sha256.Sum256(tree.Changelog)
	changelogDigest := hex.EncodeToString(digest[:])
	if manifest.ChangelogSHA256 != changelogDigest {
		return Identity{}, fmt.Errorf("merged CHANGELOG.md digest does not match manifest")
	}
	if !digestPattern.MatchString(manifest.GeneratedTree) || manifest.GeneratedTree != GeneratedTreeDigest(tree.Changelog, manifest.Fragments) {
		return Identity{}, fmt.Errorf("generated_tree does not match manifest")
	}
	if len(manifest.Publication) != 2 || manifest.Publication["version"] != manifest.Version || manifest.Publication["source_main_sha"] != manifest.SourceMainSHA {
		return Identity{}, fmt.Errorf("manifest publication must contain only version and source_main_sha")
	}
	return Identity{Version: manifest.Version, Commit: mergedSHA, SourceMain: manifest.SourceMainSHA, Tree: tree.TreeSHA, ChangelogDigest: changelogDigest, Fragments: append([]FragmentBinding(nil), manifest.Fragments...)}, nil
}

func samePaths(left, right map[string]bool) bool {
	if len(left) != len(right) {
		return false
	}
	for path := range left {
		if !right[path] {
			return false
		}
	}
	return true
}
