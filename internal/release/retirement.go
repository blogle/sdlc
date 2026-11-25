package release

import (
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"unicode/utf8"
)

type RetirementMarker struct {
	Schema        int    `json:"schema"`
	Version       string `json:"version"`
	SourceMainSHA string `json:"source_main_sha"`
	MergedSHA     string `json:"merged_sha"`
	Reason        string `json:"reason"`
}

func ParseRetirementMarker(data []byte, version, sourceSHA, mergedSHA string) error {
	fields, err := uniqueObject(data)
	if err != nil {
		return fmt.Errorf("invalid retirement marker: %w", err)
	}
	required := map[string]bool{"schema": true, "version": true, "source_main_sha": true, "merged_sha": true, "reason": true}
	if len(fields) != len(required) {
		return fmt.Errorf("retirement marker for v%s does not match the exact release identity", version)
	}
	for key := range fields {
		if !required[key] {
			return fmt.Errorf("retirement marker for v%s does not match the exact release identity", version)
		}
	}
	var marker RetirementMarker
	if err := json.Unmarshal(data, &marker); err != nil {
		return fmt.Errorf("invalid retirement marker: %w", err)
	}
	if marker.Schema != 1 || marker.Version != version ||
		marker.SourceMainSHA != sourceSHA || marker.MergedSHA != mergedSHA ||
		!ValidSHA(marker.SourceMainSHA) || !ValidSHA(marker.MergedSHA) ||
		strings.TrimSpace(marker.Reason) == "" || utf8.RuneCountInString(marker.Reason) > 500 {
		return fmt.Errorf("retirement marker for v%s does not match the exact release identity", version)
	}
	return nil
}

func RejectAbandonedMarker(data []byte, found bool, version, sourceSHA, mergedSHA string) error {
	if !found {
		return nil
	}
	if err := ParseRetirementMarker(data, version, sourceSHA, mergedSHA); err != nil {
		return err
	}
	return fmt.Errorf("release v%s is abandoned; refusing replay/publication", version)
}

func uniqueObject(data []byte) (map[string]json.RawMessage, error) {
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	token, err := decoder.Token()
	if err != nil {
		return nil, err
	}
	if token != json.Delim('{') {
		return nil, fmt.Errorf("marker is not an object")
	}
	fields := map[string]json.RawMessage{}
	for decoder.More() {
		keyToken, err := decoder.Token()
		if err != nil {
			return nil, err
		}
		key, ok := keyToken.(string)
		if !ok || fields[key] != nil {
			return nil, fmt.Errorf("duplicate field in retirement marker")
		}
		var value json.RawMessage
		if err := decoder.Decode(&value); err != nil {
			return nil, err
		}
		fields[key] = value
	}
	if _, err := decoder.Token(); err != nil {
		return nil, err
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		return nil, fmt.Errorf("trailing data")
	}
	return fields, nil
}
