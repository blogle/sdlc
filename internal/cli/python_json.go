package cli

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strconv"
)

// pythonJSON matches the compact, sorted-key JSON bytes emitted by json.dumps
// for SDLC's public identity and plan records.
func pythonJSON(value any) ([]byte, error) {
	encoded, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	var decoded any
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		return nil, err
	}
	var output bytes.Buffer
	if err := writePythonValue(&output, decoded); err != nil {
		return nil, err
	}
	return output.Bytes(), nil
}

func writeCompactJSON(output io.Writer, value any) error {
	encoded, err := pythonJSON(value)
	if err != nil {
		return err
	}
	_, err = output.Write(append(encoded, '\n'))
	return err
}

func writePythonValue(output *bytes.Buffer, value any) error {
	switch value := value.(type) {
	case map[string]any:
		keys := make([]string, 0, len(value))
		for key := range value {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		output.WriteByte('{')
		for index, key := range keys {
			if index != 0 {
				output.WriteString(", ")
			}
			writePythonString(output, key)
			output.WriteString(": ")
			if err := writePythonValue(output, value[key]); err != nil {
				return err
			}
		}
		output.WriteByte('}')
	case []any:
		output.WriteByte('[')
		for index, item := range value {
			if index != 0 {
				output.WriteString(", ")
			}
			if err := writePythonValue(output, item); err != nil {
				return err
			}
		}
		output.WriteByte(']')
	case string:
		writePythonString(output, value)
	case nil:
		output.WriteString("null")
	case bool:
		output.WriteString(strconv.FormatBool(value))
	case float64:
		output.WriteString(strconv.FormatFloat(value, 'g', -1, 64))
	default:
		encoded, err := json.Marshal(value)
		if err != nil {
			return fmt.Errorf("encode JSON value: %w", err)
		}
		output.Write(encoded)
	}
	return nil
}

func writePythonString(output *bytes.Buffer, value string) {
	output.WriteByte('"')
	for _, character := range value {
		switch character {
		case '"', '\\':
			output.WriteByte('\\')
			output.WriteRune(character)
		case '\b':
			output.WriteString(`\b`)
		case '\f':
			output.WriteString(`\f`)
		case '\n':
			output.WriteString(`\n`)
		case '\r':
			output.WriteString(`\r`)
		case '\t':
			output.WriteString(`\t`)
		default:
			if character < 0x20 {
				output.WriteString(fmt.Sprintf("\\u%04x", character))
			} else if character <= 0x7f {
				output.WriteRune(character)
			} else if character <= 0xffff {
				output.WriteString(fmt.Sprintf("\\u%04x", character))
			} else {
				value := character - 0x10000
				high, low := 0xd800+(value>>10), 0xdc00+(value&0x3ff)
				output.WriteString(fmt.Sprintf("\\u%04x\\u%04x", high, low))
			}
		}
	}
	output.WriteByte('"')
}
