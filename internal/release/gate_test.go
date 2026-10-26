package release

import "testing"

func TestRequiredCheckUsesLatestExactHeadAndPublisher(t *testing.T) {
	checks := []Check{
		{Name: "sdlc / check", SHA: "head", AppID: GitHubActionsAppID, CompletedAt: "2026-01-01T00:00:00Z", Conclusion: "success", ID: 1},
		{Name: "sdlc / check", SHA: "head", AppID: GitHubActionsAppID, CompletedAt: "2026-01-02T00:00:00Z", Conclusion: "failure", ID: 2},
		{Name: "sdlc / check", SHA: "head", AppID: 7, CompletedAt: "2026-01-03T00:00:00Z", Conclusion: "success", ID: 3},
	}
	if err := RequiredCheck(checks, "sdlc / check", "head", "", GitHubActionsAppID); err == nil {
		t.Fatal("older success improperly overrode newer trusted failure")
	}
	checks[1].Conclusion = "success"
	if err := RequiredCheck(checks, "sdlc / check", "head", "", GitHubActionsAppID); err != nil {
		t.Fatal(err)
	}
	if err := RequiredCheck(checks, "sdlc / check", "other", "", GitHubActionsAppID); err == nil {
		t.Fatal("check for another head authorized this commit")
	}
}

func TestVerifyProtectionFailsClosedOnMissingAttestationAndAppBinding(t *testing.T) {
	p := Protection{Rulesets: []map[string]any{{"name": "SDLC default branch", "enforcement": "active", "id": float64(12), "updated_at": "now", "bypass_actors": []any{}}}, CanonicalMatches: true, AutomatedMergesMatches: true, Contexts: map[string]bool{"sdlc / check": true}, CheckApps: map[string]int64{"sdlc / check": GitHubActionsAppID}}
	if err := VerifyProtection(p, []string{"sdlc / check"}); err == nil {
		t.Fatal("missing attestation was accepted")
	}
	p.Attestation = map[string]any{"ruleset_id": float64(12), "updated_at": "now", "bypass_actors": []any{}}
	if err := VerifyProtection(p, []string{"sdlc / check"}); err != nil {
		t.Fatal(err)
	}
	p.CheckApps["sdlc / check"] = 42
	if err := VerifyProtection(p, []string{"sdlc / check"}); err == nil {
		t.Fatal("wrong publishing App was accepted")
	}
}

func TestVerifyReleaseGateRequiresTrustedBotCurrentParentAndGateChecks(t *testing.T) {
	main, head := "main-sha", "candidate-sha"
	protection := Protection{
		Rulesets:         []map[string]any{{"name": "SDLC default branch", "enforcement": "active", "id": float64(12), "updated_at": "now", "bypass_actors": []any{}}},
		CanonicalMatches: true, AutomatedMergesMatches: true,
		Attestation: map[string]any{"ruleset_id": float64(12), "updated_at": "now", "bypass_actors": []any{}},
		Contexts:    map[string]bool{}, CheckApps: map[string]int64{},
	}
	for name, id := range RequiredCheckApps {
		protection.Contexts[name] = true
		protection.CheckApps[name] = id
	}
	checks := []Check{
		{Name: "sdlc / check", SHA: head, AppID: GitHubActionsAppID, Conclusion: "success", ID: 1, CompletedAt: "2026-01-01T00:00:00Z"},
		{Name: "sdlc / policy", SHA: head, AppID: GitHubActionsAppID, Conclusion: "success", ID: 2, CompletedAt: "2026-01-01T00:00:00Z"},
	}
	pr := PullRequest{HeadSHA: head, HeadBranch: ReleaseBranch, HeadRepo: "owner/repo", AuthorLogin: "release[bot]", AuthorType: "Bot"}
	manifest := &Manifest{Schema: 1, SourceMainSHA: main}
	if err := VerifyReleaseGate(pr, "release[bot]", "release[bot]", "owner/repo", main, main, checks, manifest, protection); err != nil {
		t.Fatal(err)
	}
	pr.AuthorType = "User"
	if err := VerifyReleaseGate(pr, "release[bot]", "release[bot]", "owner/repo", main, main, checks, manifest, protection); err == nil {
		t.Fatal("human-authored candidate was accepted")
	}
	pr.AuthorType = "Bot"
	if err := VerifyReleaseGate(pr, "release[bot]", "release[bot]", "owner/repo", main, "stale-main", checks, manifest, protection); err == nil {
		t.Fatal("candidate against stale main was accepted")
	}
	pr.HeadBranch = "feature"
	if err := VerifyReleaseGate(pr, "", "", "", main, "", nil, nil, Protection{}); err != nil {
		t.Fatalf("normal PR should be a no-op success: %v", err)
	}
}
