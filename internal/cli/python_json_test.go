package cli

import "testing"

func TestPythonJSONMatchesSortedPythonStyle(t *testing.T) {
	got, err := pythonJSON(map[string]any{"z": []any{"é", "<tag>"}, "a": true})
	if err != nil {
		t.Fatal(err)
	}
	want := `{"a": true, "z": ["\u00e9", "<tag>"]}`
	if string(got) != want {
		t.Fatalf("JSON bytes differ: got %s, want %s", got, want)
	}
}
