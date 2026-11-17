package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestChartReopensArchiveAfterAnalysis(t *testing.T) {
	tmp := t.TempDir()
	got, err := runDetailed("synthetic_test_nested.zip", filepath.Join(tmp, "out"), false)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 3 {
		t.Fatalf("channels %d", len(got))
	}
	for i, a := range got {
		pts, err := chartPoints(a, "", "", "voltage")
		if err != nil {
			t.Fatalf("channel %d chart: %v", i, err)
		}
		if len(pts) == 0 {
			t.Fatalf("channel %d no points", i)
		}
	}
	if _, err := os.Stat(filepath.Join(tmp, "out", "PNE_Analysis_Report.html")); err != nil {
		t.Fatal(err)
	}
}
func TestDefaultResultPath(t *testing.T) {
	if defaultOutputFor(`C:\\Foo\\test.zip`) != `D:\Downloads` {
		t.Fatal(defaultOutputFor("x"))
	}
}
