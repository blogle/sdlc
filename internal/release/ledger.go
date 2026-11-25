package release

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

type Ledger struct {
	Schema                   int               `json:"schema"`
	Version                  string            `json:"version"`
	MergedSHA                string            `json:"merged_sha"`
	SourceMainSHA            string            `json:"source_main_sha"`
	TreeSHA                  string            `json:"tree_sha"`
	ChangelogSHA256          string            `json:"changelog_sha256"`
	Fragments                [][2]string       `json:"fragments"`
	Artifacts                map[string]string `json:"artifacts"`
	Tagged                   bool              `json:"tagged"`
	ConsumerReceiptsRecorded bool              `json:"consumer_receipts_recorded"`
	Published                bool              `json:"published"`
}

var hex256 = regexp.MustCompile(`^[0-9a-f]{64}$`)

func ValidSHA256Digest(value string) bool {
	return regexp.MustCompile(`^sha256:[0-9a-f]{64}$`).MatchString(value)
}

func LoadLedger(path string, identity Identity) (*Ledger, error) {
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		fragments := make([][2]string, len(identity.Fragments))
		for i, fragment := range identity.Fragments {
			fragments[i] = [2]string{fragment.Path, fragment.BlobSHA}
		}
		return &Ledger{Schema: 1, Version: identity.Version, MergedSHA: identity.Commit, SourceMainSHA: identity.SourceMain, TreeSHA: identity.Tree, ChangelogSHA256: identity.ChangelogDigest, Fragments: fragments, Artifacts: map[string]string{}}, nil
	}
	if err != nil {
		return nil, err
	}
	var ledger Ledger
	if err := json.Unmarshal(data, &ledger); err != nil {
		return nil, fmt.Errorf("artifact ledger is malformed or has an unsupported schema")
	}
	if ledger.Schema != 1 || ledger.Artifacts == nil {
		return nil, fmt.Errorf("artifact ledger is malformed or has an unsupported schema")
	}
	expected := NewLedger(identity)
	if ledger.Version != expected.Version || ledger.MergedSHA != expected.MergedSHA || ledger.SourceMainSHA != expected.SourceMainSHA || ledger.TreeSHA != expected.TreeSHA || ledger.ChangelogSHA256 != expected.ChangelogSHA256 || !sameFragments(ledger.Fragments, expected.Fragments) {
		return nil, fmt.Errorf("artifact ledger belongs to a different release identity")
	}
	return &ledger, nil
}

func NewLedger(identity Identity) *Ledger {
	fragments := make([][2]string, len(identity.Fragments))
	for i, fragment := range identity.Fragments {
		fragments[i] = [2]string{fragment.Path, fragment.BlobSHA}
	}
	return &Ledger{Schema: 1, Version: identity.Version, MergedSHA: identity.Commit, SourceMainSHA: identity.SourceMain, TreeSHA: identity.Tree, ChangelogSHA256: identity.ChangelogDigest, Fragments: fragments, Artifacts: map[string]string{}}
}

func (ledger *Ledger) RecordArtifact(name, digest string) error {
	if name == "" {
		return fmt.Errorf("artifact name is required")
	}
	if !hex256.MatchString(digest) {
		return fmt.Errorf("artifact digest must be 64 lowercase hexadecimal characters")
	}
	if existing, ok := ledger.Artifacts[name]; ok && existing != digest {
		return fmt.Errorf("artifact digest changed for %s", name)
	}
	ledger.Artifacts[name] = digest
	return nil
}

func (ledger *Ledger) MarkTagged() { ledger.Tagged = true }
func (ledger *Ledger) MarkPublished() error {
	if !ledger.Tagged {
		return fmt.Errorf("cannot publish before the exact tag is recorded")
	}
	if !ledger.ConsumerReceiptsRecorded {
		return fmt.Errorf("cannot publish before consumer artifact receipts are validated")
	}
	ledger.Published = true
	return nil
}

func (ledger *Ledger) Save(path string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(ledger, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	temp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".")
	if err != nil {
		return err
	}
	name := temp.Name()
	defer os.Remove(name)
	if _, err := temp.Write(data); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	if err := os.Rename(name, path); err != nil {
		return err
	}
	directory, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}

type Receipts struct {
	Schema    int               `json:"schema"`
	Artifacts []ReceiptArtifact `json:"artifacts"`
}
type ReceiptArtifact struct {
	Name          string `json:"name"`
	Digest        string `json:"digest"`
	Version       string `json:"version"`
	MergedSHA     string `json:"merged_sha"`
	SourceMainSHA string `json:"source_main_sha"`
}

func ApplyReceipts(ledger *Ledger, receipts Receipts, identity Identity) error {
	if receipts.Schema != 1 || len(receipts.Artifacts) == 0 {
		return fmt.Errorf("publication receipts must be schema 1 with at least one artifact")
	}
	seen := map[string]bool{}
	for _, artifact := range receipts.Artifacts {
		if strings.TrimSpace(artifact.Name) == "" || !regexp.MustCompile(`^sha256:[0-9a-f]{64}$`).MatchString(artifact.Digest) {
			return fmt.Errorf("publication receipt artifact requires name and sha256 digest")
		}
		if seen[artifact.Name] {
			return fmt.Errorf("publication receipt artifact names must be non-empty and unique")
		}
		seen[artifact.Name] = true
		if artifact.Version != identity.Version || artifact.MergedSHA != identity.Commit || artifact.SourceMainSHA != identity.SourceMain {
			return fmt.Errorf("publication receipt artifact identity does not match the release")
		}
		if err := ledger.RecordArtifact(artifact.Name, artifact.Digest[len("sha256:"):]); err != nil {
			return err
		}
	}
	ledger.ConsumerReceiptsRecorded = true
	return nil
}

func sameFragments(left, right [][2]string) bool {
	if len(left) != len(right) {
		return false
	}
	for i := range left {
		if left[i] != right[i] {
			return false
		}
	}
	return true
}
