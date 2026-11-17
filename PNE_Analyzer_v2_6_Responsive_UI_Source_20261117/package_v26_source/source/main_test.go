package main

import (
	"testing"
)

func TestDuration(t *testing.T) {
	for _, tt := range []struct {
		s    string
		want float64
	}{{"03:00:00.00", 10800}, {"00:04:18.19", 258.19}, {"1d 00:15:42.30", 87342.30}, {"120.25", 120.25}} {
		got, ok := parseDuration(tt.s)
		if !ok || abs(got-tt.want) > 1e-5 {
			t.Fatalf("duration %s: %v, %v; want %.4f", tt.s, got, ok, tt.want)
		}
	}
}
func abs(n float64) float64 {
	if n < 0 {
		return -n
	}
	return n
}
func TestNestedZipAndMatching(t *testing.T) {
	pairs, cleanup, err := findPairs("synthetic_test_nested.zip")
	if cleanup != nil {
		defer cleanup()
	}
	if err != nil {
		t.Fatal(err)
	}
	if len(pairs) != 3 {
		t.Fatalf("found %d pairs, want 3", len(pairs))
	}
	for _, p := range pairs {
		if p.id == "" {
			t.Fatal("missing channel id")
		}
		rows, err := readStepCSV(p.csv)
		if err != nil {
			t.Fatal(err)
		}
		segs, err := readSegments(p.cyc)
		if err != nil {
			t.Fatal(err)
		}
		a := analyzed{segments: segs, csvRows: rows}
		matchSteps(&a)
		a.countStatus()
		if a.matched != 4 || a.review != 0 || a.unmatched != 0 {
			t.Fatalf("%s: %+v", p.id, a)
		}
		if rows[1].typ != "방전" {
			t.Fatalf("CP949/UTF8 decode: %q", rows[1].typ)
		}
	}
}
