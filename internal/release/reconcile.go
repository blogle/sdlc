package release

import "fmt"

type ReconcileOutcome uint8

const (
	NoReleaseCandidate ReconcileOutcome = iota
	ReleaseCandidatePushed
	ReleaseBranchLeaseLost
)

// ReconcilePort is the concrete set of observations and effects owned by the
// rolling-release workflow. Candidate generation and its lease are one operation.
type ReconcilePort interface {
	CheckRuleset(repository string) error
	SetupGitAuthentication() error
	FetchBranches(defaultBranch, releaseBranch string)
	MainSHA(defaultBranch string) (string, error)
	Candidate(repository, defaultBranch, releaseBranch, baseline string) (ReconcileOutcome, string, error)
	UpdateReleasePR(repository, defaultBranch, releaseBranch, baseline, candidate string) error
}

func Reconcile(repository, defaultBranch, releaseBranch string, port ReconcilePort) error {
	if err := port.CheckRuleset(repository); err != nil {
		return err
	}
	if err := port.SetupGitAuthentication(); err != nil {
		return err
	}
	for attempt := 1; attempt <= 3; attempt++ {
		port.FetchBranches(defaultBranch, releaseBranch)
		baseline, err := port.MainSHA(defaultBranch)
		if err != nil {
			return err
		}
		outcome, candidate, err := port.Candidate(repository, defaultBranch, releaseBranch, baseline)
		if err != nil {
			return err
		}
		switch outcome {
		case NoReleaseCandidate:
			return nil
		case ReleaseBranchLeaseLost:
			if attempt == 3 {
				return fmt.Errorf("release reconcile stopped: release branch changed concurrently after three lease retries")
			}
		case ReleaseCandidatePushed:
			return port.UpdateReleasePR(repository, defaultBranch, releaseBranch, baseline, candidate)
		}
	}
	return fmt.Errorf("release reconcile stopped: release branch changed concurrently after three lease retries")
}
