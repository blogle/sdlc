package cli

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	gitrepo "github.com/blogle/sdlc/internal/git"
)

type releaseRepo struct {
	root, source, candidate, contract string
	manifest                          []byte
}

func makeReleaseRepo(t *testing.T) releaseRepo {
	t.Helper()
	root := t.TempDir()
	original, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	contract, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(root); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(original) })
	repoGit(t, root, "init", "-q", "-b", "main")
	repoGit(t, root, "config", "user.name", "Go lifecycle test")
	repoGit(t, root, "config", "user.email", "go-lifecycle@example.invalid")
	writeRepoFile(t, root, "CHANGELOG.md", "# Changelog\n")
	writeRepoFile(t, root, ".changes/fix.json", `{"type":"fix","semver":"patch","summary":"release gate fixture"}`)
	repoGit(t, root, "add", ".")
	repoGit(t, root, "commit", "-qm", "source")
	source := strings.TrimSpace(repoGit(t, root, "rev-parse", "HEAD"))
	manifest, err := gitrepo.Open(root).Snapshot(source, "")
	if err != nil || manifest == nil {
		t.Fatalf("snapshot: %v", err)
	}
	repoGit(t, root, "add", ".")
	repoGit(t, root, "commit", "-qm", "release candidate")
	candidate := strings.TrimSpace(repoGit(t, root, "rev-parse", "HEAD"))
	manifestBytes, err := os.ReadFile(filepath.Join(root, ".sdlc", "release.json"))
	if err != nil {
		t.Fatal(err)
	}
	return releaseRepo{root: root, source: source, candidate: candidate, contract: contract, manifest: manifestBytes}
}

func makeGitHubFixture(t *testing.T, repo releaseRepo) (string, string) {
	t.Helper()
	directory := t.TempDir()
	writeRepoFile(t, directory, "manifest.json", `{"content":"`+base64.StdEncoding.EncodeToString(repo.manifest)+`"}`)
	writeRepoFile(t, directory, "installation.json", `{"repositories":[{"full_name":"owner/repo"}]}`)
	writeRepoFile(t, directory, "repository.json", `{"default_branch":"main"}`)
	pr := map[string]any{
		"head": map[string]any{"sha": repo.candidate, "ref": "sdlc/release-next", "repo": map[string]any{"full_name": "owner/repo"}, "user": map[string]any{"login": "release-app[bot]", "type": "Bot"}},
		"user": map[string]any{"login": "release-app[bot]", "type": "Bot"},
	}
	writeJSONFile(t, directory, "pr.json", pr)
	writeJSONFile(t, directory, "pr-current.json", pr)
	writeJSONFile(t, directory, "main.json", map[string]any{"sha": repo.source})
	writeJSONFile(t, directory, "main-stale.json", map[string]any{"sha": repo.source})
	writeJSONFile(t, directory, "head-commit.json", map[string]any{"parents": []any{map[string]any{"sha": repo.source}}})

	contract := repo.contract
	var canonical, automated map[string]any
	readJSONTestFixture(t, filepath.Join(contract, "rulesets", "default-branch.json"), &canonical)
	readJSONTestFixture(t, filepath.Join(contract, "rulesets", "main-updates.json"), &automated)
	canonical["id"], canonical["updated_at"], canonical["source"] = float64(10), "2026-10-26T00:00:00Z", "owner/repo"
	automated["id"], automated["updated_at"], automated["source"] = float64(11), "2026-10-26T00:00:00Z", "owner/repo"
	writeJSONFile(t, directory, "rulesets.json", []any{
		map[string]any{"id": 10, "name": "SDLC default branch"},
		map[string]any{"id": 11, "name": "SDLC automated merges"},
	})
	writeJSONFile(t, directory, "ruleset-10.json", canonical)
	writeJSONFile(t, directory, "ruleset-11.json", automated)
	writeRepoFile(t, directory, "classic.json", `{"required_status_checks":{"contexts":[],"checks":[],"strict":false}}`)
	checks := []any{
		checkRun(1, "sdlc / check", repo.candidate, "success", 15368, "github-actions"),
		checkRun(2, "sdlc / policy", repo.candidate, "success", 15368, "github-actions"),
		checkRun(3, "Mergify Merge Protections", repo.candidate, "success", 10562, "mergify"),
		checkRun(4, "sdlc / release-gate", repo.candidate, "success", 15368, "release-app"),
	}
	writeJSONFile(t, directory, "checks.json", []any{map[string]any{"check_runs": checks}})
	actors := automated["bypass_actors"]
	attestation, err := json.Marshal(map[string]any{
		"schema": 1, "repository": "owner/repo", "ruleset_id": 10,
		"updated_at": "2026-10-26T00:00:00Z", "bypass_actors": []any{},
		"automated_merges": map[string]any{"schema": 1, "repository": "owner/repo", "ruleset_id": 11, "updated_at": "2026-10-26T00:00:00Z", "bypass_actors": actors},
	})
	if err != nil {
		t.Fatal(err)
	}
	writeRepoFile(t, directory, "gh", fakeGitHubScript)
	if err := os.Chmod(filepath.Join(directory, "gh"), 0o755); err != nil {
		t.Fatal(err)
	}
	logPath := filepath.Join(directory, "calls.log")
	t.Setenv("PATH", directory+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("GH_FIXTURES", directory)
	t.Setenv("GH_LOG", logPath)
	t.Setenv("GH_MERGE_CALLED", filepath.Join(directory, "merge.called"))
	t.Setenv("SDLC_SOURCE_PATH", contract)
	t.Setenv("SDLC_RULESET_ATTESTATION", string(attestation))
	return directory, logPath
}

func TestGoReleaseGateAndMergeHandlersEnforceGitHubEvidenceBeforeMutation(t *testing.T) {
	t.Run("valid gate verifies candidate without mutating GitHub", func(t *testing.T) {
		repo := makeReleaseRepo(t)
		makeGitHubFixture(t, repo)
		command := New()
		command.SetArgs([]string{"release", "gate", "--repo", "owner/repo", "--pr", "1", "--release-bot", "release-app[bot]", "--app-slug", "release-app"})
		if err := command.Execute(); err != nil {
			t.Fatal(err)
		}
		if _, err := os.Stat(os.Getenv("GH_MERGE_CALLED")); !os.IsNotExist(err) {
			t.Fatal("gate handler invoked a merge mutation")
		}
	})

	t.Run("historical gate recovery rechecks the merged tree and trusted checks", func(t *testing.T) {
		repo := makeReleaseRepo(t)
		makeGitHubFixture(t, repo)
		command := New()
		command.SetArgs([]string{"release", "gate", "--repo", "owner/repo", "--pr", "1", "--release-bot", "release-app[bot]", "--app-slug", "release-app", "--historical-merged-sha", repo.candidate})
		if err := command.Execute(); err != nil {
			t.Fatal(err)
		}
		if _, err := os.Stat(os.Getenv("GH_MERGE_CALLED")); !os.IsNotExist(err) {
			t.Fatal("historical validation tried to merge a closed PR")
		}
	})

	t.Run("gate rejects a check from the wrong publisher before any write", func(t *testing.T) {
		repo := makeReleaseRepo(t)
		directory, logPath := makeGitHubFixture(t, repo)
		mutateGitHubFixture(t, directory, "wrong-check-app")
		command := New()
		command.SetArgs([]string{"release", "gate", "--repo", "owner/repo", "--pr", "1", "--release-bot", "release-app[bot]", "--app-slug", "release-app"})
		if err := command.Execute(); err == nil {
			t.Fatal("forged required-check publisher was accepted")
		}
		calls, err := os.ReadFile(logPath)
		if err != nil {
			t.Fatal(err)
		}
		if bytes.Contains(calls, []byte("--method PUT")) {
			t.Fatal("gate failure made a GitHub write")
		}
	})

	t.Run("valid merge uses exact expected SHA and squash", func(t *testing.T) {
		repo := makeReleaseRepo(t)
		_, logPath := makeGitHubFixture(t, repo)
		command := New()
		command.SetArgs([]string{"release", "merge", "--repo", "owner/repo", "--pr", "1", "--release-bot", "release-app[bot]", "--app-slug", "release-app", "--expected-head", repo.candidate})
		if err := command.Execute(); err != nil {
			t.Fatal(err)
		}
		if _, err := os.Stat(os.Getenv("GH_MERGE_CALLED")); err != nil {
			t.Fatal("authorized merge did not reach GitHub merge endpoint")
		}
		calls, err := os.ReadFile(logPath)
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Contains(calls, []byte("--method PUT repos/owner/repo/pulls/1/merge")) {
			t.Fatalf("merge method not recorded: %s", calls)
		}
		payload, err := os.ReadFile(os.Getenv("GH_MERGE_CALLED"))
		if err != nil {
			t.Fatal(err)
		}
		var merge map[string]string
		if err := json.Unmarshal(payload, &merge); err != nil {
			t.Fatal(err)
		}
		if merge["sha"] != repo.candidate || merge["merge_method"] != "squash" {
			t.Fatalf("wrong merge precondition: %#v", merge)
		}
	})

	for _, scenario := range []string{"wrong-check-app", "latest-failure", "human-author", "stale-head", "stale-main", "redacted-stale-attestation"} {
		t.Run(scenario+" fails before GitHub mutation", func(t *testing.T) {
			repo := makeReleaseRepo(t)
			directory, _ := makeGitHubFixture(t, repo)
			mutateGitHubFixture(t, directory, scenario)
			command := New()
			command.SetArgs([]string{"release", "merge", "--repo", "owner/repo", "--pr", "1", "--release-bot", "release-app[bot]", "--app-slug", "release-app"})
			if err := command.Execute(); err == nil {
				t.Fatal("invalid authorization evidence unexpectedly passed")
			}
			if _, err := os.Stat(os.Getenv("GH_MERGE_CALLED")); !os.IsNotExist(err) {
				t.Fatal("failed authorization reached GitHub merge endpoint")
			}
		})
	}
}

func mutateGitHubFixture(t *testing.T, directory, scenario string) {
	t.Helper()
	switch scenario {
	case "wrong-check-app":
		var pages []map[string]any
		readJSONTestFixture(t, filepath.Join(directory, "checks.json"), &pages)
		rows := pages[0]["check_runs"].([]any)
		rows[1].(map[string]any)["app"].(map[string]any)["id"] = 999
		writeJSONFile(t, directory, "checks.json", pages)
	case "latest-failure":
		var pages []map[string]any
		readJSONTestFixture(t, filepath.Join(directory, "checks.json"), &pages)
		rows := pages[0]["check_runs"].([]any)
		rows = append(rows, checkRun(9, "sdlc / policy", "", "failure", 15368, "github-actions"))
		var pr map[string]any
		readJSONTestFixture(t, filepath.Join(directory, "pr.json"), &pr)
		rows[len(rows)-1].(map[string]any)["head_sha"] = pr["head"].(map[string]any)["sha"]
		pages[0]["check_runs"] = rows
		writeJSONFile(t, directory, "checks.json", pages)
	case "human-author":
		var pr map[string]any
		readJSONTestFixture(t, filepath.Join(directory, "pr.json"), &pr)
		pr["user"].(map[string]any)["type"] = "User"
		writeJSONFile(t, directory, "pr.json", pr)
		writeJSONFile(t, directory, "pr-current.json", pr)
	case "stale-head":
		var pr map[string]any
		readJSONTestFixture(t, filepath.Join(directory, "pr-current.json"), &pr)
		pr["head"].(map[string]any)["sha"] = strings.Repeat("e", 40)
		writeJSONFile(t, directory, "pr-current.json", pr)
	case "stale-main":
		writeJSONFile(t, directory, "main-stale.json", map[string]any{"sha": strings.Repeat("e", 40)})
	case "redacted-stale-attestation":
		var canonical map[string]any
		readJSONTestFixture(t, filepath.Join(directory, "ruleset-10.json"), &canonical)
		canonical["bypass_actors"] = nil
		writeJSONFile(t, directory, "ruleset-10.json", canonical)
		t.Setenv("SDLC_RULESET_ATTESTATION", `{"schema":1,"repository":"owner/repo","ruleset_id":10,"updated_at":"2020-01-01T00:00:00Z","bypass_actors":[],"automated_merges":{"schema":1,"repository":"owner/repo","ruleset_id":11,"updated_at":"2026-10-26T00:00:00Z","bypass_actors":[]}}`)
	}
}

func checkRun(id int, name, sha, conclusion string, appID int, appSlug string) map[string]any {
	return map[string]any{"id": id, "name": name, "head_sha": sha, "status": "completed", "conclusion": conclusion, "completed_at": "2026-10-26T00:00:00Z", "app": map[string]any{"id": appID, "slug": appSlug}}
}

func readJSONTestFixture(t *testing.T, path string, target any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatal(err)
	}
}

func writeJSONFile(t *testing.T, directory, name string, value any) {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	writeRepoFile(t, directory, name, string(data))
}

func writeRepoFile(t *testing.T, directory, name, value string) {
	t.Helper()
	path := filepath.Join(directory, name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(value), 0o644); err != nil {
		t.Fatal(err)
	}
}

func repoGit(t *testing.T, directory string, args ...string) string {
	t.Helper()
	command := exec.Command("git", args...)
	command.Dir = directory
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, output)
	}
	return string(output)
}

const fakeGitHubScript = `#!/bin/sh
set -eu
args="$*"
printf '%s\n' "$args" >> "$GH_LOG"
case "$args" in
  *"--method PUT"*"pulls/1/merge"*) cat > "$GH_MERGE_CALLED"; printf '%s' '{"merged":true}' ;;
  *"installation/repositories?per_page=100"*) cat "$GH_FIXTURES/installation.json" ;;
  *"contents/.sdlc/release.json?ref="*) cat "$GH_FIXTURES/manifest.json" ;;
  *"pulls/1"*)
    if [ -f "$GH_FIXTURES/pr.count" ]; then cat "$GH_FIXTURES/pr-current.json"; else touch "$GH_FIXTURES/pr.count"; cat "$GH_FIXTURES/pr.json"; fi ;;
  *"rulesets?includes_parents=true"*) cat "$GH_FIXTURES/rulesets.json" ;;
  *"rulesets/10"*) cat "$GH_FIXTURES/ruleset-10.json" ;;
  *"rulesets/11"*) cat "$GH_FIXTURES/ruleset-11.json" ;;
  *"branches/main/protection"*) cat "$GH_FIXTURES/classic.json" ;;
  *"check-runs?per_page=100"*) cat "$GH_FIXTURES/checks.json" ;;
  *"commits/main"*)
    if [ -f "$GH_FIXTURES/main.count" ]; then cat "$GH_FIXTURES/main-stale.json"; else touch "$GH_FIXTURES/main.count"; cat "$GH_FIXTURES/main.json"; fi ;;
  *"commits/"*) cat "$GH_FIXTURES/head-commit.json" ;;
  *"repos/owner/repo") cat "$GH_FIXTURES/repository.json" ;;
  *) printf 'unexpected fake GitHub request: %s\n' "$args" >&2; exit 2 ;;
esac
`
