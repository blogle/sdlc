package release

import (
	"bytes"
	"encoding/json"
	"fmt"
)

type CandidateRepository interface {
	ParentSHA(commit string) (string, error)
	ReplaySnapshot(sourceSHA string) (*Manifest, error)
	ValidateRelease(commit string) (Identity, error)
}

func VerifyCandidate(repository CandidateRepository, sourceSHA, headSHA string, manifest *Manifest) error {
	if manifest == nil {
		return fmt.Errorf("release manifest is required")
	}
	if err := ValidateManifestStructure(*manifest); err != nil {
		return err
	}
	if len(manifest.UnknownFields) != 0 {
		return fmt.Errorf("candidate manifest differs from deterministic coordinator replay")
	}
	for _, fragment := range manifest.Fragments {
		if len(fragment.UnknownFields) != 0 {
			return fmt.Errorf("candidate manifest differs from deterministic coordinator replay")
		}
	}
	if manifest.SourceMainSHA != sourceSHA {
		return fmt.Errorf("manifest source_main_sha does not match current main")
	}
	parent, err := repository.ParentSHA(headSHA)
	if err != nil {
		return err
	}
	if parent != sourceSHA {
		return fmt.Errorf("release head parent is not current main")
	}
	first, err := repository.ReplaySnapshot(sourceSHA)
	if err != nil {
		return err
	}
	second, err := repository.ReplaySnapshot(sourceSHA)
	if err != nil {
		return err
	}
	firstBytes, _ := json.Marshal(first)
	secondBytes, _ := json.Marshal(second)
	if !bytes.Equal(firstBytes, secondBytes) {
		return fmt.Errorf("coordinator snapshot replay is not deterministic")
	}
	candidateBytes, _ := json.Marshal(manifest)
	if !bytes.Equal(firstBytes, candidateBytes) {
		return fmt.Errorf("candidate manifest differs from deterministic coordinator replay")
	}
	identity, err := repository.ValidateRelease(headSHA)
	if err != nil {
		return fmt.Errorf("shared coordinator tree verification failed: %w", err)
	}
	if identity.SourceMain != sourceSHA {
		return fmt.Errorf("manifest source_main_sha does not match current main")
	}
	return nil
}
