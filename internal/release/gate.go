package release

import (
	"encoding/json"
	"fmt"
	"sort"

	sdlcruleset "github.com/blogle/sdlc/internal/ruleset"
)

const (
	ReleaseBranch      = "sdlc/release-next"
	ReleaseCheck       = "sdlc / release-gate"
	GitHubActionsAppID = 15368
	MergifyAppID       = 10562
)

var RequiredCheckApps = map[string]int64{
	"sdlc / check":              GitHubActionsAppID,
	"sdlc / policy":             GitHubActionsAppID,
	"Mergify Merge Protections": MergifyAppID,
}

var RequiredChecks = []string{"sdlc / check", "sdlc / policy", "Mergify Merge Protections"}
var ReleaseGateChecks = []string{"sdlc / check", "sdlc / policy"}

type PullRequest struct {
	Number      int64
	HeadSHA     string
	HeadBranch  string
	HeadLogin   string
	HeadType    string
	HeadRepo    string
	AuthorLogin string
	AuthorType  string
}

type Check struct {
	Name        string
	Conclusion  string
	SHA         string
	AppSlug     string
	ID          int64
	CompletedAt string
	AppID       int64
}

type Protection struct {
	Repository  string
	Branch      string
	Rulesets    []map[string]any
	Attestation map[string]any
	Strict      bool
	Contexts    map[string]bool
	CheckApps   map[string]int64
}

type CommitInfo struct {
	SHA     string
	Parents []string
}

type MergeEvidence struct {
	PullRequest     PullRequest
	Repository      string
	ReleaseBotLogin string
	ExpectedHeadSHA string
	MainSHA         string
	ParentSHA       string
	AppSlug         string
	Protection      Protection
	CanonicalPolicy map[string]any
	AutomatedPolicy map[string]any
	Checks          []Check
}

func RequiredCheck(checks []Check, name, sha, appSlug string, appID int64) error {
	candidates := make([]Check, 0)
	for _, check := range checks {
		if check.Name == name && check.SHA == sha && (appSlug == "" || check.AppSlug == appSlug) && (appID == 0 || check.AppID == appID) {
			candidates = append(candidates, check)
		}
	}
	if len(candidates) == 0 {
		suffix := ""
		if appSlug != "" {
			suffix = fmt.Sprintf(" from App %q", appSlug)
		}
		return fmt.Errorf("required check %q%s is not successful for exact head %s", name, suffix, sha)
	}
	for _, candidate := range candidates {
		if candidate.ID == 0 || candidate.CompletedAt == "" {
			return fmt.Errorf("required check %q has ambiguous completion ordering for exact head %s", name, sha)
		}
	}
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].CompletedAt == candidates[j].CompletedAt {
			return candidates[i].ID < candidates[j].ID
		}
		return candidates[i].CompletedAt < candidates[j].CompletedAt
	})
	latest := candidates[len(candidates)-1]
	if latest.Conclusion != "success" {
		return fmt.Errorf("latest required check %q is not successful for exact head %s", name, sha)
	}
	return nil
}

func VerifyProtection(protection Protection, required []string, canonicalPolicy, automatedPolicy map[string]any) error {
	if protection.Contexts == nil {
		protection.Contexts = map[string]bool{}
	}
	if protection.CheckApps == nil {
		protection.CheckApps = map[string]int64{}
	}
	var canonical, automated []map[string]any
	for _, item := range protection.Rulesets {
		if item["enforcement"] != "active" || (item["source"] != nil && item["source"] != protection.Repository) || !appliesToBranch(item, protection.Branch) {
			continue
		}
		switch item["name"] {
		case "SDLC default branch":
			canonical = append(canonical, item)
		case "SDLC automated merges":
			automated = append(automated, item)
		}
	}
	if len(canonical) != 1 {
		return fmt.Errorf("exactly one active canonical SDLC ruleset is required")
	}
	if protection.Attestation == nil {
		return fmt.Errorf("canonical ruleset bypass actors are not attested; refusing release merge")
	}
	canonicalActors := canonical[0]["bypass_actors"]
	if canonicalActors != nil {
		if visible, ok := canonicalActors.([]any); !ok || len(visible) != 0 {
			return fmt.Errorf("release merge requires zero protection bypass actors")
		}
	}
	if !sdlcruleset.AttestsBypassActors(canonical[0], canonicalPolicy, protection.Repository, protection.Attestation, false) {
		return fmt.Errorf("canonical ruleset bypass actors are not attested; refusing release merge")
	}
	if !sdlcruleset.Matches(canonical[0], canonicalPolicy, canonicalActors == nil) {
		return fmt.Errorf("live canonical ruleset differs from the pinned SDLC policy")
	}
	if len(automated) != 1 {
		return fmt.Errorf("automated main-update restriction or its attestation is missing or stale")
	}
	automatedActors := automated[0]["bypass_actors"]
	if !sdlcruleset.AttestsBypassActors(automated[0], automatedPolicy, protection.Repository, protection.Attestation, true) {
		return fmt.Errorf("automated main-update ruleset bypass actors are not attested")
	}
	if automatedActors != nil && !equalJSON(automatedActors, automatedPolicy["bypass_actors"]) {
		return fmt.Errorf("automated main-update ruleset has an unexpected bypass actor set")
	}
	if !sdlcruleset.Matches(automated[0], automatedPolicy, automatedActors == nil) {
		return fmt.Errorf("live automated main-update restriction differs from the pinned SDLC policy")
	}
	if protection.Strict {
		return fmt.Errorf("global strict freshness is enabled; release gate will not change repository policy")
	}
	for _, rule := range objectArray(canonical[0]["rules"]) {
		if rule["type"] != "required_status_checks" {
			continue
		}
		parameters := object(rule["parameters"])
		if strict, ok := parameters["strict_required_status_checks_policy"].(bool); ok {
			protection.Strict = protection.Strict || strict
		}
		items, _ := parameters["required_status_checks"].([]any)
		for _, raw := range items {
			item := object(raw)
			name := stringValue(item["context"])
			protection.Contexts[name] = true
			protection.CheckApps[name] = numericID(item["integration_id"])
		}
	}
	if protection.Strict {
		return fmt.Errorf("global strict freshness is enabled; release gate will not change repository policy")
	}
	for _, name := range required {
		if !protection.Contexts[name] {
			return fmt.Errorf("protected required checks are absent: [%s]", name)
		}
		if protection.CheckApps[name] != RequiredCheckApps[name] {
			return fmt.Errorf("protected required checks are not bound to their trusted publishers: [%s]", name)
		}
	}
	return nil
}

func appliesToBranch(item map[string]any, branch string) bool {
	conditions := object(object(item["conditions"])["ref_name"])
	includes, _ := conditions["include"].([]any)
	return contains(includes, "~DEFAULT_BRANCH") || contains(includes, "refs/heads/"+branch)
}

func object(value any) map[string]any {
	result, _ := value.(map[string]any)
	if result == nil {
		return map[string]any{}
	}
	return result
}

func objectArray(value any) []map[string]any {
	rows, _ := value.([]any)
	result := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		result = append(result, object(row))
	}
	return result
}

func stringValue(value any) string { result, _ := value.(string); return result }
func contains(values []any, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
func numericID(value any) int64 {
	switch value := value.(type) {
	case float64:
		return int64(value)
	case int:
		return int64(value)
	case int64:
		return value
	default:
		return 0
	}
}
func equalJSON(left, right any) bool {
	a, _ := json.Marshal(left)
	b, _ := json.Marshal(right)
	return string(a) == string(b)
}

func VerifyReleaseGate(pr PullRequest, authenticatedLogin, releaseBotLogin, repository string, mainSHA string, parentSHA string, checks []Check, manifest *Manifest, protection Protection, canonicalPolicy, automatedPolicy map[string]any) error {
	if pr.HeadBranch != ReleaseBranch {
		return nil
	}
	if authenticatedLogin != releaseBotLogin || pr.AuthorLogin != releaseBotLogin || pr.AuthorType != "Bot" || pr.HeadRepo != repository {
		return fmt.Errorf("release PR is not owned by the authenticated release App")
	}
	if err := VerifyProtection(protection, RequiredChecks, canonicalPolicy, automatedPolicy); err != nil {
		return err
	}
	if parentSHA != mainSHA {
		return fmt.Errorf("release head parent is not current main")
	}
	for _, name := range ReleaseGateChecks {
		if err := RequiredCheck(checks, name, pr.HeadSHA, "", RequiredCheckApps[name]); err != nil {
			return err
		}
	}
	if manifest == nil {
		return fmt.Errorf("release manifest is required")
	}
	if manifest.SourceMainSHA != mainSHA {
		return fmt.Errorf("manifest source_main_sha does not match current main")
	}
	return nil
}

func AuthorizeReleaseMerge(evidence MergeEvidence) error {
	pr := evidence.PullRequest
	if pr.HeadBranch != ReleaseBranch || pr.HeadSHA != evidence.ExpectedHeadSHA {
		return fmt.Errorf("release head changed before merge")
	}
	if pr.AuthorLogin != evidence.ReleaseBotLogin || pr.AuthorType != "Bot" || pr.HeadRepo != evidence.Repository {
		return fmt.Errorf("release PR is not owned by the release App")
	}
	if evidence.MainSHA != evidence.ParentSHA {
		return fmt.Errorf("release head parent is not current main")
	}
	if err := VerifyProtection(evidence.Protection, RequiredChecks, evidence.CanonicalPolicy, evidence.AutomatedPolicy); err != nil {
		return err
	}
	for _, name := range RequiredChecks {
		if err := RequiredCheck(evidence.Checks, name, evidence.ExpectedHeadSHA, "", RequiredCheckApps[name]); err != nil {
			return err
		}
	}
	return RequiredCheck(evidence.Checks, ReleaseCheck, evidence.ExpectedHeadSHA, evidence.AppSlug, 0)
}
