package changelog

import (
	"fmt"
	"math/big"
	"regexp"
)

var versionPattern = regexp.MustCompile(`^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)

func ValidVersion(version string) bool { return versionPattern.MatchString(version) }

func HighestVersion(tags []string) string {
	highest := ""
	for _, tag := range tags {
		if versionPattern.MatchString(tag) && greaterVersion(tag, highest) {
			highest = tag
		}
	}
	return highest
}

func greaterVersion(left, right string) bool {
	if right == "" {
		return true
	}
	a, b := versionPattern.FindStringSubmatch(left), versionPattern.FindStringSubmatch(right)
	for i := 1; i <= 3; i++ {
		if a[i] != b[i] {
			leftValue, _ := new(big.Int).SetString(a[i], 10)
			rightValue, _ := new(big.Int).SetString(b[i], 10)
			return leftValue.Cmp(rightValue) > 0
		}
	}
	return false
}

func NextVersion(previous, bump string) (string, error) {
	major, minor, patch := new(big.Int), new(big.Int), new(big.Int)
	if previous != "" {
		parts := versionPattern.FindStringSubmatch(previous)
		if parts == nil {
			return "", fmt.Errorf("invalid previous release version %q", previous)
		}
		major.SetString(parts[1], 10)
		minor.SetString(parts[2], 10)
		patch.SetString(parts[3], 10)
	}
	switch bump {
	case "major":
		major.Add(major, big.NewInt(1))
		minor.SetInt64(0)
		patch.SetInt64(0)
	case "minor":
		minor.Add(minor, big.NewInt(1))
		patch.SetInt64(0)
	default:
		patch.Add(patch, big.NewInt(1))
	}
	return fmt.Sprintf("%s.%s.%s", major, minor, patch), nil
}
