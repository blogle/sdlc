package release

import (
	"strings"
	"testing"
)

func TestHistoricalGateRejectsRetirementMarkerAddedAfterHistoricalSource(t *testing.T) {
	source := strings.Repeat("a", 40)
	main := strings.Repeat("b", 40)
	merged := strings.Repeat("c", 40)
	head := strings.Repeat("d", 40)
	manifest := &Manifest{Version: "1.2.3", SourceMainSHA: source}
	marker := []byte(`{"schema":1,"version":"1.2.3","source_main_sha":"` + source + `","merged_sha":"` + merged + `","reason":"retired after source"}`)
	service := Service{
		API: historicalAPI{main: main, source: source, merged: merged, head: head, manifest: manifest},
		Repository: historicalRepository{
			main: main, marker: marker,
		},
	}
	err := service.Gate(GateRequest{
		Repository:          "owner/repo",
		PRNumber:            1,
		ReleaseBotLogin:     "release[bot]",
		AppSlug:             "release",
		HistoricalMergedSHA: merged,
	})
	if err == nil || !strings.Contains(err.Error(), "abandoned") {
		t.Fatalf("historical replay was not blocked by live marker: %v", err)
	}
}

type historicalAPI struct {
	main, source, merged, head string
	manifest                   *Manifest
}

func (api historicalAPI) PullRequest(string, int) (PullRequest, error) {
	return PullRequest{HeadBranch: ReleaseBranch, HeadSHA: api.head}, nil
}
func (api historicalAPI) AuthenticateInstallation(string, string, string) (string, string, error) {
	return "release[bot]", "release", nil
}
func (api historicalAPI) DefaultBranch(string) (string, error) { return "main", nil }
func (api historicalAPI) Commit(_ string, ref string) (CommitInfo, error) {
	switch ref {
	case "main":
		return CommitInfo{SHA: api.main}, nil
	case api.head:
		return CommitInfo{SHA: api.head, Parents: []string{api.source}}, nil
	case api.merged:
		return CommitInfo{SHA: api.merged, Parents: []string{api.source}}, nil
	default:
		return CommitInfo{}, nil
	}
}
func (api historicalAPI) ManifestAt(string, string) (*Manifest, error) { return api.manifest, nil }
func (historicalAPI) Protection(string, string, string, string) (Protection, error) {
	return Protection{}, nil
}
func (historicalAPI) ChecksFor(string, string) ([]Check, error) { return nil, nil }
func (historicalAPI) PublishedRelease(string, string) (PublicationState, error) {
	return PublicationState{}, nil
}
func (historicalAPI) MergeReleasePR(string, int, string) error { return nil }

type historicalRepository struct {
	main   string
	marker []byte
}

func (repo historicalRepository) ParentSHA(string) (string, error) { return "", nil }
func (repo historicalRepository) ReplaySnapshot(string) (*Manifest, error) {
	return nil, nil
}
func (repo historicalRepository) RetirementMarkerAt(commit, _ string) ([]byte, bool, error) {
	if commit == repo.main {
		return repo.marker, true, nil
	}
	return nil, false, nil
}
func (historicalRepository) PredecessorManifest(string) (*Manifest, bool, error) {
	return nil, false, nil
}
func (historicalRepository) PredecessorFacts(string, string) (PredecessorFacts, error) {
	return PredecessorFacts{}, nil
}
func (historicalRepository) ValidateRelease(string) (Identity, error) { return Identity{}, nil }
