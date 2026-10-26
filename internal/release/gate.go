package release

import (
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
	Rulesets               []map[string]any
	CanonicalMatches       bool
	AutomatedMergesMatches bool
	Attestation            map[string]any
	Strict                 bool
	Contexts               map[string]bool
	CheckApps              map[string]int64
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

func VerifyProtection(protection Protection, required []string) error {
	active := make([]map[string]any, 0)
	for _, ruleset := range protection.Rulesets {
		if ruleset["enforcement"] == "active" && ruleset["name"] == "SDLC default branch" {
			active = append(active, ruleset)
		}
	}
	if len(active) != 1 {
		return fmt.Errorf("exactly one active canonical SDLC ruleset is required")
	}
	if !protection.CanonicalMatches {
		return fmt.Errorf("live canonical ruleset differs from the pinned SDLC policy")
	}
	if protection.Attestation == nil {
		return fmt.Errorf("canonical ruleset bypass actors are not attested; refusing release merge")
	}
	canonicalRuleset := active[0]
	if protection.Attestation["ruleset_id"] != canonicalRuleset["id"] || !sdlcruleset.SameTimestamp(protection.Attestation["updated_at"], canonicalRuleset["updated_at"]) {
		return fmt.Errorf("canonical ruleset attestation does not match its current identity")
	}
	actors, ok := protection.Attestation["bypass_actors"].([]any)
	if !ok || len(actors) != 0 {
		return fmt.Errorf("release merge requires zero protection bypass actors")
	}
	visible := canonicalRuleset["bypass_actors"]
	if visible != nil {
		if visibleActors, ok := visible.([]any); !ok || len(visibleActors) != 0 {
			return fmt.Errorf("release merge requires zero protection bypass actors")
		}
	}
	if protection.Strict {
		return fmt.Errorf("global strict freshness is enabled; release gate will not change repository policy")
	}
	if !protection.AutomatedMergesMatches {
		return fmt.Errorf("automated main-update restriction or its attestation is missing or stale")
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

func VerifyReleaseGate(pr PullRequest, authenticatedLogin, releaseBotLogin, repository string, mainSHA string, parentSHA string, checks []Check, manifest *Manifest, protection Protection) error {
	if pr.HeadBranch != ReleaseBranch {
		return nil
	}
	if authenticatedLogin != releaseBotLogin || pr.AuthorLogin != releaseBotLogin || pr.AuthorType != "Bot" || pr.HeadRepo != repository {
		return fmt.Errorf("release PR is not owned by the authenticated release App")
	}
	if err := VerifyProtection(protection, RequiredChecks); err != nil {
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
