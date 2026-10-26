package cli

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/blogle/sdlc/internal/release"
	"github.com/blogle/sdlc/internal/ruleset"
	"github.com/spf13/cobra"
)

func gateCommand(action string) *cobra.Command {
	var repo, bot, slug, historical, expected string
	var number int
	command := &cobra.Command{Use: action, Args: cobra.NoArgs, RunE: func(_ *cobra.Command, _ []string) error {
		args := gateArgs{repo: repo, pr: number, bot: bot, slug: slug, historical: historical, expected: expected}
		if action == "gate" {
			return runGate(args)
		}
		return runMerge(args)
	}}
	command.Flags().StringVar(&repo, "repo", "", "GitHub OWNER/NAME")
	command.Flags().IntVar(&number, "pr", 0, "pull request number")
	command.Flags().StringVar(&bot, "release-bot", "", "release bot login")
	command.Flags().StringVar(&slug, "app-slug", "", "authenticated release App slug")
	_ = command.MarkFlagRequired("repo")
	_ = command.MarkFlagRequired("pr")
	_ = command.MarkFlagRequired("release-bot")
	if action == "gate" {
		command.Flags().StringVar(&historical, "historical-merged-sha", "", "historical squash merge SHA")
	} else {
		command.Flags().StringVar(&expected, "expected-head", "", "expected candidate head SHA")
	}
	return command
}

type gateArgs struct {
	repo                            string
	pr                              int
	bot, slug, historical, expected string
}

func runGate(args gateArgs) error {
	pr, err := pullRequest(args.repo, args.pr)
	if err != nil {
		return err
	}
	if pr.HeadBranch != release.ReleaseBranch {
		return nil
	}
	login, slug, err := authenticatedApp(args.bot, args.slug, args.repo)
	if err != nil {
		return err
	}
	repository, err := githubJSON("repos/" + args.repo)
	if err != nil {
		return err
	}
	branch := stringValue(repository["default_branch"])
	if args.historical != "" {
		manifest, err := manifestAt(args.repo, pr.HeadSHA)
		if err != nil {
			return err
		}
		headParent, err := git(".", "rev-parse", pr.HeadSHA+"^")
		if err != nil {
			return err
		}
		if manifest.SourceMainSHA == "" || headParent != manifest.SourceMainSHA {
			return fmt.Errorf("historical release head parent does not match manifest source_main_sha")
		}
		mergeParents, err := git(".", "rev-list", "--parents", "-n1", args.historical)
		if err != nil {
			return err
		}
		mergeFields := strings.Fields(mergeParents)
		if len(mergeFields) != 2 || mergeFields[1] != manifest.SourceMainSHA {
			return fmt.Errorf("historical squash merge does not match manifest source_main_sha")
		}
		protection, err := liveProtection(args.repo, branch)
		if err != nil {
			return err
		}
		if err := release.VerifyProtection(protection, release.RequiredChecks); err != nil {
			return err
		}
		checks, err := checksFor(args.repo, pr.HeadSHA)
		if err != nil {
			return err
		}
		for _, name := range release.RequiredChecks {
			if err := release.RequiredCheck(checks, name, pr.HeadSHA, "", release.RequiredCheckApps[name]); err != nil {
				return err
			}
		}
		if err := release.RequiredCheck(checks, release.ReleaseCheck, pr.HeadSHA, slug, 0); err != nil {
			return err
		}
		if err := verifyCandidate(manifest.SourceMainSHA, args.historical, manifest); err != nil {
			return err
		}
		return nil
	}
	main, err := githubJSON("repos/" + args.repo + "/commits/" + branch)
	if err != nil {
		return err
	}
	commit, err := githubJSON("repos/" + args.repo + "/commits/" + pr.HeadSHA)
	if err != nil {
		return err
	}
	parents, _ := commit["parents"].([]any)
	parentSHA := ""
	if len(parents) > 0 {
		parentSHA = stringValue(object(parents[0])["sha"])
	}
	manifest, err := manifestAt(args.repo, pr.HeadSHA)
	if err != nil {
		return err
	}
	protection, err := liveProtection(args.repo, branch)
	if err != nil {
		return err
	}
	checks, err := checksFor(args.repo, pr.HeadSHA)
	if err != nil {
		return err
	}
	gateProtection := protection
	if err := release.VerifyReleaseGate(pr, login, args.bot, args.repo, stringValue(main["sha"]), parentSHA, checks, manifest, gateProtection); err != nil {
		return err
	}
	return verifyCandidate(stringValue(main["sha"]), pr.HeadSHA, manifest)
}

func runMerge(args gateArgs) error {
	pr, err := pullRequest(args.repo, args.pr)
	if err != nil {
		return err
	}
	if pr.HeadBranch != release.ReleaseBranch {
		return fmt.Errorf("target PR is not the release PR")
	}
	_, slug, err := authenticatedApp(args.bot, args.slug, args.repo)
	if err != nil {
		return err
	}
	repository, err := githubJSON("repos/" + args.repo)
	if err != nil {
		return err
	}
	branch := stringValue(repository["default_branch"])
	expected := args.expected
	if expected == "" {
		expected = pr.HeadSHA
	}
	main, err := githubJSON("repos/" + args.repo + "/commits/" + branch)
	if err != nil {
		return err
	}
	mainSHA := stringValue(main["sha"])
	manifest, err := manifestAt(args.repo, expected)
	if err != nil {
		return err
	}
	if err := verifyCandidate(mainSHA, expected, manifest); err != nil {
		return err
	}
	currentPR, err := pullRequest(args.repo, args.pr)
	if err != nil {
		return err
	}
	if currentPR.HeadBranch != release.ReleaseBranch || currentPR.HeadSHA != expected {
		return fmt.Errorf("release head changed before merge")
	}
	if currentPR.AuthorLogin != args.bot || currentPR.AuthorType != "Bot" || currentPR.HeadRepo != args.repo {
		return fmt.Errorf("release PR is not owned by the release App")
	}
	latestMain, err := githubJSON("repos/" + args.repo + "/commits/" + branch)
	if err != nil {
		return err
	}
	if stringValue(latestMain["sha"]) != mainSHA {
		return fmt.Errorf("main advanced before merge; regenerate release candidate")
	}
	parent, err := git(".", "rev-parse", expected+"^")
	if err != nil {
		return err
	}
	if parent != mainSHA {
		return fmt.Errorf("release head parent is not current main")
	}
	protection, err := liveProtection(args.repo, branch)
	if err != nil {
		return err
	}
	if err := release.VerifyProtection(protection, release.RequiredChecks); err != nil {
		return err
	}
	checks, err := checksFor(args.repo, expected)
	if err != nil {
		return err
	}
	for _, name := range release.RequiredChecks {
		if err := release.RequiredCheck(checks, name, expected, "", release.RequiredCheckApps[name]); err != nil {
			return err
		}
	}
	if err := release.RequiredCheck(checks, release.ReleaseCheck, expected, slug, 0); err != nil {
		return err
	}
	payload, _ := json.Marshal(map[string]string{"sha": expected, "merge_method": "squash"})
	command := exec.Command("gh", "api", "--method", "PUT", "repos/"+args.repo+fmt.Sprintf("/pulls/%d/merge", args.pr), "--input", "-")
	command.Stdin = bytes.NewReader(payload)
	response, err := command.Output()
	if err != nil {
		return err
	}
	var mergeResult map[string]any
	if err := json.Unmarshal(response, &mergeResult); err != nil || mergeResult["merged"] != true {
		return fmt.Errorf("GitHub declined release merge: %s", strings.TrimSpace(string(response)))
	}
	return nil
}

func pullRequest(repo string, number int) (release.PullRequest, error) {
	data, err := githubJSON(fmt.Sprintf("repos/%s/pulls/%d", repo, number))
	if err != nil {
		return release.PullRequest{}, err
	}
	head, user := object(data["head"]), object(data["user"])
	headUser, headRepo := object(head["user"]), object(head["repo"])
	return release.PullRequest{Number: int64(number), HeadSHA: stringValue(head["sha"]), HeadBranch: stringValue(head["ref"]), HeadLogin: stringValue(headUser["login"]), HeadType: stringValue(headUser["type"]), HeadRepo: stringValue(headRepo["full_name"]), AuthorLogin: stringValue(user["login"]), AuthorType: stringValue(user["type"])}, nil
}

func authenticatedApp(bot, slug, repo string) (string, string, error) {
	if slug == "" || bot != slug+"[bot]" {
		return "", "", fmt.Errorf("configured release bot does not match the authenticated release App")
	}
	data, err := githubJSON("installation/repositories?per_page=100")
	if err != nil {
		return "", "", err
	}
	repositories, _ := data["repositories"].([]any)
	for _, item := range repositories {
		if stringValue(object(item)["full_name"]) == repo {
			return bot, slug, nil
		}
	}
	return "", "", fmt.Errorf("authenticated release App installation cannot access this repository")
}

func manifestAt(repo, sha string) (*release.Manifest, error) {
	data, err := githubJSON(fmt.Sprintf("repos/%s/contents/.sdlc/release.json?ref=%s", repo, sha))
	if err != nil {
		return nil, err
	}
	content, err := base64.StdEncoding.DecodeString(strings.ReplaceAll(stringValue(data["content"]), "\n", ""))
	if err != nil {
		return nil, fmt.Errorf("release manifest is missing or invalid JSON")
	}
	var manifest release.Manifest
	if err := json.Unmarshal(content, &manifest); err != nil {
		return nil, fmt.Errorf("release manifest is missing or invalid JSON")
	}
	return &manifest, nil
}

func checksFor(repo, sha string) ([]release.Check, error) {
	command := exec.Command("gh", "api", fmt.Sprintf("repos/%s/commits/%s/check-runs?per_page=100", repo, sha), "--paginate", "--slurp")
	output, err := command.Output()
	if err != nil {
		return nil, fmt.Errorf("paginated GitHub check data unavailable for %s", sha)
	}
	var pages []map[string]any
	if err := json.Unmarshal(output, &pages); err != nil || pages == nil {
		return nil, fmt.Errorf("ambiguous paginated GitHub check data for %s", sha)
	}
	checks := make([]release.Check, 0)
	ids := map[int64]bool{}
	for _, page := range pages {
		items, ok := page["check_runs"].([]any)
		if !ok {
			return nil, fmt.Errorf("ambiguous paginated GitHub check data for %s", sha)
		}
		for _, raw := range items {
			item := object(raw)
			id, ok := item["id"].(float64)
			if !ok {
				return nil, fmt.Errorf("ambiguous GitHub check metadata for %s", sha)
			}
			if ids[int64(id)] {
				return nil, fmt.Errorf("duplicate paginated GitHub checks for %s", sha)
			}
			ids[int64(id)] = true
			completed := stringValue(item["completed_at"])
			if item["status"] == "completed" && completed == "" {
				return nil, fmt.Errorf("ambiguous GitHub check metadata for %s", sha)
			}
			app := object(item["app"])
			check := release.Check{Name: stringValue(item["name"]), Conclusion: stringValue(item["conclusion"]), SHA: stringValue(item["head_sha"]), AppSlug: stringValue(app["slug"]), ID: int64(id), CompletedAt: completed}
			appID, _ := app["id"].(float64)
			check.AppID = int64(appID)
			checks = append(checks, check)
		}
	}
	return checks, nil
}

func liveProtection(repo, branch string) (release.Protection, error) {
	var result release.Protection
	result.Contexts, result.CheckApps = map[string]bool{}, map[string]int64{}
	rulesetBytes, err := githubOutput(true, fmt.Sprintf("repos/%s/rulesets?includes_parents=true", repo))
	if err != nil {
		return result, err
	}
	var summaries []map[string]any
	if err := json.Unmarshal(rulesetBytes, &summaries); err != nil {
		return result, fmt.Errorf("invalid GitHub rulesets response")
	}
	classic, classicErr := githubOutput(true, fmt.Sprintf("repos/%s/branches/%s/protection", repo, branch))
	var classicData map[string]any
	if classicErr == nil {
		_ = json.Unmarshal(classic, &classicData)
	}
	status := object(classicData["required_status_checks"])
	contexts, _ := status["contexts"].([]any)
	for _, context := range contexts {
		if name, ok := context.(string); ok {
			result.Contexts[name] = true
		}
	}
	checks, _ := status["checks"].([]any)
	for _, item := range checks {
		row := object(item)
		if name, ok := row["context"].(string); ok {
			id, _ := row["app_id"].(float64)
			result.CheckApps[name] = int64(id)
		}
	}
	if strict, ok := status["strict"].(bool); ok {
		result.Strict = strict
	}
	attestation := map[string]any{}
	if raw := os.Getenv("SDLC_RULESET_ATTESTATION"); raw != "" {
		if err := json.Unmarshal([]byte(raw), &attestation); err != nil {
			return result, fmt.Errorf("canonical ruleset bypass actors are not visible; refusing release merge")
		}
	}
	result.Attestation = attestation
	contract := os.Getenv("SDLC_SOURCE_PATH")
	canonical, err := loadRuleset(contract, "default-branch.json")
	if err != nil {
		return result, err
	}
	automated, err := loadRuleset(contract, "main-updates.json")
	if err != nil {
		return result, err
	}
	var activeCanonical, activeAutomated int
	for _, summary := range summaries {
		id, _ := summary["id"].(float64)
		detail := summary
		if id != 0 {
			body, err := githubOutput(true, fmt.Sprintf("repos/%s/rulesets/%d", repo, int64(id)))
			if err != nil {
				return result, err
			}
			if err := json.Unmarshal(body, &detail); err != nil {
				return result, fmt.Errorf("invalid GitHub ruleset detail")
			}
		}
		if detail["enforcement"] != "active" || (detail["source"] != nil && detail["source"] != repo) {
			continue
		}
		conditions := object(object(detail["conditions"])["ref_name"])
		includes, _ := conditions["include"].([]any)
		if !containsString(includes, "~DEFAULT_BRANCH") && !containsString(includes, "refs/heads/"+branch) {
			continue
		}
		if detail["name"] == "SDLC automated merges" {
			binding := object(attestation["automated_merges"])
			if binding["schema"] != float64(1) || binding["repository"] != repo || binding["ruleset_id"] != detail["id"] || !ruleset.SameTimestamp(binding["updated_at"], detail["updated_at"]) || !equalJSON(binding["bypass_actors"], automated["bypass_actors"]) {
				return result, fmt.Errorf("automated main-update ruleset bypass actors are not attested")
			}
			actors := detail["bypass_actors"]
			if actors != nil && !equalJSON(actors, automated["bypass_actors"]) {
				return result, fmt.Errorf("automated main-update ruleset has an unexpected bypass actor set")
			}
			if !ruleset.Matches(detail, automated, actors == nil) {
				return result, fmt.Errorf("live automated main-update restriction differs from the pinned SDLC policy")
			}
			activeAutomated++
			continue
		}
		if detail["name"] != "SDLC default branch" {
			continue
		}
		actors := detail["bypass_actors"]
		if actors != nil {
			if _, ok := actors.([]any); !ok {
				return result, fmt.Errorf("canonical ruleset bypass actors are malformed; refusing release merge")
			}
		}
		if attestation["repository"] != repo || attestation["schema"] != float64(1) || !equalJSON(attestation["bypass_actors"], []any{}) {
			return result, fmt.Errorf("canonical ruleset bypass actors are not visible; refusing release merge")
		}
		if attestation["ruleset_id"] != detail["id"] || !ruleset.SameTimestamp(attestation["updated_at"], detail["updated_at"]) {
			return result, fmt.Errorf("canonical ruleset attestation does not match its current identity")
		}
		if visible, ok := actors.([]any); ok && len(visible) != 0 {
			return result, fmt.Errorf("canonical ruleset has bypass actors; refusing release merge")
		}
		if !ruleset.Matches(detail, canonical, actors == nil) {
			return result, fmt.Errorf("live canonical ruleset differs from the pinned SDLC policy")
		}
		activeCanonical++
		for _, rule := range arrayOfMaps(detail["rules"]) {
			if rule["type"] != "required_status_checks" {
				continue
			}
			params := object(rule["parameters"])
			items, _ := params["required_status_checks"].([]any)
			for _, raw := range items {
				item := object(raw)
				name := stringValue(item["context"])
				result.Contexts[name] = true
				id, _ := item["integration_id"].(float64)
				result.CheckApps[name] = int64(id)
			}
			if strict, ok := params["strict_required_status_checks_policy"].(bool); ok {
				result.Strict = result.Strict || strict
			}
		}
		result.Rulesets = append(result.Rulesets, detail)
	}
	result.CanonicalMatches = activeCanonical == 1
	result.AutomatedMergesMatches = activeAutomated == 1
	return result, nil
}

func loadRuleset(contract, name string) (map[string]any, error) {
	data, err := os.ReadFile(filepath.Join(contract, "rulesets", name))
	if err != nil {
		return nil, err
	}
	var result map[string]any
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, err
	}
	return result, nil
}

func verifyCandidate(source, head string, manifest *release.Manifest) error {
	if manifest.Schema != 1 {
		return fmt.Errorf("release manifest schema must be 1")
	}
	if len(manifest.UnknownFields) != 0 {
		return fmt.Errorf("candidate manifest differs from deterministic coordinator replay")
	}
	for _, fragment := range manifest.Fragments {
		if len(fragment.UnknownFields) != 0 {
			return fmt.Errorf("candidate manifest differs from deterministic coordinator replay")
		}
	}
	if manifest.SourceMainSHA != source {
		return fmt.Errorf("manifest source_main_sha does not match current main")
	}
	if len(manifest.Fragments) == 0 {
		return fmt.Errorf("release manifest must contain fragments")
	}
	if err := release.ValidateFragmentBindings(manifest.Fragments); err != nil {
		return err
	}
	parent, err := git(".", "rev-parse", head+"^")
	if err != nil {
		return err
	}
	if parent != source {
		return fmt.Errorf("release head parent is not current main")
	}
	if err := replayCandidate(source, head, manifest); err != nil {
		return err
	}
	return nil
}

func replayCandidate(source, head string, manifest *release.Manifest) error {
	worktree, err := os.MkdirTemp("", "release-gate-")
	if err != nil {
		return err
	}
	_ = os.Remove(worktree)
	if _, err := git(".", "worktree", "add", "--detach", worktree, source); err != nil {
		return err
	}
	defer func() { _, _ = git(".", "worktree", "remove", "--force", worktree) }()
	firstManifest, err := replaySnapshot(worktree, source)
	if err != nil {
		return err
	}
	secondManifest, err := replaySnapshot(worktree, source)
	if err != nil {
		return err
	}
	first, _ := json.Marshal(firstManifest)
	second, _ := json.Marshal(secondManifest)
	if !bytes.Equal(first, second) {
		return fmt.Errorf("coordinator snapshot replay is not deterministic")
	}
	expected, _ := json.Marshal(manifest)
	if !bytes.Equal(first, expected) {
		return fmt.Errorf("candidate manifest differs from deterministic coordinator replay")
	}
	identity, err := validateCheckout(head)
	if err != nil {
		return fmt.Errorf("shared coordinator tree verification failed: %w", err)
	}
	if identity.SourceMain != source {
		return fmt.Errorf("manifest source_main_sha does not match current main")
	}
	return nil
}

func replaySnapshot(worktree, source string) (*release.Manifest, error) {
	program, err := os.Executable()
	if err != nil {
		return nil, err
	}
	command := exec.Command(program, "release", "snapshot", "--source-sha", source, "--json")
	command.Dir = worktree
	command.Env = os.Environ()
	output, err := command.Output()
	if err != nil {
		return nil, fmt.Errorf("coordinator snapshot replay failed")
	}
	var replay struct {
		Release  bool              `json:"release"`
		Manifest *release.Manifest `json:"manifest"`
	}
	if err := json.Unmarshal(output, &replay); err != nil || !replay.Release || replay.Manifest == nil {
		return nil, fmt.Errorf("coordinator snapshot produced no release manifest")
	}
	return replay.Manifest, nil
}

func githubJSON(endpoint string) (map[string]any, error) {
	data, err := githubOutput(strings.Contains(endpoint, "rulesets") || strings.Contains(endpoint, "/protection"), endpoint)
	if err != nil {
		return nil, err
	}
	var result map[string]any
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, fmt.Errorf("invalid GitHub API response for %s", endpoint)
	}
	return result, nil
}

func githubOutput(readOnly bool, endpoint string) ([]byte, error) {
	command := exec.Command("gh", "api", endpoint)
	command.Env = os.Environ()
	if readOnly {
		if token := os.Getenv("SDLC_RULESET_TOKEN"); token != "" {
			command.Env = append(command.Env, "GH_TOKEN="+token)
		}
	}
	output, err := command.Output()
	if err != nil {
		return nil, fmt.Errorf("live GitHub API data unavailable for %s", endpoint)
	}
	return output, nil
}

func object(value any) map[string]any {
	result, _ := value.(map[string]any)
	if result == nil {
		return map[string]any{}
	}
	return result
}
func stringValue(value any) string { result, _ := value.(string); return result }
func containsString(values []any, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
func arrayOfMaps(value any) []map[string]any {
	rows, _ := value.([]any)
	result := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		result = append(result, object(row))
	}
	return result
}
