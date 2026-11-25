package ruleset

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"
)

type Policies struct {
	DefaultBranch   map[string]any
	AutomatedMerges map[string]any
}

func ReadPolicies(contractRoot string) (Policies, error) {
	defaultPolicy, err := readPolicy(filepath.Join(contractRoot, "rulesets", "default-branch.json"))
	if err != nil {
		return Policies{}, err
	}
	automatedPolicy, err := readPolicy(filepath.Join(contractRoot, "rulesets", "main-updates.json"))
	if err != nil {
		return Policies{}, err
	}
	return Policies{DefaultBranch: defaultPolicy, AutomatedMerges: automatedPolicy}, nil
}

func readPolicy(path string) (map[string]any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var result map[string]any
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, fmt.Errorf("%s: invalid policy JSON: %w", path, err)
	}
	return result, nil
}

func SameTimestamp(left, right any) bool {
	a, okA := left.(string)
	b, okB := right.(string)
	if !okA || !okB || a == "" || b == "" {
		return false
	}
	if a == b {
		return true
	}
	first, errA := time.Parse(time.RFC3339Nano, a)
	second, errB := time.Parse(time.RFC3339Nano, b)
	return errA == nil && errB == nil && first.Equal(second)
}

// Matches compares only policy fields. GitHub's API adds metadata and may
// reorder arrays, neither of which changes this pinned ruleset contract.
func Matches(actual, desired map[string]any, allowRedactedBypass bool) bool {
	if actual == nil {
		return false
	}
	if allowRedactedBypass && actual["bypass_actors"] == nil {
		copy := clone(actual)
		copy["bypass_actors"] = desired["bypass_actors"]
		actual = copy
	}
	for key, expected := range desired {
		if key == "rules" {
			continue
		}
		if !equal(project(actual[key], expected), project(expected, expected)) {
			return false
		}
	}
	wantRules, ok := desired["rules"].([]any)
	if !ok {
		return false
	}
	actualRules, ok := actual["rules"].([]any)
	if !ok || len(actualRules) != len(wantRules) {
		return false
	}
	for _, expected := range wantRules {
		want, ok := expected.(map[string]any)
		if !ok {
			return false
		}
		count := 0
		for _, item := range actualRules {
			got, ok := item.(map[string]any)
			if ok && got["type"] == want["type"] && equal(project(got, want), project(want, want)) {
				count++
			}
		}
		if count != 1 {
			return false
		}
	}
	return true
}

func project(value, shape any) any {
	switch expected := shape.(type) {
	case map[string]any:
		actual, ok := value.(map[string]any)
		if !ok {
			return nil
		}
		result := make(map[string]any, len(expected))
		for key, child := range expected {
			result[key] = project(actual[key], child)
		}
		return result
	case []any:
		actual, ok := value.([]any)
		if !ok {
			return nil
		}
		result := make([]any, len(actual))
		for i, item := range actual {
			if len(expected) > 0 {
				result[i] = project(item, expected[0])
			} else {
				result[i] = item
			}
		}
		sort.Slice(result, func(i, j int) bool { return canonical(result[i]) < canonical(result[j]) })
		return result
	default:
		return value
	}
}

func equal(left, right any) bool { return canonical(left) == canonical(right) }
func canonical(value any) string { bytes, _ := json.Marshal(value); return string(bytes) }
func clone(value map[string]any) map[string]any {
	result := make(map[string]any, len(value))
	for key, item := range value {
		result[key] = item
	}
	return result
}
