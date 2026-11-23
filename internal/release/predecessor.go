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

func VerifyPredecessor(predecessor Predecessor, tagSHA, tagParent string, taggedManifest, mainManifest []byte, tagOnDefaultBranch, tagHistoryConnected bool, publication PublicationState) error {
	tag := "v" + predecessor.Version
	if tagSHA == "" {
		return fmt.Errorf("release reconcile stopped: release %s has no committed tag; publication predecessor is unreconciled", tag)
	}
	if tagParent != predecessor.SourceMainSHA {
		message := fmt.Sprintf("release reconcile stopped: release %s tag does not identify the manifest's merged commit: tag %s has parent %s, manifest source_main_sha is %s", tag, tagSHA, tagParent, predecessor.SourceMainSHA)
		if !tagHistoryConnected {
			message += "; the tag commit and default branch have no common ancestor"
		}
		return fmt.Errorf("%s; preserve the existing tag and publication, reconcile the consumer's recorded history with an operator, then retry only after all predecessor checks pass", message)
	}
	if !bytesEqual(taggedManifest, append([]byte(strings.TrimSpace(string(mainManifest))), '\n')) {
		return fmt.Errorf("release reconcile stopped: release %s tag manifest differs from main", tag)
	}
	if !tagOnDefaultBranch {
		if !tagHistoryConnected {
			return fmt.Errorf("release reconcile stopped: release %s tag is not on the default branch and the tag commit has no common ancestor with it; preserve the existing tag and publication, reconcile the consumer's recorded history with an operator, then retry only after all predecessor checks pass", tag)
		}
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
