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
	IsDraft          *bool
	IsPrerelease     *bool
	Assets           []PublicationAsset
	RetirementMarker []byte
}

type PredecessorFacts struct {
	TagSHA          string
	TagParent       string
	TaggedManifest  []byte
	MainManifest    []byte
	OnDefaultBranch bool
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

func VerifyPredecessor(predecessor Predecessor, facts PredecessorFacts, mainManifest []byte, publication PublicationState) error {
	tag := "v" + predecessor.Version
	if facts.TagSHA == "" {
		return fmt.Errorf("release reconcile stopped: release %s has no committed tag; publication predecessor is unreconciled", tag)
	}
	if facts.TagParent != predecessor.SourceMainSHA {
		return fmt.Errorf("release reconcile stopped: release %s tag does not identify the manifest's merged commit: tag %s has parent %q, manifest source_main_sha is %s; preserve the existing tag and publication, reconcile the consumer's recorded history with an operator, then retry only after all predecessor checks pass", tag, facts.TagSHA, facts.TagParent, predecessor.SourceMainSHA)
	}
	if !bytesEqual(facts.TaggedManifest, append([]byte(strings.TrimSpace(string(mainManifest))), '\n')) {
		return fmt.Errorf("release reconcile stopped: release %s tag manifest differs from main", tag)
	}
	if !facts.OnDefaultBranch {
		return fmt.Errorf("release reconcile stopped: release %s tag is not on the default branch", tag)
	}
	if publication.IsDraft == nil || publication.IsPrerelease == nil {
		return fmt.Errorf("release reconcile stopped: release %s is still draft or prerelease", tag)
	}
	if publication.RetirementMarker != nil {
		if !*publication.IsDraft {
			return fmt.Errorf("release reconcile stopped: release %s has a retirement marker but is not draft", tag)
		}
		if err := ParseRetirementMarker(publication.RetirementMarker, predecessor.Version, predecessor.SourceMainSHA, facts.TagSHA); err != nil {
			return err
		}
	}
	if *publication.IsDraft {
		if publication.RetirementMarker == nil {
			return fmt.Errorf("release reconcile stopped: release %s is still draft or prerelease", tag)
		}
		return nil
	} else if *publication.IsPrerelease {
		return fmt.Errorf("release reconcile stopped: release %s is still draft or prerelease", tag)
	}
	assetName := fmt.Sprintf("sdlc-source-%s-%s.tar.gz", predecessor.Version, facts.TagSHA)
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
