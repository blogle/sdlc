package changelog

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

var versionPattern = regexp.MustCompile(`^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)

func NextVersion(previous, bump string) (string, error) {
	major, minor, patch := 0, 0, 0
	if previous != "" {
		parts := versionPattern.FindStringSubmatch(previous)
		if parts == nil {
			return "", fmt.Errorf("invalid previous release version %q", previous)
		}
		major, _ = strconv.Atoi(parts[1])
		minor, _ = strconv.Atoi(parts[2])
		patch, _ = strconv.Atoi(parts[3])
	}
	switch bump {
	case "major":
		major, minor, patch = major+1, 0, 0
	case "minor":
		minor, patch = minor+1, 0
	default:
		patch++
	}
	return strings.TrimPrefix(fmt.Sprintf("%d.%d.%d", major, minor, patch), "v"), nil
}
