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
