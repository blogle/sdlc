package github

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"

	"github.com/blogle/sdlc/internal/release"
)

// Client adapts the GitHub CLI's JSON API to SDLC release evidence. Every
// operation is explicit so release decisions remain testable without GitHub.
type Client struct{ RulesetToken string }

func (Client) PullRequest(repo string, number int) (release.PullRequest, error) {
	data, err := jsonObject(false, "", fmt.Sprintf("repos/%s/pulls/%d", repo, number))
	if err != nil {
		return release.PullRequest{}, err
	}
	head, user := object(data["head"]), object(data["user"])
	headUser, headRepo := object(head["user"]), object(head["repo"])
	return release.PullRequest{
		Number: int64(number), HeadSHA: text(head["sha"]), HeadBranch: text(head["ref"]),
		HeadLogin: text(headUser["login"]), HeadType: text(headUser["type"]), HeadRepo: text(headRepo["full_name"]),
		AuthorLogin: text(user["login"]), AuthorType: text(user["type"]),
	}, nil
}

func (Client) AuthenticateInstallation(bot, slug, repository string) (string, string, error) {
	if slug == "" || bot != slug+"[bot]" {
		return "", "", fmt.Errorf("configured release bot does not match the authenticated release App")
	}
	data, err := jsonObject(false, "", "installation/repositories?per_page=100")
	if err != nil {
		return "", "", err
	}
	repositories, _ := data["repositories"].([]any)
	for _, item := range repositories {
		if text(object(item)["full_name"]) == repository {
			return bot, slug, nil
		}
	}
	return "", "", fmt.Errorf("authenticated release App installation cannot access this repository")
}

func (Client) DefaultBranch(repository string) (string, error) {
	data, err := jsonObject(false, "", "repos/"+repository)
	if err != nil {
		return "", err
	}
	branch := text(data["default_branch"])
	if branch == "" {
		return "", fmt.Errorf("repository response has no default branch")
	}
	return branch, nil
}

func (Client) Commit(repository, ref string) (release.CommitInfo, error) {
	data, err := jsonObject(false, "", fmt.Sprintf("repos/%s/commits/%s", repository, ref))
	if err != nil {
		return release.CommitInfo{}, err
	}
	parents := make([]string, 0)
	for _, item := range objectArray(data["parents"]) {
		parents = append(parents, text(item["sha"]))
	}
	return release.CommitInfo{SHA: text(data["sha"]), Parents: parents}, nil
}

func (Client) ManifestAt(repository, sha string) (*release.Manifest, error) {
	data, err := jsonObject(false, "", fmt.Sprintf("repos/%s/contents/.sdlc/release.json?ref=%s", repository, sha))
	if err != nil {
		return nil, err
	}
	content, err := base64.StdEncoding.DecodeString(strings.ReplaceAll(text(data["content"]), "\n", ""))
	if err != nil {
		return nil, fmt.Errorf("release manifest is missing or invalid JSON")
	}
	var manifest release.Manifest
	if err := json.Unmarshal(content, &manifest); err != nil {
		return nil, fmt.Errorf("release manifest is missing or invalid JSON")
	}
	return &manifest, nil
}

func (Client) ChecksFor(repository, sha string) ([]release.Check, error) {
	command := exec.Command("gh", "api", fmt.Sprintf("repos/%s/commits/%s/check-runs?per_page=100", repository, sha), "--paginate", "--slurp")
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
			completed := text(item["completed_at"])
			if item["status"] == "completed" && completed == "" {
				return nil, fmt.Errorf("ambiguous GitHub check metadata for %s", sha)
			}
			app := object(item["app"])
			appID, _ := app["id"].(float64)
			checks = append(checks, release.Check{
				Name: text(item["name"]), Conclusion: text(item["conclusion"]), SHA: text(item["head_sha"]),
				AppSlug: text(app["slug"]), ID: int64(id), CompletedAt: completed, AppID: int64(appID),
			})
		}
	}
	return checks, nil
}

func (Client) Protection(repository, branch, attestationJSON, rulesetToken string) (release.Protection, error) {
	result := release.Protection{Repository: repository, Branch: branch, Contexts: map[string]bool{}, CheckApps: map[string]int64{}}
	rulesetBytes, err := output(true, rulesetToken, fmt.Sprintf("repos/%s/rulesets?includes_parents=true", repository))
	if err != nil {
		return result, err
	}
	var summaries []map[string]any
	if err := json.Unmarshal(rulesetBytes, &summaries); err != nil {
		return result, fmt.Errorf("invalid GitHub rulesets response")
	}
	classic, classicErr := output(true, rulesetToken, fmt.Sprintf("repos/%s/branches/%s/protection", repository, branch))
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
	if attestationJSON != "" {
		if err := json.Unmarshal([]byte(attestationJSON), &result.Attestation); err != nil {
			return result, fmt.Errorf("canonical ruleset bypass actors are not visible; refusing release merge")
		}
	}
	for _, summary := range summaries {
		id, _ := summary["id"].(float64)
		detail := summary
		if id != 0 {
			body, err := output(true, rulesetToken, fmt.Sprintf("repos/%s/rulesets/%d", repository, int64(id)))
			if err != nil {
				return result, err
			}
			if err := json.Unmarshal(body, &detail); err != nil {
				return result, fmt.Errorf("invalid GitHub ruleset detail")
			}
		}
		result.Rulesets = append(result.Rulesets, detail)
	}
	return result, nil
}

func (Client) MergeReleasePR(repository string, number int, sha string) error {
	payload, _ := json.Marshal(map[string]string{"sha": sha, "merge_method": "squash"})
	command := exec.Command("gh", "api", "--method", "PUT", fmt.Sprintf("repos/%s/pulls/%d/merge", repository, number), "--input", "-")
	command.Stdin = bytes.NewReader(payload)
	response, err := command.Output()
	if err != nil {
		return err
	}
	var result map[string]any
	if err := json.Unmarshal(response, &result); err != nil || result["merged"] != true {
		return fmt.Errorf("GitHub declined release merge: %s", strings.TrimSpace(string(response)))
	}
	return nil
}

func (Client) SetupGitAuthentication() error { return run("gh", "auth", "setup-git") }

func (Client) OpenReleasePR(repository, defaultBranch, releaseBranch string) (string, error) {
	return commandOutput("gh", "pr", "list", "--repo", repository, "--state", "open", "--base", defaultBranch, "--head", releaseBranch, "--json", "number", "--jq", ".[0].number")
}

func (Client) CloseReleasePR(repository, number string) error {
	return run("gh", "pr", "close", number, "--repo", repository, "--comment", "No unreleased fragments remain; candidate retired.")
}

func (Client) EditReleasePR(repository, number, title, body string) error {
	return run("gh", "pr", "edit", number, "--repo", repository, "--title", title, "--body", body, "--add-label", "release")
}

func (Client) CreateReleasePR(repository, defaultBranch, releaseBranch, title, body string) error {
	return run("gh", "pr", "create", "--repo", repository, "--base", defaultBranch, "--head", releaseBranch, "--title", title, "--body", body, "--label", "release")
}

func (Client) PublishedRelease(tag, repository string) (release.PublicationState, error) {
	data, err := commandOutput("gh", "release", "view", tag, "--repo", repository, "--json", "isDraft,isPrerelease,assets")
	if err != nil {
		return release.PublicationState{}, err
	}
	var response struct {
		IsDraft      *bool                      `json:"isDraft"`
		IsPrerelease *bool                      `json:"isPrerelease"`
		Assets       []release.PublicationAsset `json:"assets"`
	}
	if err := json.Unmarshal([]byte(data), &response); err != nil {
		return release.PublicationState{}, fmt.Errorf("invalid GitHub release state: %w", err)
	}
	return release.PublicationState{IsDraft: response.IsDraft, IsPrerelease: response.IsPrerelease, Assets: response.Assets}, nil
}

func commandOutput(name string, args ...string) (string, error) {
	data, err := exec.Command(name, args...).Output()
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}

func run(name string, args ...string) error {
	command := exec.Command(name, args...)
	command.Stdin, command.Stdout, command.Stderr = os.Stdin, os.Stdout, os.Stderr
	return command.Run()
}

func (client Client) ReadRuleset(repository, name string, readOnly bool) (map[string]any, error) {
	data, err := output(readOnly, client.RulesetToken, "repos/"+repository+"/rulesets")
	if err != nil {
		return nil, err
	}
	var summaries []map[string]any
	if err := json.Unmarshal(data, &summaries); err != nil {
		return nil, fmt.Errorf("invalid GitHub ruleset list: %w", err)
	}
	var found map[string]any
	for _, item := range summaries {
		if item["name"] == name {
			if found != nil {
				return nil, fmt.Errorf("multiple %s rulesets exist on %s", name, repository)
			}
			found = item
		}
	}
	if found == nil {
		return nil, nil
	}
	id, ok := found["id"].(float64)
	if !ok {
		return nil, fmt.Errorf("ruleset %s has invalid id", name)
	}
	detail, err := jsonObject(readOnly, client.RulesetToken, fmt.Sprintf("repos/%s/rulesets/%d", repository, int64(id)))
	return detail, err
}

func (Client) WriteRuleset(repository string, current map[string]any, desired map[string]any) error {
	endpoint, method := "repos/"+repository+"/rulesets", "POST"
	if current != nil {
		endpoint += fmt.Sprintf("/%v", current["id"])
		method = "PUT"
	}
	data, err := json.Marshal(desired)
	if err != nil {
		return err
	}
	command := exec.Command("gh", "api", "--method", method, endpoint, "--input", "-")
	command.Stdin, command.Stdout, command.Stderr = bytes.NewReader(data), os.Stdout, os.Stderr
	return command.Run()
}

func jsonObject(readOnly bool, token, endpoint string) (map[string]any, error) {
	data, err := output(readOnly, token, endpoint)
	if err != nil {
		return nil, err
	}
	var result map[string]any
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, fmt.Errorf("invalid GitHub API response for %s", endpoint)
	}
	return result, nil
}

func output(readOnly bool, token, endpoint string) ([]byte, error) {
	command := exec.Command("gh", "api", endpoint)
	command.Env = os.Environ()
	if readOnly && token != "" {
		command.Env = append(command.Env, "GH_TOKEN="+token)
	}
	data, err := command.Output()
	if err != nil {
		return nil, fmt.Errorf("live GitHub API data unavailable for %s", endpoint)
	}
	return data, nil
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
func text(value any) string { result, _ := value.(string); return result }
