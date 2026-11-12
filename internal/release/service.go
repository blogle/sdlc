package release

import "fmt"

type ReleaseAPI interface {
	PullRequest(repository string, number int) (PullRequest, error)
	AuthenticateInstallation(bot, appSlug, repository string) (string, string, error)
	DefaultBranch(repository string) (string, error)
	Commit(repository, ref string) (CommitInfo, error)
	ManifestAt(repository, sha string) (*Manifest, error)
	Protection(repository, branch, attestation, token string) (Protection, error)
	ChecksFor(repository, sha string) ([]Check, error)
	MergeReleasePR(repository string, number int, sha string) error
}

type GateRequest struct {
	Repository          string
	PRNumber            int
	ReleaseBotLogin     string
	AppSlug             string
	HistoricalMergedSHA string
}

type MergeRequest struct {
	Repository      string
	PRNumber        int
	ReleaseBotLogin string
	AppSlug         string
	ExpectedHeadSHA string
}

type Service struct {
	API                ReleaseAPI
	Repository         CandidateRepository
	RulesetAttestation string
	RulesetToken       string
	CanonicalPolicy    map[string]any
	AutomatedPolicy    map[string]any
}

func (service Service) Gate(request GateRequest) error {
	pr, err := service.API.PullRequest(request.Repository, request.PRNumber)
	if err != nil {
		return err
	}
	if pr.HeadBranch != ReleaseBranch {
		return nil
	}
	login, slug, err := service.API.AuthenticateInstallation(request.ReleaseBotLogin, request.AppSlug, request.Repository)
	if err != nil {
		return err
	}
	branch, err := service.API.DefaultBranch(request.Repository)
	if err != nil {
		return err
	}
	if request.HistoricalMergedSHA != "" {
		return service.historicalGate(request, pr, slug, branch)
	}
	main, err := service.API.Commit(request.Repository, branch)
	if err != nil {
		return err
	}
	head, err := service.API.Commit(request.Repository, pr.HeadSHA)
	if err != nil {
		return err
	}
	parent := ""
	if len(head.Parents) > 0 {
		parent = head.Parents[0]
	}
	manifest, err := service.API.ManifestAt(request.Repository, pr.HeadSHA)
	if err != nil {
		return err
	}
	protection, err := service.protection(request.Repository, branch)
	if err != nil {
		return err
	}
	checks, err := service.API.ChecksFor(request.Repository, pr.HeadSHA)
	if err != nil {
		return err
	}
	if err := VerifyReleaseGate(pr, login, request.ReleaseBotLogin, request.Repository, main.SHA, parent, checks, manifest, protection, service.CanonicalPolicy, service.AutomatedPolicy); err != nil {
		return err
	}
	return VerifyCandidate(service.Repository, main.SHA, pr.HeadSHA, manifest)
}

func (service Service) historicalGate(request GateRequest, pr PullRequest, appSlug, branch string) error {
	manifest, err := service.API.ManifestAt(request.Repository, pr.HeadSHA)
	if err != nil {
		return err
	}
	head, err := service.API.Commit(request.Repository, pr.HeadSHA)
	if err != nil {
		return err
	}
	if len(head.Parents) != 1 || manifest.SourceMainSHA == "" || head.Parents[0] != manifest.SourceMainSHA {
		return fmt.Errorf("historical release head parent does not match manifest source_main_sha")
	}
	merged, err := service.API.Commit(request.Repository, request.HistoricalMergedSHA)
	if err != nil {
		return err
	}
	if len(merged.Parents) != 1 || merged.Parents[0] != manifest.SourceMainSHA {
		return fmt.Errorf("historical squash merge does not match manifest source_main_sha")
	}
	protection, err := service.protection(request.Repository, branch)
	if err != nil {
		return err
	}
	if err := VerifyProtection(protection, RequiredChecks, service.CanonicalPolicy, service.AutomatedPolicy); err != nil {
		return err
	}
	checks, err := service.API.ChecksFor(request.Repository, pr.HeadSHA)
	if err != nil {
		return err
	}
	for _, name := range RequiredChecks {
		if err := RequiredCheck(checks, name, pr.HeadSHA, "", RequiredCheckApps[name]); err != nil {
			return err
		}
	}
	if err := RequiredCheck(checks, ReleaseCheck, pr.HeadSHA, appSlug, 0); err != nil {
		return err
	}
	return VerifyCandidate(service.Repository, manifest.SourceMainSHA, request.HistoricalMergedSHA, manifest)
}

func (service Service) Merge(request MergeRequest) error {
	pr, err := service.API.PullRequest(request.Repository, request.PRNumber)
	if err != nil {
		return err
	}
	if pr.HeadBranch != ReleaseBranch {
		return fmt.Errorf("target PR is not the release PR")
	}
	_, slug, err := service.API.AuthenticateInstallation(request.ReleaseBotLogin, request.AppSlug, request.Repository)
	if err != nil {
		return err
	}
	branch, err := service.API.DefaultBranch(request.Repository)
	if err != nil {
		return err
	}
	expected := request.ExpectedHeadSHA
	if expected == "" {
		expected = pr.HeadSHA
	}
	main, err := service.API.Commit(request.Repository, branch)
	if err != nil {
		return err
	}
	manifest, err := service.API.ManifestAt(request.Repository, expected)
	if err != nil {
		return err
	}
	if err := VerifyCandidate(service.Repository, main.SHA, expected, manifest); err != nil {
		return err
	}
	currentPR, err := service.API.PullRequest(request.Repository, request.PRNumber)
	if err != nil {
		return err
	}
	latestMain, err := service.API.Commit(request.Repository, branch)
	if err != nil {
		return err
	}
	if latestMain.SHA != main.SHA {
		return fmt.Errorf("main advanced before merge; regenerate release candidate")
	}
	parent, err := service.Repository.ParentSHA(expected)
	if err != nil {
		return err
	}
	protection, err := service.protection(request.Repository, branch)
	if err != nil {
		return err
	}
	checks, err := service.API.ChecksFor(request.Repository, expected)
	if err != nil {
		return err
	}
	if err := AuthorizeReleaseMerge(MergeEvidence{
		PullRequest: currentPR, Repository: request.Repository, ReleaseBotLogin: request.ReleaseBotLogin,
		ExpectedHeadSHA: expected, MainSHA: main.SHA, ParentSHA: parent,
		AppSlug: slug, Protection: protection, Checks: checks,
		CanonicalPolicy: service.CanonicalPolicy, AutomatedPolicy: service.AutomatedPolicy,
	}); err != nil {
		return err
	}
	return service.API.MergeReleasePR(request.Repository, request.PRNumber, expected)
}

func (service Service) protection(repository, branch string) (Protection, error) {
	return service.API.Protection(repository, branch, service.RulesetAttestation, service.RulesetToken)
}
