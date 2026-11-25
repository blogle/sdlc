package release

import "fmt"

func ValidatePublisherIdentity(identity Identity, version, mergedSHA, sourceSHA string) error {
	if identity.Version != version || identity.Commit != mergedSHA || identity.SourceMain != sourceSHA {
		return fmt.Errorf("publisher environment does not match the actual merged release identity")
	}
	return nil
}
