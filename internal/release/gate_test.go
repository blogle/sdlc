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

func TestVerifyProtectionOwnsCanonicalPolicyAndAttestationChecks(t *testing.T) {
	protection, canonical, automated := protectionFixture()
	if err := VerifyProtection(protection, []string{"sdlc / check"}, canonical, automated); err != nil {
		t.Fatal(err)
	}
	wrong := cloneMap(canonical)
	wrong["name"] = "forged policy"
	if err := VerifyProtection(protection, []string{"sdlc / check"}, wrong, automated); err == nil {
		t.Fatal("different canonical policy was accepted")
	}
	protection.Attestation["updated_at"] = "2020-01-01T00:00:00Z"
	if err := VerifyProtection(protection, []string{"sdlc / check"}, canonical, automated); err == nil {
		t.Fatal("stale attestation was accepted")
	}
	protection, canonical, automated = protectionFixture()
	parameters := object(objectArray(protection.Rulesets[0]["rules"])[0]["parameters"])
	for _, raw := range parameters["required_status_checks"].([]any) {
		check := object(raw)
		if check["context"] == "sdlc / check" {
			check["integration_id"] = 42
		}
	}
	if err := VerifyProtection(protection, []string{"sdlc / check"}, canonical, automated); err == nil {
		t.Fatal("wrong publisher App was accepted")
	}
}

func TestVerifyReleaseGateRequiresTrustedBotCurrentParentAndGateChecks(t *testing.T) {
	main, head := "main-sha", "candidate-sha"
	protection, canonical, automated := protectionFixture()
	checks := []Check{
		{Name: "sdlc / check", SHA: head, AppID: GitHubActionsAppID, Conclusion: "success", ID: 1, CompletedAt: "2026-01-01T00:00:00Z"},
		{Name: "sdlc / policy", SHA: head, AppID: GitHubActionsAppID, Conclusion: "success", ID: 2, CompletedAt: "2026-01-01T00:00:00Z"},
	}
	pr := PullRequest{HeadSHA: head, HeadBranch: ReleaseBranch, HeadRepo: "owner/repo", AuthorLogin: "release[bot]", AuthorType: "Bot"}
	manifest := &Manifest{Schema: 1, SourceMainSHA: main}
	if err := VerifyReleaseGate(pr, "release[bot]", "release[bot]", "owner/repo", main, main, checks, manifest, protection, canonical, automated); err != nil {
		t.Fatal(err)
	}
	pr.AuthorType = "User"
	if err := VerifyReleaseGate(pr, "release[bot]", "release[bot]", "owner/repo", main, main, checks, manifest, protection, canonical, automated); err == nil {
		t.Fatal("human-authored candidate was accepted")
	}
	pr.AuthorType = "Bot"
	if err := VerifyReleaseGate(pr, "release[bot]", "release[bot]", "owner/repo", main, "stale-main", checks, manifest, protection, canonical, automated); err == nil {
		t.Fatal("candidate against stale main was accepted")
	}
	pr.HeadBranch = "feature"
	if err := VerifyReleaseGate(pr, "", "", "", main, "", nil, nil, Protection{}, nil, nil); err != nil {
		t.Fatalf("normal PR should be a no-op success: %v", err)
	}
}

func protectionFixture() (Protection, map[string]any, map[string]any) {
	statuses := make([]any, 0, len(RequiredCheckApps))
	for name, appID := range RequiredCheckApps {
		statuses = append(statuses, map[string]any{"context": name, "integration_id": appID})
	}
	conditions := map[string]any{"ref_name": map[string]any{"include": []any{"~DEFAULT_BRANCH"}, "exclude": []any{}}}
	canonical := map[string]any{
		"name": "SDLC default branch", "target": "branch", "enforcement": "active", "bypass_actors": []any{}, "conditions": conditions,
		"rules": []any{map[string]any{"type": "required_status_checks", "parameters": map[string]any{"strict_required_status_checks_policy": false, "required_status_checks": statuses}}},
	}
	automated := map[string]any{"name": "SDLC automated merges", "target": "branch", "enforcement": "active", "bypass_actors": []any{}, "conditions": conditions, "rules": []any{map[string]any{"type": "update"}}}
	canonicalActual, automatedActual := cloneMap(canonical), cloneMap(automated)
	canonicalActual["id"], canonicalActual["updated_at"], canonicalActual["source"] = float64(1), "2026-01-01T00:00:00Z", "owner/repo"
	automatedActual["id"], automatedActual["updated_at"], automatedActual["source"] = float64(2), "2026-01-01T00:00:00Z", "owner/repo"
	attestation := map[string]any{
		"schema": float64(1), "repository": "owner/repo", "ruleset_id": float64(1), "updated_at": "2026-01-01T00:00:00Z", "bypass_actors": []any{},
		"automated_merges": map[string]any{"schema": float64(1), "repository": "owner/repo", "ruleset_id": float64(2), "updated_at": "2026-01-01T00:00:00Z", "bypass_actors": []any{}},
	}
	return Protection{Repository: "owner/repo", Branch: "main", Rulesets: []map[string]any{canonicalActual, automatedActual}, Attestation: attestation, Contexts: map[string]bool{}, CheckApps: map[string]int64{}}, canonical, automated
}

func cloneMap(value map[string]any) map[string]any {
	result := make(map[string]any, len(value))
	for key, item := range value {
		result[key] = item
	}
	return result
}
