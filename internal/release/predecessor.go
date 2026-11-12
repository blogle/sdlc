package release

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/blogle/sdlc/internal/changelog"
)

type Predecessor struct {
	Version       string `json:"version"`
	SourceMainSHA string `json:"source_main_sha"`
}

type PublicationState struct {
	IsDraft      *bool
	IsPrerelease *bool
	Assets       []PublicationAsset
}

type PublicationAsset struct {
	Name   string
	Digest string
}

func ParsePredecessorManifest(data []byte) (Predecessor, error) {
	var predecessor Predecessor
	if err := json.Unmarshal(data, &predecessor); err != nil {
		return Predecessor{}, fmt.Errorf("release reconcile stopped: committed release manifest has no valid JSON")
	}
	if !changelog.ValidVersion(predecessor.Version) {
		return Predecessor{}, fmt.Errorf("release reconcile stopped: committed release manifest has no valid version")
	}
	if !shaPattern.MatchString(predecessor.SourceMainSHA) {
		return Predecessor{}, fmt.Errorf("release reconcile stopped: committed release manifest has no valid source SHA")
	}
	return predecessor, nil
}

func VerifyPredecessor(predecessor Predecessor, tagSHA, tagParent string, taggedManifest, mainManifest []byte, tagOnDefaultBranch bool, publication PublicationState) error {
	tag := "v" + predecessor.Version
	if tagSHA == "" {
		return fmt.Errorf("release reconcile stopped: release %s has no committed tag; publication predecessor is unreconciled", tag)
	}
	if tagParent != predecessor.SourceMainSHA {
		return fmt.Errorf("release reconcile stopped: release %s tag does not identify the manifest's merged commit", tag)
	}
	if !bytesEqual(taggedManifest, append([]byte(strings.TrimSpace(string(mainManifest))), '\n')) {
		return fmt.Errorf("release reconcile stopped: release %s tag manifest differs from main", tag)
	}
	if !tagOnDefaultBranch {
		return fmt.Errorf("release reconcile stopped: release %s tag is not on the default branch", tag)
	}
	if publication.IsDraft == nil || *publication.IsDraft || publication.IsPrerelease == nil || *publication.IsPrerelease {
		return fmt.Errorf("release reconcile stopped: release %s is still draft or prerelease", tag)
	}
	assetName := fmt.Sprintf("sdlc-source-%s-%s.tar.gz", predecessor.Version, tagSHA)
	for _, asset := range publication.Assets {
		if asset.Name == assetName && ValidSHA256Digest(asset.Digest) {
			return nil
		}
	}
	return fmt.Errorf("release reconcile stopped: release %s lacks its immutable published asset identity", tag)
}

func bytesEqual(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	for index := range a {
		if a[index] != b[index] {
			return false
		}
	}
	return true
}
