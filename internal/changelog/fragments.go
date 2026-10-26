package changelog

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type Fragment struct {
	Type    string `json:"type"`
	Semver  string `json:"semver"`
	Summary string `json:"summary"`
}

type Entry struct {
	Path     string
	Fragment Fragment
	Raw      []byte
}

var rank = map[string]int{"patch": 1, "minor": 2, "major": 3}

func ReadFragments(root string, selected []string) ([]Entry, error) {
	paths, err := fragmentPaths(root, selected)
	if err != nil {
		return nil, err
	}
	entries := make([]Entry, 0, len(paths))
	for _, path := range paths {
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil, fmt.Errorf("%s: invalid fragment: %w", path, err)
		}
		var fragment Fragment
		if err := json.Unmarshal(raw, &fragment); err != nil {
			return nil, fmt.Errorf("%s: invalid fragment: %w", path, err)
		}
		if _, ok := rank[fragment.Semver]; !ok || strings.TrimSpace(fragment.Type) == "" || strings.TrimSpace(fragment.Summary) == "" {
			return nil, fmt.Errorf("%s: invalid type, semver, or summary", path)
		}
		entries = append(entries, Entry{Path: filepath.ToSlash(path), Fragment: fragment, Raw: raw})
	}
	return entries, nil
}

func fragmentPaths(root string, selected []string) ([]string, error) {
	paths := []string{}
	if selected == nil {
		directory := filepath.Join(root, ".changes")
		matches, err := filepath.Glob(filepath.Join(directory, "*.json"))
		if err != nil {
			return nil, err
		}
		paths = matches
	} else {
		for _, name := range selected {
			clean := filepath.Clean(name)
			parts := strings.Split(filepath.ToSlash(clean), "/")
			if filepath.IsAbs(name) || len(parts) != 2 || parts[0] != ".changes" || filepath.Ext(name) != ".json" {
				return nil, fmt.Errorf("invalid fragment path %q; expected .changes/<name>.json", name)
			}
			paths = append(paths, filepath.Join(root, clean))
		}
	}
	sort.Strings(paths)
	return paths, nil
}

func Bump(entries []Entry) string {
	best, score := "", 0
	for _, entry := range entries {
		if rank[entry.Fragment.Semver] > score {
			best, score = entry.Fragment.Semver, rank[entry.Fragment.Semver]
		}
	}
	return best
}

func Render(entries []Entry) string {
	lines := make([]string, len(entries))
	for i, entry := range entries {
		lines[i] = fmt.Sprintf("- **%s**: %s", entry.Fragment.Type, entry.Fragment.Summary)
	}
	return strings.Join(lines, "\n")
}

func FinalizeBytes(old []byte, version, date string, entries []Entry) []byte {
	text := string(old)
	if text == "" {
		text = "# Changelog\n"
	}
	title, rest, found := strings.Cut(text, "\n")
	if !found || !strings.HasPrefix(title, "# ") {
		title, rest = "# Changelog", text
	}
	section := fmt.Sprintf("\n## [%s] - %s\n\n%s\n", strings.TrimPrefix(version, "v"), date, Render(entries))
	result := title + "\n" + section
	if strings.TrimSpace(rest) != "" {
		result += "\n" + strings.TrimLeft(rest, "\n")
	}
	return []byte(result)
}

func ValidateVersion(version string) error {
	if !versionPattern.MatchString(version) {
		return errors.New("finalize requires a valid --version")
	}
	return nil
}
