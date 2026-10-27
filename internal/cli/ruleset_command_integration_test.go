package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRulesetHandlersPlanCheckAndApplyCanonicalPolicies(t *testing.T) {
	t.Run("plan is read-only and includes the automated-merge policy", func(t *testing.T) {
		fixtures, logPath := makeRulesetFixtures(t, false)
		command := New()
		var output bytes.Buffer
		command.SetOut(&output)
		command.SetArgs([]string{"ruleset", "plan", "--repo", "owner/repo"})
		if err := command.Execute(); err != nil {
			t.Fatal(err)
		}
		if !bytes.Contains(output.Bytes(), []byte(`"automated_merges"`)) {
			t.Fatal("canonical plan omitted automated merge restriction")
		}
		calls := readIntegrationFile(t, logPath)
		if bytes.Contains(calls, []byte("--method POST")) || bytes.Contains(calls, []byte("--method PUT")) {
			t.Fatalf("ruleset plan mutated GitHub: %s", calls)
		}
		if _, err := os.Stat(filepath.Join(fixtures, "default.applied")); !os.IsNotExist(err) {
			t.Fatal("plan created a ruleset")
		}
	})

	t.Run("check accepts only currently attested redacted bypass actors", func(t *testing.T) {
		fixtures, logPath := makeRulesetFixtures(t, true)
		writeRepoFile(t, fixtures, "default.applied", "")
		writeRepoFile(t, fixtures, "automated.applied", "")
		command := New()
		var output bytes.Buffer
		command.SetOut(&output)
		command.SetArgs([]string{"ruleset", "check", "--repo", "owner/repo"})
		if err := command.Execute(); err != nil {
			t.Fatal(err)
		}
		if got := strings.Count(output.String(), "canonical ruleset matches for owner/repo"); got != 2 {
			t.Fatalf("both policy layers were not checked: %q", output.String())
		}
		calls := readIntegrationFile(t, logPath)
		if bytes.Contains(calls, []byte("--method POST")) || bytes.Contains(calls, []byte("--method PUT")) {
			t.Fatalf("ruleset check mutated GitHub: %s", calls)
		}

		command = New()
		command.SetArgs([]string{"ruleset", "check", "--repo", "owner/repo"})
		t.Setenv("SDLC_RULESET_ATTESTATION", `{"schema":1,"repository":"owner/repo","ruleset_id":10,"updated_at":"2020-01-01T00:00:00Z","bypass_actors":[],"automated_merges":{"schema":1,"repository":"owner/repo","ruleset_id":11,"updated_at":"2026-10-26T00:00:00Z","bypass_actors":[]}}`)
		if err := command.Execute(); err == nil {
			t.Fatal("stale bypass attestation authorized redacted policy")
		}
	})

	t.Run("apply creates both rulesets, verifies them and is idempotent", func(t *testing.T) {
		_, logPath := makeRulesetFixtures(t, false)
		command := New()
		command.SetArgs([]string{"ruleset", "apply", "--repo", "owner/repo"})
		if err := command.Execute(); err != nil {
			t.Fatal(err)
		}
		calls := readIntegrationFile(t, logPath)
		if got := bytes.Count(calls, []byte("--method POST")); got != 2 {
			t.Fatalf("apply should create both policy layers, got %d writes", got)
		}
		if err := command.Execute(); err != nil {
			t.Fatal(err)
		}
		calls = readIntegrationFile(t, logPath)
		if got := bytes.Count(calls, []byte("--method POST")); got != 2 {
			t.Fatalf("idempotent apply repeated mutations, got %d writes", got)
		}
	})
}

func readIntegrationFile(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func makeRulesetFixtures(t *testing.T, redacted bool) (string, string) {
	t.Helper()
	contract, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	directory := t.TempDir()
	var canonical, automated map[string]any
	readJSONTestFixture(t, filepath.Join(contract, "rulesets", "default-branch.json"), &canonical)
	readJSONTestFixture(t, filepath.Join(contract, "rulesets", "main-updates.json"), &automated)
	automatedActors := automated["bypass_actors"]
	canonical["id"], canonical["updated_at"], canonical["source"] = float64(10), "2026-10-26T00:00:00Z", "owner/repo"
	automated["id"], automated["updated_at"], automated["source"] = float64(11), "2026-10-26T00:00:00Z", "owner/repo"
	if redacted {
		canonical["bypass_actors"], automated["bypass_actors"] = nil, nil
	}
	writeJSONFile(t, directory, "canonical.json", canonical)
	writeJSONFile(t, directory, "automated.json", automated)
	writeRepoFile(t, directory, "list-empty.json", `[]`)
	writeRepoFile(t, directory, "list-default.json", `[{"id":10,"name":"SDLC default branch"}]`)
	writeRepoFile(t, directory, "list-both.json", `[{"id":10,"name":"SDLC default branch"},{"id":11,"name":"SDLC automated merges"}]`)
	writeRepoFile(t, directory, "gh", fakeRulesetScript)
	if err := os.Chmod(filepath.Join(directory, "gh"), 0o755); err != nil {
		t.Fatal(err)
	}
	logPath := filepath.Join(directory, "calls.log")
	t.Setenv("PATH", directory+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("GH_FIXTURES", directory)
	t.Setenv("GH_LOG", logPath)
	t.Setenv("SDLC_SOURCE_PATH", contract)
	attestation, err := json.Marshal(map[string]any{
		"schema": 1, "repository": "owner/repo", "ruleset_id": 10, "updated_at": "2026-10-26T00:00:00Z", "bypass_actors": []any{},
		"automated_merges": map[string]any{"schema": 1, "repository": "owner/repo", "ruleset_id": 11, "updated_at": "2026-10-26T00:00:00Z", "bypass_actors": automatedActors},
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("SDLC_RULESET_ATTESTATION", string(attestation))
	return directory, logPath
}

const fakeRulesetScript = `#!/bin/sh
set -eu
args="$*"
printf '%s\n' "$args" >> "$GH_LOG"
case "$args" in
  *"auth setup-git"*) exit 0 ;;
  *"pr list"*) printf '%s' "$GH_PR_NUMBER" ;;
  *"pr close"*|*"pr create"*|*"pr edit"*) printf '%s\n' "$args" > "$GH_PR_MUTATION" ;;
  *"--method POST"*"/rulesets"*)
    payload=$(cat)
    case "$payload" in
      *"SDLC default branch"*) touch "$GH_FIXTURES/default.applied" ;;
      *) touch "$GH_FIXTURES/automated.applied" ;;
    esac
    printf '%s' '{}'
    ;;
  *"/rulesets")
    if [ -f "$GH_FIXTURES/automated.applied" ]; then cat "$GH_FIXTURES/list-both.json"
    elif [ -f "$GH_FIXTURES/default.applied" ]; then cat "$GH_FIXTURES/list-default.json"
    else cat "$GH_FIXTURES/list-empty.json"; fi
    ;;
  *"/rulesets/10"*) cat "$GH_FIXTURES/canonical.json" ;;
  *"/rulesets/11"*) cat "$GH_FIXTURES/automated.json" ;;
  *) printf 'unexpected fake ruleset request: %s\n' "$args" >&2; exit 2 ;;
esac
`
